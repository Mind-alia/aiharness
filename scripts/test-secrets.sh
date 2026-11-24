#!/bin/sh
[ ! -f "${0%/*}/python.sh" ] || . "${0%/*}/python.sh"
umask 077
# Generate local test credentials without printing their contents.
set -eu
root=$(CDPATH= cd -- "${0%/*}/.." && pwd)
exec "${WORKSPACE_PYTHON:-python3}" - "$root" "$@" <<'PY'
import os
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
args = sys.argv[2:]
force = '--force' in args
names = [arg for arg in args if arg != '--force'] or ['db_password', 'api_key', 'jwt_secret']
if any(not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', name) for name in names):
    sys.exit('Nom de secret de test invalide')
directory = root / '.workspace/secrets-test'
for component in (directory, *directory.parents):
    if component.is_symlink():
        sys.exit('Chemin symbolique interdit')
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
paths = [directory / name for name in names]
if any(path.is_symlink() or (path.exists() and (not force or not path.is_file())) for path in paths):
    sys.exit('Un fichier existe déjà ; utiliser --force pour le remplacer')
for path in paths:
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
    flags |= os.O_TRUNC if force else os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(os.urandom(32).hex() + '\n')
PY
