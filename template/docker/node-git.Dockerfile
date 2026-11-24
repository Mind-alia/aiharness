# Image complète de la porte d'intégration pour les projets Node (porte de la console) : Node 24, python3, git
# et Chromium de Playwright, pour que la porte passe dans le conteneur de la tâche.
# Construction : docker build -t workspace-node-git:24-bookworm -f template/docker/node-git.Dockerfile template/docker
# --only-shell : en mode sans tête, Playwright n'utilise que le Chromium « headless shell » ; l'image est plus légère.
# Usage : choisie automatiquement pour le profil node quand elle existe localement (template/scripts/lib/sandbox.py).
FROM node:24-bookworm-slim
ARG PLAYWRIGHT_VERSION=1.64.0
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 git ca-certificates procps \
    && npx -y playwright@${PLAYWRIGHT_VERSION} install --with-deps --only-shell chromium \
    && chmod -R a+rX /ms-playwright \
    && rm -rf /var/lib/apt/lists/* /root/.npm /tmp/*
