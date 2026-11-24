# Image de vérification pour les projets C de l'école 42 (profil c du kit).
# But : compiler, lancer les tests du Makefile (valgrind inclus) et vérifier la norme avec norminette 3.3.60.
# Construction : docker build -t workspace-c-norm:bookworm -f template/docker/c-norm.Dockerfile template/docker
# Usage : WORKSPACE_IMAGE_C=workspace-c-norm:bookworm bash scripts/delegate.sh <fiche>
FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc make valgrind libc6-dev python3 python3-pip git ca-certificates \
    && pip3 install --no-cache-dir --break-system-packages norminette==3.3.60 \
    && rm -rf /var/lib/apt/lists/*
