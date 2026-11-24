"""Contrôle d'une compétence Claude Code candidate avant tout essai (T13c).

Le module ne lit, n'installe et ne publie rien d'autre : il imprime un verdict. Les motifs dangereux sont cherchés
d'abord ; une suite d'évaluation n'est lancée que si aucun motif n'a été trouvé.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

MAX_BYTES = 1024 * 1024
MAX_PRINTED = 50
EXCERPT_CHARS = 80
DEFAULT_TIMEOUT = 1800
USAGE = 'usage : skill-check.sh <dossier> [--trust]'
EVAL_FILES = ('case.yaml', 'prompt.md')

WRITE_PATTERNS = (
    'rm -rf',
    'sudo',
    'chmod',
    'chown',
    '~/',
    '$HOME',
    '/etc/',
    '/usr/',
    '/root',
    '../',
    '.claude/',
    '.workspace/',
    '.git/',
    'project.profile',
    'git push',
    'crontab',
    '--dangerously-skip-permissions',
)
NETWORK_PATTERNS = (
    'curl',
    'wget',
    'http://',
    'https://',
    'WebFetch',
    'WebSearch',
    'ssh',
    'scp',
    'netcat',
    ' nc ',
    'requests.',
    'urllib',
    'fetch(',
    'socket',
)
SENSITIVE_PATTERNS = (
    'secrets.env',
    '.env',
    'id_rsa',
    'id_ed25519',
    '.ssh',
    '.aws',
    '.netrc',
    'api key',
    'api_key',
    'apikey',
    'token',
    'password',
    'mot de passe',
    'credential',
    'keychain',
    'secret',
)
PATTERN_GROUPS = (('écriture', WRITE_PATTERNS), ('réseau', NETWORK_PATTERNS), ('secret', SENSITIVE_PATTERNS))
CATEGORY_ORDER = ('structure',) + tuple(name for name, _ in PATTERN_GROUPS)


def usage_error(message):
    print(f'{message}\n{USAGE}', file=sys.stderr)
    return 64


def parse_args(argv):
    """Retourne (dossier, trust), ou None si l'usage est invalide."""
    positional = [arg for arg in argv if arg != '--trust']
    if len(positional) != 1 or positional[0].startswith('-'):
        return None
    return Path(positional[0]), '--trust' in argv


def walk(root):
    """Parcourt tout le dossier ; os.walk ne descend pas dans les liens de répertoires."""
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames + filenames):
            yield Path(dirpath) / name


def scan_file(path, rel, findings):
    if path.stat().st_size > MAX_BYTES:
        findings.append(('structure', rel, 0, 'fichier de plus de 1 Mo'))
        return
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        findings.append(('structure', rel, 0, 'fichier non lisible en UTF-8'))
        return
    for number, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        for category, patterns in PATTERN_GROUPS:
            if any(pattern.lower() in lowered for pattern in patterns):
                findings.append((category, rel, number, line.strip()[:EXCERPT_CHARS]))


def scan_tree(root):
    findings = []
    if not (root / 'SKILL.md').is_file():
        findings.append(('structure', 'SKILL.md', 0, 'fichier absent'))
    for path in walk(root):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            findings.append(('écriture', rel, 0, 'lien symbolique'))
        elif path.is_file():
            scan_file(path, rel, findings)
    return findings


def print_refusal(findings):
    for category, rel, number, excerpt in findings[:MAX_PRINTED]:
        print(f'- {category} : {rel}:{number} : {excerpt}')
    if len(findings) > MAX_PRINTED:
        print(f'- … ({len(findings) - MAX_PRINTED} autres)')
    found = {category for category, _, _, _ in findings}
    names = [name for name in CATEGORY_ORDER if name in found]
    print(f'verdict : refus ({", ".join(names)})')
    return 1


def has_evaluation(root):
    evals = root / 'evals'
    if not evals.is_dir():
        return False
    return any(name in EVAL_FILES for _, _, names in os.walk(evals) for name in names)


def run_evaluation(root, trust):
    """Retourne None si l'évaluation réussit, sinon la raison de l'échec."""
    claude = os.environ.get('SKILL_CHECK_CLAUDE') or shutil.which('claude')
    if not claude:
        return 'exécutable absent'
    timeout = int(os.environ.get('SKILL_CHECK_TIMEOUT', str(DEFAULT_TIMEOUT)))
    with tempfile.TemporaryDirectory(prefix='skill-check-') as out:
        command = [claude, 'plugin', 'eval', str(root), '--no-publish', '--runs', '1', '--output-dir', out]
        if trust:
            command.append('--trust-plugin')
        try:
            done = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 'délai dépassé'
        except OSError:
            return 'exécutable absent'
    if done.returncode != 0:
        return f'code de sortie {done.returncode}'
    return None


def main(argv):
    parsed = parse_args(argv)
    if parsed is None:
        return usage_error('argument invalide')
    root, trust = parsed
    if root.is_symlink():
        return usage_error(f'lien symbolique interdit : {root}')
    if not root.exists():
        return usage_error(f'dossier absent : {root}')
    if not root.is_dir():
        return usage_error(f'pas un dossier : {root}')
    findings = scan_tree(root)
    if findings:
        return print_refusal(findings)
    if not has_evaluation(root):
        print('verdict : accepté (sans évaluation)')
        return 0
    try:
        failure = run_evaluation(root, trust)
    except ValueError:
        return usage_error('SKILL_CHECK_TIMEOUT doit être un nombre entier de secondes')
    if failure is not None:
        print(f'verdict : refus (évaluation échouée : {failure})')
        return 1
    print('verdict : accepté (évaluation réussie)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
