#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"
./workspace console-path >/dev/null 2>&1 || ./workspace setup
printf 'Nom du projet (existant ou nouveau) : '
read -r name
if [ ! -d "projects/$name" ]; then
  ./workspace new "$name"
fi
exec ./workspace open "projects/$name"
