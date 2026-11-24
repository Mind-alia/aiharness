"""Generate the Claude Code sub-agents (.claude/agents/<slug>.md) from the team registry team.json.

Public API: slug(name) -> str, render(member) -> str, generate(project_root) -> dict.
Command line: python3 team_agents.py <project root>. Only files carrying MARKER are ever rewritten or removed.
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import safety  # noqa: E402
import team  # noqa: E402

MARKER = '<!-- Généré par workspace.py team-agents depuis team.json ; ne pas modifier à la main, relancer la commande. -->'
MODELS = {'claude-sonnet': 'sonnet', 'claude-haiku': 'haiku', 'claude-opus': 'opus', 'codex': 'haiku', 'antigravity': 'haiku'}
RELAYS = {'codex': 'codex.sh', 'antigravity': 'gemini.sh'}
ROLE_DEFAULT = "membre de l'équipe"
AGENTS_DIR = ('.claude', 'agents')
EXIT_USAGE = 64


def slug(nom):
    """Lowercase ASCII slug of a member name: accents removed, other characters replaced by dashes."""
    ascii_name = unicodedata.normalize('NFKD', nom.lower()).encode('ascii', 'ignore').decode('ascii')
    result = re.sub(r'[^a-z0-9-]', '-', ascii_name).strip('-')
    if not result:
        raise ValueError('slug vide pour : ' + repr(nom))
    return result


def tools_for(member):
    """Tool list: relays only read and run the script; otherwise the member type decides."""
    if member['executant'] in RELAYS:
        return 'Read, Bash'
    kind = member.get('type')
    if kind in ('code', 'test'):
        return 'Read, Edit, Write, Bash, Grep, Glob'
    if kind == 'docs':
        return 'Read, Edit, Write, Grep, Glob'
    return 'Read, Grep, Glob'


def render(member):
    """Text of the sub-agent file for one team member."""
    nom = member['nom']
    executant = member['executant']
    role = member.get('role') or ROLE_DEFAULT
    description = json.dumps(f'{nom} : {role} (exécutant {executant})', ensure_ascii=False)
    lines = [
        '---',
        f'name: {slug(nom)}',
        f'description: {description}',
        f'model: {MODELS[executant]}',
        f'tools: {tools_for(member)}',
        '---',
        MARKER,
        '',
        f"Tu es {nom}, membre de l'équipe du projet : {role}.",
        "Lis d'abord la fiche de la tâche que l'orchestrateur te confie (`.workspace/tasks/<id>.md`) ; "
        "ne modifie que les fichiers de son périmètre `allowed`.",
        'Ne touche jamais `scripts/`, `project.profile` ni `.workspace/` ; les règles du projet sont dans `AGENTS.md`.',
        "Termine par un compte rendu court : fichiers modifiés, vérification lancée, doutes.",
    ]
    if executant in RELAYS:
        lines.append(
            'Tu ne fais pas le travail toi-même : transmets la demande par '
            f'`bash scripts/{RELAYS[executant]} review <consigne> <réponse>` et rends sa réponse telle quelle.'
        )
    return '\n'.join(lines) + '\n'


def relative(path, root):
    return path.relative_to(root).as_posix()


def generated_files(agents, root):
    """Sub-agent files of the folder that carry MARKER (symbolic links are refused)."""
    if not agents.is_dir():
        return []
    found = []
    for path in sorted(agents.glob('*.md')):
        safety.check_no_symlinks(path, root)
        if path.is_file() and MARKER in path.read_text(encoding='utf-8'):
            found.append(path)
    return found


def generate(project_root):
    """Write the sub-agents of team.json; every check runs before the first write."""
    root = Path(project_root)
    registry = root / team.REGISTRY_NAME
    if not registry.is_file():
        raise ValueError(f'team.json absent : {registry}')
    safety.check_no_symlinks(registry, root)
    texts = {}
    for member in team.load(registry):
        name = slug(member['nom'])
        if name in texts:
            raise ValueError('slug en double : ' + name)
        texts[name] = render(member)

    agents = root.joinpath(*AGENTS_DIR)
    safety.check_no_symlinks(agents, root)
    wanted = {agents / (name + '.md'): text for name, text in texts.items()}
    for path in wanted:
        safety.check_no_symlinks(path, root)
        if path.exists() and (not path.is_file() or MARKER not in path.read_text(encoding='utf-8')):
            raise ValueError('fichier non généré : ' + relative(path, root))
    removed = [path for path in generated_files(agents, root) if path not in wanted]

    written, unchanged = [], []
    for path, text in wanted.items():
        if path.is_file() and path.read_text(encoding='utf-8') == text:
            unchanged.append(path)
            continue
        agents.mkdir(parents=True, exist_ok=True)
        safety.write_file_secure(path, text)
        written.append(path)
    for path in removed:
        path.unlink()
    return {
        'written': sorted(relative(path, root) for path in written),
        'unchanged': sorted(relative(path, root) for path in unchanged),
        'removed': sorted(relative(path, root) for path in removed),
    }


def main(argv):
    if len(argv) != 1:
        print('usage: python3 team_agents.py <racine du projet>', file=sys.stderr)
        return EXIT_USAGE
    try:
        result = generate(argv[0])
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(
        f"Sous-agents : {len(result['written'])} écrits, {len(result['unchanged'])} identiques, "
        f"{len(result['removed'])} retirés (.claude/agents)"
    )
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
