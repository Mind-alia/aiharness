#!/usr/bin/env python3
"""Portable entry point for the Workspace IA kit."""

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(args, cwd=None):
    subprocess.run(args, cwd=cwd or ROOT, check=True)


def console_root():
    env = os.environ.get('WORKSPACE_CONSOLE_ROOT')
    pointer = ROOT / '.workspace/console-root'
    if env is not None:
        raw, base = env, Path.cwd()
    elif pointer.is_file():
        raw, base = (pointer.read_text().splitlines() or [''])[0].strip(), ROOT
    else:
        raw, base = str(ROOT.parent / 'workspace-console'), ROOT
    path = (base / Path(raw).expanduser()).resolve()
    if not raw or not (path / 'package.json').is_file():
        raise SystemExit(
            f'console introuvable : {path} ; définir WORKSPACE_CONSOLE_ROOT ou écrire le chemin '
            'dans .workspace/console-root (dépôt Mind-alia/workspace-console)'
        )
    return path


def doctor():
    missing = []
    for name in ['git', 'node', 'npm']:
        present = shutil.which(name) is not None
        print(f"{name}: {'disponible' if present else 'manquant'}")
        if not present:
            missing.append(name)
    if shutil.which('node'):
        version = subprocess.check_output(['node', '--version'], text=True).strip()
        numbers = tuple(int(n) for n in version.lstrip('v').split('.')[:2])
        print(f'Node {version} — Node 24 LTS requis pour les dépendances verrouillées')
        if numbers[0] != 24:
            missing.append('Node 24 LTS (nvm install && nvm use)')
    python = '.'.join(str(part) for part in sys.version_info[:3])
    print(f'Python {python} — Python 3.14 requis (.python-version)')
    if sys.version_info[:2] < (3, 14):
        missing.append('Python 3.14 (python3.14 dans le PATH)')
    for name in ['claude', 'codex', 'agy']:
        print(f"{name}: {'disponible' if shutil.which(name) else 'optionnel, à installer et connecter séparément'}")
    return not missing


def install_statusline(settings=None, replace=False):
    """Claude Code status line of the kit (template/scripts/lib/statusline.py): writes ~/.workspace-console/claude-statusline.jsonl."""
    library = str(ROOT / 'template/scripts/lib')
    if library not in sys.path:
        sys.path.insert(0, library)
    import statusline

    target = Path(settings).expanduser() if settings else Path.home() / '.claude/settings.json'
    command = 'sh ' + shlex.quote(str(ROOT / 'scripts/statusline.sh'))
    outcome = statusline.install(target, command, replace=replace)
    print(f'Barre d\'état Claude : {outcome} ({target})')
    if outcome == 'conservé':
        print('Une autre barre d\'état est déjà réglée ; la remplacer : ./workspace statusline-install --replace')
    return outcome


def setup():
    if not doctor():
        raise ValueError('Installez les prérequis manquants avant de continuer.')
    console = console_root()
    run(['npm', 'ci'], console)
    run(['npm', 'run', 'build'], console)
    install_statusline()
    print('Prêt : ./workspace new mon-projet --open')


def create_project(name, parent=None, profile="docs"):
    if profile not in {"node", "docker", "python", "docs", "c"}:
        raise ValueError(f"Profil inconnu : {profile}")
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name):
        raise ValueError('Nom attendu : lettres, chiffres, tirets ; aucun chemin.')
    target = (Path(parent).expanduser().resolve() if parent else ROOT / 'projects') / name
    if target.exists():
        raise ValueError(f'Le dossier existe déjà, aucun fichier écrasé : {target}')
    target.parent.mkdir(parents=True, exist_ok=True)

    def ignore_kit_tests(directory, names):
        if Path(directory) == ROOT / 'template/scripts':
            return set(names) & {'__tests__', 'test-support'}
        return set()

    shutil.copytree(ROOT / 'template', target, ignore=ignore_kit_tests)
    creation_date = date.today().isoformat()
    for p in target.rglob('*'):
        if p.is_file() and p.suffix in {'.md', '.yaml', '.toml', '.json', '.csv'}:
            try:
                text = p.read_text()
            except UnicodeError:
                continue
            p.write_text(text.replace('__PROJECT_NAME__', name).replace('__CREATION_DATE__', creation_date))
    # Materialize documentation paths declared by the starter's governance map.
    (target / 'docs/plans/active').mkdir(parents=True, exist_ok=True)
    (target / 'docs/plans/done').mkdir(parents=True, exist_ok=True)
    (target / 'docs/adr').mkdir(parents=True, exist_ok=True)
    (target / 'docs/adr/README.md').write_text('# Décisions d’architecture\n\nAucune décision enregistrée à la création du projet.\n')
    (target / 'project.profile').write_text(profile + '\n')
    run(['git', 'init', '-q'], target)
    print(f'Projet créé : {target}')
    return target


CIRCUIT_SCRIPTS = (
    'prepare-task.sh',
    'delegate.sh',
    'breaker.sh',
    'report.sh',
    'questions.sh',
    'advise.sh',
    'experiment.sh',
    'attack-bench.sh',
    'metrics.sh',
    'supervise.sh',
    'task-workspace.sh',
    'sandbox.sh',
    'run-in-sandbox.sh',
    'orchestrator.sh',
    'ask.sh',
    'answer.sh',
    'verify-task.sh',
    'check-scope.sh',
    'scope-baseline.sh',
    'test-secrets.sh',
    'check-secret-tracing.sh',
    'note.sh',
    'report-check.sh',
    'weekly-report.sh',
    'statusline.sh',
    'skill-check.sh',
)


def python_distribution(project):
    """True when `pyproject.toml` declares a Python package (`[project]` table): its `package.json` is side tooling."""
    try:
        text = (project / 'pyproject.toml').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return False
    return re.search(r'^\[project\]\s*$', text, re.MULTILINE) is not None


def detect_profile(project):
    if (project / 'package.json').is_file() and not python_distribution(project):
        return 'node'
    if (project / 'pyproject.toml').is_file() or any(project.glob('requirements*')):
        return 'python'
    docker_files = ('Dockerfile', 'docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml', 'docker')
    if (project / 'Makefile').is_file() and any((project / name).exists() for name in docker_files):
        return 'docker'
    if (project / 'Makefile').is_file() and any(
        not any(part.startswith('.') for part in path.relative_to(project).parts) for path in project.rglob('*.c')
    ):
        return 'c'
    return 'docs'


def adopt_repository(source, directory=None, name=None):
    """Clone a foreign repository as a mission; the work lives in tools/mission/adopt.py."""
    tools = str(ROOT / 'tools/mission')
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import adopt

    return adopt.adopt(source, directory=directory, name=name)


def install_tooling(path, dry_run=False, profile=None, force_circuit=False):  # noqa: C901, PLR0915
    """Plan the complete installation before touching the destination."""
    project = Path(path).expanduser().resolve()
    if project == ROOT.resolve():
        raise ValueError('Installation refusée dans le kit lui-même.')
    repository = subprocess.run(['git', '-C', str(project), 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    if repository.returncode or Path(repository.stdout.strip()).resolve() != project:
        raise ValueError(f'Le chemin doit être la racine d’un dépôt Git : {project}')
    if profile is not None and profile not in {'node', 'docker', 'python', 'docs', 'c'}:
        raise ValueError(f'Profil inconnu : {profile}')

    template = ROOT / 'template'
    circuit = [Path('scripts') / name for name in CIRCUIT_SCRIPTS]
    circuit += [source.relative_to(template) for source in sorted((template / 'scripts/lib').glob('*.py'))]
    # Container image recipes: the build command printed by the circuit points to the project's docker/ folder.
    circuit += [source.relative_to(template) for source in sorted((template / 'docker').glob('*.Dockerfile'))]
    # team.json is the project's team registry: installed once, never replaced (not even with --force-circuit).
    # report-check.sh reads the verifier rules and the extraction guide from docs/ (installed once, editable by the project).
    documents = [
        Path('docs/BRIEF-AGENTS.md'),
        Path('docs/PIEGES.md'),
        Path('team.json'),
        Path('docs/REGLES-VERIFICATEUR.md'),
        Path('docs/CONSIGNE-EXTRACTION.md'),
    ]
    report = {key: [] for key in ('Ajoutés', 'Identiques', 'Conservés (différents)', 'Ignorés')}
    writes = []
    conflicts = []

    def safe_target(relative):
        target = project / relative
        for part in [target] + list(target.parents):
            if part == project:
                break
            if part.is_symlink():
                raise ValueError(f'Chemin symbolique refusé : {relative}')
        if target.exists() and not target.is_file():
            raise ValueError(f'Fichier attendu : {relative}')
        return target

    for relative in circuit + documents:
        source = template / relative
        content = source.read_bytes()
        target = safe_target(relative)
        if not target.exists():
            report['Ajoutés'].append(str(relative))
            writes.append((target, content, source.stat().st_mode | (0o111 if relative.suffix == '.sh' else 0)))
        elif target.read_bytes() == content:
            report['Identiques'].append(str(relative))
        elif relative in circuit and force_circuit:
            report['Ajoutés'].append(str(relative) + ' (remplacé --force-circuit)')
            writes.append((target, content, source.stat().st_mode | (0o111 if relative.suffix == '.sh' else 0)))
        else:
            report['Conservés (différents)'].append(str(relative))
            if relative in circuit:
                conflicts.append(str(relative))

    # Other template files are outside this command's installation scope.
    selected = set(circuit + documents)
    excluded = list((template / 'scripts').glob('*'))
    excluded += list((template / 'scripts/lib').glob('*'))
    excluded += list((template / 'docs').glob('*'))
    excluded += [template / 'AGENTS.md', template / 'CLAUDE.md']
    for source in sorted(excluded):
        relative = source.relative_to(template)
        if not source.is_file() or relative in selected:
            continue
        target = project / relative
        if target.is_file():
            category = 'Identiques' if target.read_bytes() == source.read_bytes() else 'Conservés (différents)'
        else:
            category = 'Ignorés'
        report[category].append(str(relative))

    target = safe_target(Path('project.profile'))
    if target.exists():
        report['Ignorés'].append('project.profile (existant, conservé)')
    else:
        chosen = profile or detect_profile(project)
        report['Ajoutés'].append(f'project.profile ({chosen})')
        writes.append((target, (chosen + '\n').encode(), None))

    target = safe_target(Path('.gitignore'))
    content = target.read_bytes() if target.exists() else b''
    lines = content.decode().splitlines()
    missing = [entry for entry in ('.workspace/', '__pycache__/') if entry not in lines]
    if missing:
        addition = b'\n' if content and not content.endswith(b'\n') else b''
        addition += ('\n'.join(missing) + '\n').encode()
        writes.append((target, content + addition, None))
        report['Ajoutés'].append('.gitignore (ajout : ' + ', '.join(missing) + ')')
    else:
        report['Identiques'].append('.gitignore (entrées présentes)')

    target = safe_target(Path('.gitattributes'))
    content = target.read_bytes() if target.exists() else b''
    lines = content.decode().splitlines()
    missing = [
        line for line in (template / '.gitattributes').read_text().splitlines() if line and not line.startswith('#') and line not in lines
    ]
    if missing:
        addition = b'\n' if content and not content.endswith(b'\n') else b''
        addition += ('\n'.join(missing) + '\n').encode()
        writes.append((target, content + addition, None))
        report['Ajoutés'].append('.gitattributes (ajout : ' + ', '.join(missing) + ')')
    else:
        report['Identiques'].append('.gitattributes (entrées présentes)')

    print(f"Tooling : {project}" + (' — simulation, aucune écriture' if dry_run else ''))
    if conflicts:
        print('Installation refusée : aucune écriture ; conflits du circuit ci-dessous.')
    for category, entries in report.items():
        print(f'{category} ({len(entries)}) :')
        for entry in entries:
            print(f'  {entry}')
    if conflicts:
        raise ValueError('Fichiers du circuit différents : ' + ', '.join(conflicts) + ' ; utilisez --force-circuit pour les remplacer.')
    if not dry_run:
        for target, content, mode in writes:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            if mode is not None:
                target.chmod(mode & 0o777)
        # Subagents follow team.json at every installation; a hand-written file or an invalid team.json only warns.
        try:
            generate_team_agents(project)
        except ValueError as error:
            print(f'Sous-agents non générés : {error}')
        print(
            'Images des conteneurs : docker build -t workspace-node-git:24-bookworm -f docker/node-git.Dockerfile docker'
            ' ; docker build -t workspace-python-git:3.14 -f docker/python-git.Dockerfile docker'
            ' ; docker build -t workspace-c-norm:bookworm -f docker/c-norm.Dockerfile docker'
        )
    return report


def generate_team_agents(path):
    """Regenerate `.claude/agents/` from the project's team.json (template/scripts/lib/team_agents.py)."""
    library = str(ROOT / 'template/scripts/lib')
    if library not in sys.path:
        sys.path.insert(0, library)
    import team_agents

    result = team_agents.generate(Path(path).expanduser().resolve())
    print(
        'Sous-agents : %d écrits, %d identiques, %d retirés (.claude/agents)'
        % (len(result['written']), len(result['unchanged']), len(result['removed']))
    )
    return result


def open_project(path, no_browser=False):
    project = Path(path).expanduser().resolve()
    entry = console_root() / 'packages/cli/dist/bin.js'
    if not project.is_dir():
        raise ValueError(f'Projet introuvable : {project}')
    if not entry.is_file():
        raise ValueError('Interface non compilée : lancez ./workspace setup')
    args = ['node', str(entry), 'open']
    if no_browser:
        args.append('--no-browser')
    run(args, project)


def main():  # noqa: C901, PLR0915
    parser = argparse.ArgumentParser(description='Workspace IA — créer un projet et ouvrir sa console locale')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor')
    sub.add_parser('setup')
    p = sub.add_parser('statusline-install')
    p.add_argument('--settings')
    p.add_argument('--replace', action='store_true')
    p = sub.add_parser('new')
    p.add_argument('name')
    p.add_argument('--parent')
    p.add_argument('--profile', choices=['node', 'docker', 'python', 'docs', 'c'], default='docs')
    p.add_argument('--open', action='store_true')
    p = sub.add_parser('tooling')
    p.add_argument('project')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--profile', choices=['node', 'docker', 'python', 'docs', 'c'])
    p.add_argument('--force-circuit', action='store_true')
    p = sub.add_parser('adopt')
    p.add_argument('source')
    p.add_argument('--dir')
    p.add_argument('--name')
    p = sub.add_parser('team-agents')
    p.add_argument('project', nargs='?', default='.')
    p = sub.add_parser('open')
    p.add_argument('project', nargs='?', default='.')
    p.add_argument('--no-browser', action='store_true')
    sub.add_parser('console-path')
    args = parser.parse_args()
    try:
        if args.command == 'doctor':
            return 0 if doctor() else 1
        if args.command == 'setup':
            setup()
        elif args.command == 'statusline-install':
            install_statusline(args.settings, args.replace)
        elif args.command == 'new':
            project = create_project(args.name, args.parent, args.profile)
            if args.open:
                open_project(project)
        elif args.command == 'tooling':
            install_tooling(args.project, args.dry_run, args.profile, args.force_circuit)
        elif args.command == 'adopt':
            adopt_repository(args.source, args.dir, args.name)
        elif args.command == 'team-agents':
            generate_team_agents(args.project)
        elif args.command == 'open':
            open_project(args.project, args.no_browser)
        elif args.command == 'console-path':
            print(console_root())
        return 0
    except SystemExit as error:
        print(error.code, file=sys.stderr)
        return 2
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'Erreur : {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
