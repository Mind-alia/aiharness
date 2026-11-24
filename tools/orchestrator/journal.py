"""Journal de reprise de l'orchestrateur programme (fiche T14, fiche 2) : lecture, écriture atomique, dossier des journaux."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

JOURNAL = '.workspace/orchestrator-run.json'
LOGS = '.workspace/orchestrator-run'
# États d'une fiche (GT2) et transitions permises ; `done` et `failed` sont terminaux.
STATES = ('queued', 'in_progress', 'blocked', 'awaiting_user', 'done', 'failed')
TRANSITIONS = {
    'queued': {'in_progress'},
    'in_progress': {'awaiting_user', 'blocked', 'done', 'failed'},
    'awaiting_user': {'in_progress'},
    'blocked': {'in_progress'},
    'done': set(),
    'failed': set(),
}
# Motif de reprise d'une fiche `awaiting_user`, selon le motif de sa dernière transition.
RESUME_REASONS = {'validation': 'validation reçue', 'question': 'réponse reçue', 'acceptance': 'proposition acceptée'}
NO_VALUE = '-'
# Copie du circuit dans le kit, quand le projet n'a pas la sienne (masquage des secrets, coût d'une tâche).
KIT_LIB = Path(__file__).resolve().parents[2] / 'template' / 'scripts' / 'lib'


def now():
    """Horodatage UTC AAAA-MM-JJTHH:MM:SSZ."""
    return datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def precise():
    """Instant UTC à la microseconde, pour le comparer aux événements (`at` du journal d'événements)."""
    return datetime.now(UTC).isoformat()


def inside(root, path):
    """Chemin absolu sous root ; ValueError hors de root ou à travers un lien symbolique."""
    base = os.path.abspath(str(root))
    target = os.path.abspath(os.path.join(base, str(path)))
    if target != base and not target.startswith(base + os.sep):
        raise ValueError(f'chemin hors de la racine : {path}')
    current = Path(base)
    for part in Path(target).relative_to(base).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f'lien symbolique refusé : {current}')
    return Path(target)


def load(root):
    """Journal de la racine, ou None s'il n'existe pas. ValueError s'il est illisible ou à travers un lien."""
    path = inside(root, JOURNAL)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError(f'journal illisible : {error}')
    if not isinstance(data, dict):
        raise ValueError('journal illisible : objet JSON attendu')
    return data


def save(root, data):
    """Écriture atomique (fichier temporaire du même dossier puis os.replace), mode 0600."""
    path = inside(root, JOURNAL)
    os.makedirs(str(path.parent), mode=0o700, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix='.orchestrator-run-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        os.chmod(temp, 0o600)
        os.replace(temp, str(path))
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def logs(root):
    """Dossier des journaux (0700), créé avec `.workspace/` ; ValueError sur un lien symbolique."""
    base = Path(os.path.abspath(str(root)))
    for part in ('.workspace', 'orchestrator-run'):
        base = base / part
        if base.is_symlink():
            raise ValueError(f'lien symbolique refusé : {base}')
        if not base.exists():
            base.mkdir(mode=0o700)
    return base


def circuit_module(root, name):
    """Module autonome `name` du circuit (`scripts/lib` du projet, sinon celui du kit), chargé sous un nom privé :
    `scripts/lib/secrets.py` ne doit pas masquer le module `secrets` de Python dans le programme. None s'il manque."""
    for folder in (Path(os.path.abspath(str(root))) / 'scripts' / 'lib', KIT_LIB):
        path = folder / f'{name}.py'
        if path.is_file() and not path.is_symlink():
            spec = importlib.util.spec_from_file_location(f'circuit_{name}', str(path))
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return None


def mask(root, text):
    """Texte masqué comme le fait le circuit (`secrets.mask_secrets` et valeurs connues du projet) : une consigne
    qui le contient n'est pas refusée par le garde-fou de `delegate.py` (T100). Sans module, rend None."""
    secrets = circuit_module(root, 'secrets')
    if secrets is None:
        return None
    return secrets.mask_secrets(text, secrets.known_values(Path(os.path.abspath(str(root)))))


def write_note(root, name, text):
    """Écrit un contexte d'étape (diagnostic, corrections, échec de qualité) dans le dossier des journaux, mode 0600,
    masqué avant d'entrer dans une consigne (T100) ; rend son chemin relatif à root."""
    masked = mask(root, text)
    if masked is None:
        raise ValueError('masquage des secrets impossible : scripts/lib/secrets.py absent')
    path = logs(root) / name
    path.write_text(masked.rstrip() + '\n', encoding='utf-8')
    os.chmod(str(path), 0o600)
    return path.relative_to(Path(os.path.abspath(str(root)))).as_posix()


def new(plan):
    """Journal d'un nouveau passage : toutes les clés de la fiche, aucune fiche commencée."""
    stamp = now()
    entry = {
        'agent': None,
        'failures': 0,
        'reason': '',
        'merge_commit': None,
        'commit': None,
        # T14 phase B : avis en attente du propriétaire, verdict de relecture, ronde de correction (contexte, exécutant, seuil).
        'notice': None,
        'review_verdict': None,
        'stage_context': None,
        'override': None,
        'since': None,
        'correction': False,
        # T113 : corrections demandées par la relecture, que la seconde relecture vérifie une à une.
        'review_fixes': None,
        'state': 'queued',
    }
    return {
        'schema_version': 1,
        'plan': plan['path'],
        'plan_sha256': plan['sha256'],
        'session': plan['session'],
        'by': plan['by'],
        'model': plan['model'],
        'pid': os.getpid(),
        'started_at': stamp,
        'updated_at': stamp,
        'finished_at': None,
        'status': 'running',
        'stop_reason': None,
        'resumes': 0,
        'current': None,
        'child': None,
        'children': {},
        'order': [item['id'] for item in plan['tasks']],
        'done': [],
        'failed': [],
        'decisions': [],
        'report_check_enabled': plan['report_check'],
        'report': plan['report'],
        'final': {},
        'tasks': {item['id']: dict(entry, fiche=item['fiche'], steps={}, transitions=[]) for item in plan['tasks']},
    }


def state_of(entry):
    """État d'une fiche du journal ; `queued` pour un journal écrit avant les états."""
    return entry.get('state', 'queued')


def transition(data, task_id, to, reason):
    """Fait passer la fiche à l'état `to` et date la transition ; rend vrai si l'état a changé.

    ValueError (journal intact) sur un état inconnu, un motif vide ou une transition interdite ; même état : rien.
    """
    if to not in STATES:
        raise ValueError(f'état inconnu : {to!r}')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('motif de transition vide')
    entry = data['tasks'][task_id]
    before = state_of(entry)
    if before == to:
        return False
    if to not in TRANSITIONS[before]:
        raise ValueError(f'transition interdite pour {task_id} : {before} -> {to}')
    entry['transitions'] = entry.get('transitions', []) + [{'at': now(), 'from': before, 'to': to, 'reason': reason}]
    entry['state'] = to
    return True


def resume_reason(entry):
    """Motif de la reprise d'une fiche `awaiting_user`, d'après ce qu'elle attendait."""
    last = (entry.get('transitions') or [{}])[-1]
    return RESUME_REASONS.get(last.get('reason') or '', 'reprise')


def status_lines(data, given, path):
    """Lignes de `run.py --status` pour le journal `data` du plan `path` (donné sous la forme `given`)."""
    if data is None:
        return [f'aucun passage pour {given}']
    if data.get('plan') != path:
        return ["journal d'un autre plan : {}".format(data.get('plan'))]
    head = 'plan : {} ; passage : {} ({}) ; reprises : {}'.format(
        given, data.get('status', NO_VALUE), data.get('stop_reason') or 'en cours', data.get('resumes', NO_VALUE)
    )
    lines = [head]
    for task_id in data.get('order', []):
        entry = data.get('tasks', {}).get(task_id, {})
        recorded = list(entry.get('steps') or {})
        last = (entry.get('transitions') or [{}])[-1]
        step = recorded[-1] if recorded else NO_VALUE
        lines.append('\t'.join([task_id, state_of(entry), step, last.get('at') or NO_VALUE, last.get('reason') or NO_VALUE]))
    return lines


def archive(root, old):
    """Déplace un journal terminé sous `.workspace/orchestrator-run/archive/`."""
    folder = logs(root) / 'archive'
    folder.mkdir(mode=0o700, exist_ok=True)
    name = '{}-{}.json'.format((old.get('finished_at') or now()).replace(':', ''), old.get('session'))
    os.replace(str(inside(root, JOURNAL)), str(folder / name))


def detach(root, argv):
    """Relance `argv` hors de la fenêtre (nouvelle session, sortie ajoutée à run.log) et rend 0 aussitôt."""
    fd = os.open(str(logs(root) / 'run.log'), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(root),
            env=dict(os.environ, ORCHESTRATOR_DETACHED='1'),
            stdin=subprocess.DEVNULL,
            stdout=fd,
            stderr=fd,
            start_new_session=True,
        )
    finally:
        os.close(fd)
    print('détaché : pid {}, journal {}, sortie {}'.format(proc.pid, JOURNAL, LOGS + '/run.log'))
    return 0


def emit(root, kind, plan, current, extra=None):
    """Événement `note` de l'orchestrateur par scripts/lib/events.py s'il existe ; toute erreur est ignorée."""
    lib = os.path.join(root, 'scripts', 'lib')
    if not os.path.isfile(os.path.join(lib, 'events.py')):
        return
    added = lib not in sys.path
    try:
        if added:
            sys.path.append(lib)
        try:
            import events
        finally:
            if added and lib in sys.path:
                sys.path.remove(lib)
        current = current or {}
        events.record(
            root,
            'orchestrator',
            'note',
            dict(
                {'run': kind, 'session': plan['session'], 'plan': plan['path'], 'task': current.get('task'), 'step': current.get('step')},
                **(extra or {}),
            ),
        )
    except Exception:
        pass
