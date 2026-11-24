"""Ce que l'orchestrateur programme écrit pour le propriétaire (T14 B0, B2, B3, B4).

- `PRET-<id>.md` : une fiche est prête à fusionner, avec ses preuves ; la fusion attend `- fusion validée: <id>` dans le plan.
- `docs/QUESTIONS.md` : une question d'exécutant attend la réponse du propriétaire ; l'alerte du circuit part aussi. Une
  vérification arrêtée par l'environnement ou par la fiche (T117) y entre aussi, avec son avis.
- `PROPOSITION-<id>.md` : une demande est devenue une proposition de fiche ; elle attend `- proposition acceptée: <id>`.
- `docs/LOG.md` : l'entrée de fin de plan, ajoutée en bas, jamais une ligne existante modifiée.
Un texte venu d'un agent passe par le masquage des secrets du circuit avant d'entrer dans `docs/`, et ce que le
programme ajoute à `docs/` est commité aussitôt : la fusion suivante exige un dépôt propre.
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import journal
import steps

QUESTIONS = 'docs/QUESTIONS.md'
LOG = 'docs/LOG.md'
QUESTION_LIMIT = 300
COST_TIMEOUT = 60

# Même raison que review.RECORD : les modules du circuit tournent dans un processus à part.
ENGINE = """import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / 'scripts' / 'lib'))
request = json.load(sys.stdin)
if request['call'] == 'mask':
    from secrets import known_values, mask_secrets
    print(mask_secrets(request['text'], known_values(root)), end='')
else:
    import alerts
    alerts.emit(root, request['task'], int(request['question']), 'orchestrateur programme')
"""


def _engine(root, request):
    if not (Path(root) / 'scripts' / 'lib' / 'secrets.py').is_file():
        return None
    done = subprocess.run(
        [sys.executable, '-c', ENGINE, str(root)], input=json.dumps(request), cwd=str(root), capture_output=True, text=True
    )
    return done.stdout if done.returncode == 0 else None


def mask(root, text):
    """Texte masqué par le circuit ; sans circuit lisible, rien ne sort (une ligne vide vaut mieux qu'un secret)."""
    masked = _engine(root, {'call': 'mask', 'text': text})
    return masked if masked is not None else ''


def _today():
    return datetime.now(UTC).strftime('%Y-%m-%d')


def _relative(root, path):
    return Path(path).relative_to(Path(os.path.abspath(str(root)))).as_posix()


def _write(root, name, lines):
    path = journal.logs(root) / name
    path.write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')
    os.chmod(str(path), 0o600)
    return _relative(root, path)


def _append(root, rel, text):
    """Ajoute text en fin de root/rel (créé seulement s'il existe déjà) ; faux si le fichier manque."""
    path = journal.inside(root, rel)
    if not path.is_file():
        return False
    current = path.read_text(encoding='utf-8')
    path.write_text(current + ('' if not current or current.endswith('\n') else '\n') + text, encoding='utf-8')
    return True


def _cost(root, task_id):
    script = Path(root) / 'scripts' / 'report.sh'
    if not script.is_file():
        return 'non mesuré'
    try:
        done = subprocess.run(['bash', str(script), '--task', task_id], cwd=str(root), capture_output=True, text=True, timeout=COST_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 'non mesuré (délai dépassé)'
    return done.stdout.strip() or 'non mesuré'


def _step(entry, name):
    step = entry['steps'].get(name) or {}
    return step.get('status') or 'non lancée'


def ready(root, plan, item, entry):
    """Écrit l'avis « prêt à fusionner » d'une fiche et rend son chemin relatif à root."""
    task_id = item['id']
    status = steps.delegate_status(root, task_id) or {}
    files = status.get('files_changed') or []
    review = entry.get('review_verdict') or ('pas de relecture demandée' if not item.get('review') else 'non lancée')
    lines = [
        f'# Prêt à fusionner : {task_id}',
        '',
        '- Fiche : `{}`'.format(item['fiche']),
        '- Exécutant : {} ; issue {} ; tentatives {}'.format(
            status.get('agent') or entry.get('agent') or '?', status.get('outcome') or '?', status.get('attempts') or '?'
        ),
        '- Fichiers modifiés ({}) : {}'.format(len(files), ', '.join(f'`{name}`' for name in files) or 'aucun'),
        '- Vérification sur l\'hôte : {} (`{}`)'.format(_step(entry, 'verify'), plan['verify'] or 'aucune'),
        f'- Relecture ciblée : {review}',
        f'- Diff complet : `.workspace/run/delegate/{task_id}/diff.txt`',
        '- Coût de la tâche (`report.sh --task`) :',
        '',
        '```',
        _cost(root, task_id),
        '```',
        '',
        'Pour fusionner : écrire dans le plan `{}` la ligne `- fusion validée: {}`, puis relancer la même commande.'.format(
            plan['path'], task_id
        ),
        f'Le programme lance alors `task-workspace.sh merge {task_id} --no-verify-again --no-integration`, recopie le miroir et commite.',
    ]
    return _write(root, f'PRET-{task_id}.md', lines)


def _question_text(root, task_id, number):
    path = Path(root) / '.workspace' / 'orchestration' / 'tasks' / task_id / 'context' / 'questions' / f'{number}.md'
    try:
        text = ' '.join(path.read_text(encoding='utf-8', errors='replace').split())
    except OSError:
        return '(texte de la question introuvable dans le registre)'
    return text if len(text) <= QUESTION_LIMIT else text[:QUESTION_LIMIT] + '…'


def questions(root, task_id, numbers):
    """Inscrit chaque question une fois dans docs/QUESTIONS.md, alerte, et rend le nombre de lignes ajoutées."""
    path = journal.inside(root, QUESTIONS)
    existing = path.read_text(encoding='utf-8') if path.is_file() else ''
    added = []
    for number in numbers:
        number = '%03d' % int(number)
        _engine(root, {'call': 'alert', 'task': task_id, 'question': number})
        marker = f'· {task_id} (question {number}'
        if marker in existing:
            continue
        added.append(
            f'- [ ] {_today()} {marker}, usine) · {mask(root, _question_text(root, task_id, number))} → répondre : `bash scripts/questions.sh answer {task_id}/{number} "<réponse>" --by owner`, puis relancer le plan.\n'
        )
    if added and _append(root, QUESTIONS, ''.join(added)):
        steps.commit_paths(root, [QUESTIONS], f'docs: question from {task_id} waits for the owner')
    return len(added)


BLOCKED = {
    'environnement': "vérification rouge sur la base aussi : l'environnement est en cause, aucune tentative comptée",
    'fiche': "test existant hors périmètre rouge : trou de la fiche ou test cassé que l'exécutant ne pouvait pas corriger, aucune tentative comptée",
    'diagnostic': "le diagnostic met en cause le circuit, la fiche ou l'environnement, pas l'exécutant : ni reprise ni tentative comptée",
}


def blocked(root, task_id, category, notice):
    """T117 : une ligne dans docs/QUESTIONS.md quand l'environnement ou la fiche arrête une tâche ; vrai si ajoutée."""
    path = journal.inside(root, QUESTIONS)
    marker = f'· {task_id} (usine, {category}, avis `{notice}`)'
    if path.is_file() and marker in path.read_text(encoding='utf-8'):
        return False
    line = f'- [ ] {_today()} {marker} · {BLOCKED[category]} → lire l\'avis, corriger, puis relancer le plan.\n'
    if not _append(root, QUESTIONS, line):
        return False
    steps.commit_paths(root, [QUESTIONS], f'docs: {category} blocks {task_id}, waits for the owner')
    return True


def unanswered(root, task_id, numbers):
    """Numéros de questions sans fichier de réponse dans le registre de la tâche."""
    answers = Path(root) / '.workspace' / 'orchestration' / 'tasks' / task_id / 'context' / 'answers'
    return [number for number in numbers if not (answers / ('%03d.md' % int(number))).is_file()]


def proposal_dir(root, task_id):
    return journal.inside(root, f'.workspace/proposals/{task_id}')


def proposal(root, plan, item, check_log):
    """Écrit l'avis d'une proposition prête et rend son chemin relatif à root."""
    task_id, folder = item['id'], proposal_dir(root, item['id'])
    tests = sorted(_relative(root, path) for path in (folder / 'acceptance').rglob('*') if path.is_file())
    lines = [
        f'# Proposition de fiche : {task_id}',
        '',
        '- Demande : `{}`'.format(item['demande']),
        f'- Fiche proposée : `{_relative(root, folder / "task.md")}`',
        '- Tests cachés proposés ({}) : {}'.format(len(tests), ', '.join(f'`{name}`' for name in tests) or 'aucun'),
        f'- Avis de `plan-check.sh --with-model` : `{check_log}` (verdict « bon »)',
        '',
        'Relire la fiche et les tests. Pour la faire exécuter : écrire dans le plan `{}` la ligne `- proposition acceptée: {}`,'.format(
            plan['path'], task_id
        ),
        'puis relancer la même commande. Le programme la copie dans `{}`, prépare (`--selfcheck`) et délègue.'.format(item['fiche']),
    ]
    return _write(root, f'PROPOSITION-{task_id}.md', lines)


def _copy(source, target, overwrite=False):
    if source.is_symlink():
        raise ValueError(f'lien symbolique refusé : {source}')
    if not overwrite and target.exists() and target.read_bytes() != source.read_bytes():
        raise ValueError(f'déjà présent avec un autre contenu : {target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(source), str(target))


def stage_tests(root, task_id):
    """Recopie les tests cachés de la proposition dans `.workspace/acceptance/<id>/`, là où plan-check et le circuit
    les lisent ; ils y restent invisibles de l'exécutant, et rien n'est délégué sans l'accord du propriétaire."""
    acceptance = proposal_dir(root, task_id) / 'acceptance'
    for source in sorted(acceptance.rglob('*')) if acceptance.is_dir() else []:
        if source.is_symlink() or source.is_file():
            target = journal.inside(root, f'.workspace/acceptance/{task_id}/{source.relative_to(acceptance).as_posix()}')
            _copy(source, target, overwrite=True)


def accept(root, item):
    """Copie la proposition acceptée en fiche et tests cachés du circuit ; ValueError si elle manque ou déborde."""
    task_id, folder = item['id'], proposal_dir(root, item['id'])
    if not (folder / 'task.md').is_file():
        raise ValueError(f'proposition absente : {_relative(root, folder / "task.md")}')
    _copy(folder / 'task.md', journal.inside(root, item['fiche']))
    # Le propriétaire a pu corriger la proposition après plan-check : ses tests remplacent ceux recopiés avant.
    stage_tests(root, task_id)


def log_entry(root, run, report_path):
    """Ajoute l'entrée de fin de plan à docs/LOG.md et la commite ; faux si le projet n'a pas de LOG."""
    done, failed = run.get('done') or [], run.get('failed') or []
    reasons = ['{} ({})'.format(task_id, (run['tasks'].get(task_id) or {}).get('reason') or '?') for task_id in failed]
    text = '\n## [{}] usine | Plan `{}`, session {}\n{}\n'.format(
        _today(),
        run.get('plan'),
        run.get('session'),
        'Programme d\'orchestration : {} fiche(s) fusionnée(s) ({}) ; {} en échec{}. Rapport : `{}`.'.format(
            len(done), ', '.join(done) or 'aucune', len(failed), (' : ' + ', '.join(reasons)) if reasons else '', report_path
        ),
    )
    if not _append(root, LOG, text):
        return False
    steps.commit_paths(root, [LOG], 'docs: LOG entry for orchestrator session {}'.format(run.get('session')))
    return True
