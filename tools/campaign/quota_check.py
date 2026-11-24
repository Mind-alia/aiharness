"""Contrôle de quota des fournisseurs d'un banc avant le premier bras (Python 3.14, bibliothèque standard).

`check` renvoie les couples (fournisseur, raison) des fournisseurs à sec ; liste vide = tout va bien. Codex et
Antigravity sont sondés par un appel léger de leur relais (`scripts/codex.sh`, `scripts/gemini.sh`). Claude n'est
jamais appelé : on lit les relevés de barre d'état et le journal de consommation du projet."""

import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROVIDER_ORDER = ('claude', 'codex', 'antigravity')
RELAYS = {'codex': 'codex.sh', 'antigravity': 'gemini.sh'}
PROBE_TIMEOUT = 180
STATUSLINE = ('claude-statusline.jsonl', 'claude-statusline.jsonl.1')
WINDOWS = ('five_hour', 'seven_day')

# « quota » seulement isolé : pas dans un identifiant, un chemin ou un champ JSON.
QUOTA_TEXT = re.compile(r'rate[ _-]?limit|usage[ _-]?limit|resource_exhausted|too many requests|(?<![\w-])quota(?![\w-])', re.I)


def breaker_agent(config, arm):
    """Exécutant du casseur d'un bras `systeme-*` : une chaîne pour tous, ou un objet `{bras: exécutant}`."""
    chosen = config.get('breaker_agent', 'codex')
    return chosen.get(arm, 'codex') if isinstance(chosen, dict) else chosen


def provider_of(agent):
    return 'claude' if agent.startswith('claude-') else agent


def providers(config):
    """Fournisseurs appelés par le banc, dans l'ordre fixe `claude`, `codex`, `antigravity`."""
    used = set()
    for arm in config['arms']:
        if arm == 'opus-seul':
            used.add('claude')
        elif arm == 'codex-seul':
            used.add('codex')
        elif arm.startswith('systeme-'):
            used.update(('claude', provider_of(arm[8:]), provider_of(breaker_agent(config, arm))))
    return [name for name in PROVIDER_ORDER if name in used]


def check(root, config, env):
    now = datetime.now(UTC)
    dry = []
    for provider in providers(config):
        if provider == 'claude':
            reason = claude_reason(root, env, now)
        else:
            reason = relay_reason(root, env, provider)
        if reason:
            dry.append((provider, reason))
    return dry


def relay_reason(root, env, provider):
    """Raison d'être à sec d'un fournisseur sondé par son relais, ou None si le relais répond."""
    script = Path(root) / 'scripts' / RELAYS[provider]
    if not script.is_file():
        return 'script absent'
    with tempfile.TemporaryDirectory() as directory:
        prompt = Path(directory) / 'consigne.md'
        answer = Path(directory) / 'reponse.md'
        prompt.write_text('Réponds seulement OK.\n')
        try:
            proc = subprocess.run(
                ['bash', 'scripts/' + RELAYS[provider], 'review', str(prompt), str(answer)],
                cwd=str(root),
                env=dict(env, WORKSPACE_ROLE='orchestration'),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=PROBE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return 'délai dépassé (%d s)' % PROBE_TIMEOUT
        if QUOTA_TEXT.search(proc.stdout or ''):
            return 'quota signalé par le relais'
        if proc.returncode:
            return 'contrôle en échec (code %d)' % proc.returncode
        if not answer.is_file() or not answer.read_text().strip():
            return 'réponse vide'
    return None


def claude_reason(root, env, now):
    """Raison d'être à sec de Claude, lue sans appel : barre d'état d'abord, journal ensuite."""
    return statusline_reason(env, now) or journal_reason(root, now)


def console_dir(env):
    return Path(env['WSC_HOME']) if env.get('WSC_HOME') else Path(env.get('HOME') or os.path.expanduser('~')) / '.workspace-console'


def statusline_reason(env, now):
    """Dernière ligne valide des relevés : une fenêtre à 100 % dont la remise à zéro n'est pas passée."""
    latest = None
    for name in STATUSLINE:
        for record in read_lines(console_dir(env) / name):
            stamp = moment(record.get('timestamp'))
            if stamp is not None and (latest is None or stamp > latest[0]):
                latest = (stamp, record)
    if latest is None:
        return None
    for key in WINDOWS:
        window = latest[1].get(key)
        if not isinstance(window, dict):
            continue
        used, resets = window.get('used_percentage'), moment(window.get('resets_at'))
        if type(used) in (int, float) and used >= 100 and resets is not None and resets > now:
            return 'fenêtre %s à %s %%, remise à zéro à %s' % (key, used, resets.isoformat(timespec='minutes'))
    return None


def journal_reason(root, now):
    """Un événement de limite Claude de moins de cinq heures dans le journal du projet."""
    limit = now - timedelta(hours=5)
    for event in read_lines(Path(root) / '.workspace/metrics/consumption.jsonl'):
        quota = event.get('quota')
        if event.get('provider') != 'claude' or not isinstance(quota, dict) or quota.get('limit_reached') is not True:
            continue
        at = moment(event.get('at'))
        if at is not None and at > limit:
            return 'limite signalée le %s' % at.isoformat(timespec='minutes')
    return None


def read_lines(path):
    """Objets JSON lisibles d'un fichier ; lignes illisibles ignorées, fichier absent = rien."""
    records = []
    try:
        with path.open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError:
        pass
    return records


def moment(value):
    """Instant aware d'un horodatage ISO 8601 ou de secondes Unix ; None si illisible."""
    if type(value) in (int, float):
        try:
            return datetime.fromtimestamp(value, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
