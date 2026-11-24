"""Profil de permissions de Codex pour `codex.sh` (Python 3.14, stdlib seule).

Le bac à sable `workspace-write` de Codex lit tout le disque de l'hôte. Ce profil, passé par `-c`, ne laisse lire que
le minimum système (`:minimal`), le dossier du projet (écriture pour `write`, lecture pour `review`) et le dossier
temporaire. Le reste (`~/.ssh`, `~/.claude`, les autres dossiers) rend `Operation not permitted`.

Usage : python3 codex_profile.py write|review   (un argument par ligne ; code 64 si l'usage est invalide)
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

PROFILE = 'atelier'
PROJECT_ACCESS = {'write': 'write', 'review': 'read'}
USAGE = 'Usage : codex_profile.py write|review'


def config_args(mode: str) -> list[str]:
    """Arguments `-c` de `codex exec` : profil par défaut et table des accès au système de fichiers."""
    if mode not in PROJECT_ACCESS:
        raise ValueError("mode de profil Codex inconnu : '%s' (write ou review)" % mode)
    table = '{":minimal"="read",":project_roots"="%s",":tmpdir"="write"}' % PROJECT_ACCESS[mode]
    return [
        '-c',
        'default_permissions="%s"' % PROFILE,
        '-c',
        'permissions.%s.filesystem=%s' % (PROFILE, table),
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in PROJECT_ACCESS:
        print(USAGE, file=sys.stderr)
        return 64
    print('\n'.join(config_args(argv[0])))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
