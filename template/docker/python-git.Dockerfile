# Verification image for the kit's Python projects (and the kit itself): Python 3.14 on Debian trixie, with Git.
# The interpreter is copied from the official image instead of using that image as the base, so none of its environment
# variables reach the sandbox (older official images carried GPG_KEY, a public fingerprint the attack bench flags by name).
# Both bases are pinned by tag and digest (26 October 2026); a new version is a deliberate change, followed by the attack bench.
# Build: docker build -t workspace-python-git:3.14 -f template/docker/python-git.Dockerfile template/docker
# Use:   WORKSPACE_IMAGE_PYTHON=workspace-python-git:3.14 bash scripts/delegate.sh <task sheet>
FROM python:3.14.9-slim-trixie@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8 AS python

# ruff, pinned like tests/test_code_quality.py and the Makefile, so the quality suite runs inside the sandbox (T98):
# an executor's format or lint error fails in its container, not later on the host. Only its static binary is kept.
FROM python AS ruff
RUN pip install --no-cache-dir --root-user-action=ignore ruff==0.17.2 && ruff --version

# pyright, pinned like the Makefile and tests/test_code_quality.py, so the type budget is measured inside the sandbox too
# (T116): on 3 November 2026 gt2-run-states was stopped on the host for 195 > 193 errors its executor could not see.
# The npm tarball has no dependency; it is checked by SHA-256 (the same file as the registry's sha512 integrity
# DwzfZFTl…QvqKQ==) and run by Debian's Node, without npm.
FROM python AS pyright
RUN python3 -c "import hashlib, urllib.request; data = urllib.request.urlopen('https://registry.npmjs.org/pyright/-/pyright-1.1.402.tgz', timeout=300).read(); assert hashlib.sha256(data).hexdigest() == 'c0f45ed549c216f1aea353f8888a9a38744790be195a73541188853f5159453d', 'pyright tarball checksum'; open('/tmp/pyright.tgz', 'wb').write(data)" \
    && mkdir /opt/pyright && tar -xzf /tmp/pyright.tgz -C /opt/pyright --strip-components=1 --no-same-owner && rm /tmp/pyright.tgz

FROM debian:trixie-slim@sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates tzdata netbase \
       libssl3t64 libsqlite3-0 libffi8 zlib1g libbz2-1.0 liblzma5 libzstd1 libreadline8t64 libncursesw6 libexpat1 libuuid1 \
       libgdbm6t64 libgdbm-compat4t64 libdb5.3t64 libmpdec4 nodejs \
    && rm -rf /var/lib/apt/lists/*
COPY --from=python /usr/local /usr/local
COPY --from=ruff /usr/local/bin/ruff /usr/local/bin/ruff
COPY --from=pyright /opt/pyright /usr/local/lib/pyright
# No pip inside the sandbox: the kit and the tasks use the standard library only.
RUN rm -rf /usr/local/bin/pip* /usr/local/lib/python3.14/site-packages/pip* \
    && printf '#!/bin/sh\nexec node /usr/local/lib/pyright/index.js "$@"\n' > /usr/local/bin/pyright && chmod 755 /usr/local/bin/pyright \
    && ldconfig && python3.14 --version && ruff --version && pyright --version | grep -qx 'pyright 1.1.402' \
    && python3 -c "import ssl, sqlite3, ctypes, zlib, bz2, lzma, readline, uuid, _decimal, pyexpat, _hashlib, dbm.gnu, compression.zstd, socket; socket.getservbyname('http')"
