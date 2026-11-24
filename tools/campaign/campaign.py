#!/usr/bin/env python3
"""Campagnes séquentielles et reprenables, Python 3.14, bibliothèque standard.

Chaque bras (tâche, bras, répétition) travaille dans son propre worktree, `<projet>/.workspace/campaign/<uid>`, créé
sur la branche `campagne/<tâche>/<bras>/<n>-<k>` (`k` : lignes déjà dans results.jsonl) depuis `start_commit`. Le
checkout principal du projet ne change jamais de branche ni de HEAD ; il doit seulement être propre au départ.
Scripts du circuit, contrats, évaluation et journal de consommation y utilisent le worktree comme dossier courant.

`setup` (facultatif, liste argv ou commande Bash) se lance dans le worktree juste après sa création, avant les
contrats, par exemple `npm ci` : un worktree neuf n'a pas les dépendances. Un `setup` en échec rend le bras `failed`,
jamais `quota`.

Fin du bras : `completed` retire le worktree (`git worktree remove`, sans --force ; s'il est sale il reste,
`arm_worktree_kept`). `failed`, `quota` et `interrupted` le gardent comme preuve, sauf `--discard-failed` qui le
retire avec --force. Chaque ligne porte `arm_worktree` (chemin absolu) et `arm_worktree_kept`. La branche reste.

`breaker_agent` (facultatif) choisit l'exécutant du casseur des bras `systeme-*` : une chaîne pour tous, ou un objet
`{bras: exécutant}` (un bras absent garde `codex`). Chaque ligne porte `breaker_agent`, `null` hors bras système."""

import hashlib
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'template/scripts/lib'))
import workdir
from consumption import token_total

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export
from evaluation import evaluation_target, json_output, score
from quota_check import QUOTA_TEXT, breaker_agent, check


class Quota(Exception):
    pass


BREAKER_AGENTS = ('codex', 'claude-haiku', 'claude-sonnet', 'claude-opus')

# Seuls ces scripts appellent un agent : leur texte de sortie peut signaler un vrai quota.
AGENT_SCRIPTS = ('scripts/delegate.sh', 'scripts/prepare-task.sh', 'scripts/breaker.sh')


def records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class Campaign:
    def __init__(self, config_path, discard_failed=False):
        self.discard_failed = discard_failed
        self.config_path = Path(config_path).resolve()
        self.config = json.loads(self.config_path.read_text())
        self.root = Path(self.config['project']).expanduser()
        if not self.root.is_absolute():
            self.root = self.config_path.parent / self.root
        self.root = self.root.resolve()
        self.output = self.source(self.config.get('output', '.'))
        self.output.mkdir(parents=True, exist_ok=True)
        self.results = self.output / 'results.jsonl'
        self.env = dict(os.environ)
        self.env.pop('WORKSPACE_AGENT_ROOT', None)
        self.work = None  # Worktree du bras en cours ; None entre deux bras.
        self.offset = 0
        self.row = None
        self.validate()

    @property
    def log(self):
        return self.work / '.workspace/metrics/consumption.jsonl'

    def source(self, name):
        path = Path(name).expanduser()
        return path if path.is_absolute() else (self.config_path.parent / path).resolve()

    def validate(self):  # noqa: C901
        names = [task['id'] for task in self.config['tasks']]
        if len(names) != len(set(names)) or not names:
            raise ValueError('Identifiants de tâches absents ou dupliqués')
        for name in names + self.config['arms']:
            if not re.fullmatch('[a-z0-9-]+', name):
                raise ValueError('Identifiant invalide : ' + name)
        agents = ('codex', 'claude-opus', 'claude-sonnet', 'claude-haiku', 'antigravity')
        for arm in self.config['arms']:
            if arm == 'systeme-antigravity' and not (self.root / 'scripts/lib/antigravity_files.py').is_file():
                raise ValueError(
                    'Bras systeme-antigravity refusé : Antigravity ne lance aucune commande sans '
                    'interaction (docs/ANTIGRAVITY-SANS-INTERACTION.md)'
                )
            if arm not in ('opus-seul', 'codex-seul', 'cascade') and not (arm.startswith('systeme-') and arm[8:] in agents):
                raise ValueError('Bras invalide : ' + arm)
        if len(set(self.config['arms'])) != len(self.config['arms']):
            raise ValueError('Bras dupliqués')
        if type(self.config.get('repetitions', 1)) is not int or self.config.get('repetitions', 1) < 1:
            raise ValueError('repetitions doit être positif')
        for task in self.config['tasks']:
            self.source(task['request']).read_text()
            for name in task['evaluation_files']:
                if not self.source(name).is_file():
                    raise ValueError('Test absent : ' + name)
            self.target(task['evaluation_target'])
        chosen = self.config.get('breaker_agent', 'codex')
        if not isinstance(chosen, (str, dict)):
            raise ValueError('breaker_agent doit être un exécutant ou un objet {bras: exécutant}')
        for arm, agent in chosen.items() if isinstance(chosen, dict) else [(None, chosen)]:
            if arm is not None and arm not in self.config['arms']:
                raise ValueError('breaker_agent : bras inconnu : ' + arm)
            if agent == 'antigravity':
                raise ValueError(
                    'breaker_agent antigravity refusé : Antigravity ne lance aucune commande sans '
                    'interaction (docs/ANTIGRAVITY-SANS-INTERACTION.md)'
                )
            if not isinstance(agent, str) or agent not in BREAKER_AGENTS:
                raise ValueError('breaker_agent invalide (%s) : %s' % (', '.join(BREAKER_AGENTS), json.dumps(agent)))
        setup = self.config.get('setup')
        if setup is not None and not (
            isinstance(setup, str) and setup or (isinstance(setup, list) and setup and all(isinstance(a, str) for a in setup))
        ):
            raise ValueError('setup doit être une commande ou une liste argv')

    def breaker_agent(self, arm):
        """Exécutant du casseur d'un bras `systeme-*` ; None pour les autres bras."""
        return breaker_agent(self.config, arm) if arm.startswith('systeme-') else None

    def target(self, name, base=None):
        return evaluation_target(base or self.root, name)

    def raw(self, args, cwd=None):
        return subprocess.run(
            args, cwd=str(cwd or self.work or self.root), env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )

    def task_clone(self, task_id):
        """Clone de la tâche, hors du bras : le chemin enregistré dans son état ; None si l'état manque."""
        try:
            state = json.loads((self.work / '.workspace/orchestration/tasks' / task_id / 'state.json').read_text())
            return workdir.state_worktree(self.work, state)
        except (OSError, ValueError):
            return None

    def close_failed(self, task_id):
        """Abandonne une tâche en échec sans jeter le travail de l'agent (sauf --discard-failed)."""
        close = ['bash', 'scripts/task-workspace.sh', 'close', task_id, '--abandoned']
        worktree = self.task_clone(task_id)
        if self.discard_failed:
            close.append('--force')
        elif worktree is not None:
            top = self.raw(workdir.git_command('rev-parse', '--show-toplevel'), cwd=worktree) if worktree.is_dir() else None
            # Seulement un vrai clone de tâche : jamais le dépôt englobant.
            if top and not top.returncode and Path(top.stdout.strip()).resolve() == worktree.resolve():
                kept = self.raw(workdir.git_command('add', '-A'), cwd=worktree)
                if not kept.returncode and self.raw(workdir.git_command('diff', '--cached', '--quiet'), cwd=worktree).returncode:
                    kept = self.raw(
                        workdir.git_command('commit', '--no-verify', '-q', '-m', 'campagne: travail conservé après échec de ' + task_id),
                        cwd=worktree,
                    )
                    if not kept.returncode:
                        self.row.setdefault('failed_work', []).append(
                            dict(
                                task=task_id,
                                branch='workspace-task/' + task_id,
                                commit=self.raw(workdir.git_command('rev-parse', 'HEAD'), cwd=worktree).stdout.strip(),
                            )
                        )
                if kept.returncode:
                    self.row.setdefault('cleanup_errors', []).append(kept.stdout)
        cleanup = self.raw(close)
        if cleanup.returncode:
            self.row.setdefault('cleanup_errors', []).append(cleanup.stdout)
            if not self.discard_failed and worktree is not None:
                self.row.setdefault('worktree_kept', []).append(str(worktree))

    def git(self, *args, cwd=None):
        proc = self.raw(['git'] + list(args), cwd=cwd)
        if proc.returncode:
            raise RuntimeError(proc.stdout)
        return proc.stdout.strip()

    def events(self):
        if self.work is None or not self.log.exists():
            return []
        with self.log.open('rb') as stream:
            stream.seek(self.offset)
            events = []
            for line in stream:
                try:
                    event = json.loads(line)
                    if isinstance(event, dict):
                        events.append(event)
                except ValueError:
                    pass  # Journal append-only : ignorer une ligne partielle.
            return events

    def command(self, args, check=True, quota=True):
        proc = self.raw(args)
        self.row['commands'].append(dict(args=args, exit=proc.returncode, output=proc.stdout))
        if quota:
            if any(event.get('quota', {}).get('limit_reached') is True for event in self.events()):
                raise Quota('Limite signalée dans consumption.jsonl')
            # Seul un script qui appelle un agent peut signaler un quota par son texte.
            if args[1:2] and args[1] in AGENT_SCRIPTS and proc.returncode and QUOTA_TEXT.search(proc.stdout):
                raise Quota(proc.stdout)
        if check and proc.returncode:
            raise RuntimeError('Commande échouée (%s) : %s' % (proc.returncode, proc.stdout))
        return proc

    def script(self, name, *args, **kwargs):
        return self.command(['bash', 'scripts/' + name + '.sh'] + list(args), **kwargs)

    def cascade_attempts(self, task_id):
        """Somme des tentatives des étages de cascade.json ; 1 si le fichier manque ou est illisible."""
        try:
            data = json.loads((self.work / '.workspace/run/delegate' / task_id / 'cascade.json').read_text())
            return sum(stage['attempts'] for stage in data['stages'])
        except (OSError, ValueError, KeyError, TypeError):
            return 1

    def delegate(self, task_id, system, cascade=False):
        try:
            if cascade:
                proc = self.script('delegate', '--cascade', 'haiku,sonnet,opus', 'tasks/' + task_id + '.md', check=False)
            else:
                proc = self.script('delegate', 'tasks/' + task_id + '.md', check=False)
        finally:
            # Même une tentative interrompue par quota a consommé un appel.
            command = self.row['commands'][-1] if self.row['commands'] else {}
            if cascade:
                self.row['attempts'] += self.cascade_attempts(task_id)
            else:
                status = json_output(command.get('output', ''))
                self.row['attempts'] += int(status.get('attempts', 1))
        if system and proc.returncode:
            raise RuntimeError('Délégation échouée : ' + proc.stdout)

    def contracts(self, task, arm, uid):
        system = arm.startswith('systeme-') or arm == 'cascade'
        agent = ('claude-haiku' if arm == 'cascade' else arm[8:]) if system else ('claude-opus' if arm == 'opus-seul' else 'codex')
        if system:
            proc = self.script('prepare-task', str(self.source(task['request'])), uid, '--executor', agent, '--planner', 'claude-opus')
            ids = proc.stdout.strip().splitlines()
            if not ids or any(not re.fullmatch(re.escape(uid) + r'(?:-[1-9][0-9]*)?', x) for x in ids):
                raise ValueError('Sortie prepare-task invalide')
            if len(set(ids)) != len(ids):
                raise ValueError('Identifiants prepare-task dupliqués')
        else:
            ids = [uid]
            allowed = task.get('allowed')
            if not allowed:
                # Même périmètre de code pour tous les agents seuls ; infrastructure exclue.
                excluded = {
                    '.git',
                    '.workspace',
                    'scripts',
                    'tasks',
                    'docs',
                    'AGENTS.md',
                    'CLAUDE.md',
                    'GEMINI.md',
                    'project.profile',
                    '_bmad',
                    '.claude',
                    '.codex',
                    'node_modules',
                }
                allowed = [
                    p.name + '/**' if p.is_dir() else p.name
                    for p in sorted(self.work.iterdir())
                    if p.name not in excluded and not p.name.startswith('.')
                ]
            directory = self.work / 'tasks'
            directory.mkdir(exist_ok=True)
            path = directory / (uid + '.md')
            if path.exists():
                raise ValueError('Contrat déjà présent')
            header = ['---', 'id: ' + uid, 'agent: ' + agent, 'mode: write', 'isolation: exchange', 'allowed:']
            header += ['  - ' + json.dumps(item) for item in allowed]
            header += [
                'verify: "true"',
                'verify_profile: off',
                'verify_lint: off',
                'attempts: 1',
                '---',
                self.source(task['request']).read_text(),
            ]
            path.write_text('\n'.join(header) + '\n')
        # Les contrats doivent être suivis : mission.prepare exige un arbre propre.
        for task_id in ids:
            path = self.work / 'tasks' / (task_id + '.md')
            if not path.is_file() or path.is_symlink():
                raise ValueError('Contrat créé absent ou symbolique')
            self.git('add', '--', str(path))
        if self.git('diff', '--cached', '--name-only'):
            self.git('commit', '-m', 'chore: contrats de campagne ' + uid)
        return ids

    def evaluate(self, task):
        gate = self.script('gate', check=False, quota=False)
        failures = [line for line in gate.stdout.splitlines() if re.search(r'FAIL|not ok|✗|×|❯', line)]
        self.row['gate'] = dict(
            exit=gate.returncode,
            output=gate.stdout,
            statusline_binary_only=bool(gate.returncode and failures and all('statusline-binary' in line for line in failures)),
        )
        target = self.target(task['evaluation_target'], self.work)
        existed = target.exists()
        copied = []
        try:
            target.mkdir(parents=True, exist_ok=True)
            for source_name in task['evaluation_files']:
                source = self.source(source_name)
                dest = target / source.name
                if dest.exists() or dest.is_symlink():
                    raise ValueError('Refus d’écraser un fichier d’évaluation : ' + str(dest))
                shutil.copyfile(str(source), str(dest))
                copied.append(dest)
            command = task['evaluation_command']
            proc = self.command(command if isinstance(command, list) else ['bash', '-c', command], check=False, quota=False)
            passed, total = score(proc.stdout, proc.returncode)
            self.row['evaluation'] = dict(exit=proc.returncode, passed=passed, total=total, output=proc.stdout)
        finally:
            for path in copied:
                path.unlink()
            if not existed and target.exists():
                # Retirer uniquement les dossiers vides créés par cette exécution.
                current = target
                while current != self.work:
                    try:
                        current.rmdir()
                    except OSError:
                        break
                    current = current.parent

    def write(self):
        with self.results.open('a') as stream:
            stream.write(json.dumps(self.row, ensure_ascii=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        rows = records(self.results)
        lines = [
            '| Tâche | Bras | Rép. | État | Évaluation | Porte (statusline seul) | Failles | Tentatives | Durée s | Tokens fournisseur/rôle (compteurs natifs et total normalisé) |',
            '|---|---|---|---|---|---|---|---|---|---|',
        ]
        for row in rows:
            evaluation = row.get('evaluation', {})
            gate = row.get('gate', {})
            tokens = []
            for provider, roles in sorted(row['consumption'].items()):
                for role, counts in sorted(roles.items()):

                    def shown(key, counts=counts):
                        value = counts.get(key)
                        return str(value) if value is not None else 'inconnu'

                    tokens.append(
                        '%s/%s entrée=%s sortie=%s cache lu=%s créé=%s total=%s inconnus=%s'
                        % (
                            provider,
                            role,
                            shown('input'),
                            shown('output'),
                            shown('cached_input'),
                            shown('cache_creation'),
                            shown('total_tokens'),
                            shown('unknown_token_calls'),
                        )
                    )
            lines.append(
                '| %s | %s | %s | %s | %s/%s | %s (%s) | %s | %s | %.2f | %s |'
                % (
                    row['task'],
                    row['arm'],
                    row['repetition'],
                    row['status'],
                    evaluation.get('passed', '?'),
                    evaluation.get('total', '?'),
                    gate.get('exit', '?'),
                    gate.get('statusline_binary_only', False),
                    row['breaker_retained'],
                    row['attempts'],
                    row['duration_seconds'],
                    ', '.join(tokens),
                )
            )
        temporary = self.output / 'results.md.tmp'
        temporary.write_text('\n'.join(lines) + '\n')
        temporary.replace(self.output / 'results.md')

    def release(self, worktree):
        """Retire le worktree d'un bras `completed` s'il est propre ; les autres états le gardent (--discard-failed : retiré)."""
        self.work = None
        completed = self.row['status'] == 'completed'
        kept = worktree.exists()
        if kept and (completed or self.discard_failed):
            removed = self.raw(['git', 'worktree', 'remove'] + ([] if completed else ['--force']) + [str(worktree)], cwd=self.root)
            kept = bool(removed.returncode)
            if kept and not completed:
                self.row.setdefault('cleanup_errors', []).append(removed.stdout)
        self.row.update(arm_worktree=str(worktree), arm_worktree_kept=kept)

    def run(self):  # noqa: C901, PLR0915
        if self.git('status', '--porcelain'):
            raise ValueError('Le projet doit être propre avant la campagne')
        commit = self.git('rev-parse', '--verify', self.config['start_commit'] + '^{commit}')
        fingerprint = hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest()
        previous = records(self.results)
        if any(r['config_hash'] != fingerprint for r in previous):
            raise ValueError('Configuration différente des résultats existants')
        seed = self.config.get('seed', previous[0]['seed'] if previous else random.SystemRandom().randrange(2**63))
        schedule = [
            (task, arm, n)
            for task in self.config['tasks']
            for arm in self.config['arms']
            for n in range(1, self.config.get('repetitions', 1) + 1)
        ]
        random.Random(seed).shuffle(schedule)
        done = {(r['task'], r['arm'], r['repetition']) for r in previous if r['status'] != 'quota' and r['status'] != 'interrupted'}
        for index, (task, arm, n) in enumerate(schedule):
            if (task['id'], arm, n) in done:
                continue
            started = time.monotonic()
            k = len(records(self.results))
            branch = 'campagne/%s/%s/%d-%d' % (task['id'], arm, n, k)
            uid = 'camp-%s-%s-%d-%d' % (task['id'], arm, n, k)
            worktree = self.root / '.workspace/campaign' / uid
            self.row = dict(
                task=task['id'],
                arm=arm,
                repetition=n,
                branch=branch,
                seed=seed,
                order=index,
                config_hash=fingerprint,
                start_commit=commit,
                breaker_agent=self.breaker_agent(arm),
                status='completed',
                attempts=0,
                breaker_retained=0,
                commands=[],
                consumption={},
            )
            active = []
            try:
                self.git('worktree', 'add', '-b', branch, str(worktree), commit, cwd=self.root)
                self.work = worktree
                self.offset = self.log.stat().st_size if self.log.exists() else 0
                setup = self.config.get('setup')
                if setup is not None:
                    self.command(setup if isinstance(setup, list) else ['bash', '-c', setup], quota=False)
                ids = self.contracts(task, arm, uid)
                for task_id in ids:
                    self.script('task-workspace', 'prepare', 'tasks/' + task_id + '.md')
                    active.append(task_id)
                    system = arm.startswith('systeme-')
                    cascade = arm == 'cascade'
                    self.delegate(task_id, system or cascade, cascade)
                    if system:
                        self.script('breaker', task_id, '--promote', '--agent', self.row['breaker_agent'])
                        report = json.loads((self.work / '.workspace/breaker' / task_id / 'report.json').read_text())
                        retained = len(report['retained'])
                        self.row['breaker_retained'] += retained
                        if retained:
                            self.delegate(task_id, True)
                    flags = [] if system or cascade else ['--no-verify-again', '--no-integration']
                    self.script('task-workspace', 'merge', task_id, *flags)
                    self.script('task-workspace', 'close', task_id, '--merged')
                    active.remove(task_id)
                self.evaluate(task)
            except Quota as error:
                self.row.update(status='quota', error=str(error))
            except KeyboardInterrupt:
                self.row.update(status='interrupted', error='Interruption')
            except (OSError, ValueError, RuntimeError) as error:
                self.row.update(status='failed', error=str(error))
            finally:
                for task_id in active:
                    self.close_failed(task_id)
                events = self.events()
                self.row['consumption_events'] = events
                for event in events:
                    provider = event.get('provider', 'unknown')
                    counts = (
                        self.row['consumption']
                        .setdefault(provider, {})
                        .setdefault(
                            event.get('role', 'unknown'),
                            dict(input=0, output=0, cached_input=0, cache_creation=0, reasoning=0, total_tokens=0, unknown_token_calls=0),
                        )
                    )
                    native = event.get('tokens')
                    native = native if isinstance(native, dict) else {}
                    for key in ('input', 'output', 'cached_input', 'cache_creation', 'reasoning'):
                        value = native.get(key)
                        if counts[key] is None or type(value) is not int or value < 0:
                            counts[key] = None
                        else:
                            counts[key] += value
                    total = None if event.get('tokens_complete') is False else token_total(native, provider)
                    if total is None:
                        counts['unknown_token_calls'] += 1
                    if total is None or counts['total_tokens'] is None:
                        counts['total_tokens'] = None
                    else:
                        counts['total_tokens'] += total
                self.row['duration_seconds'] = time.monotonic() - started
                try:
                    self.row['journal'] = export.journal_summary(worktree)
                    self.release(worktree)
                finally:
                    self.write()
            if self.row['status'] in ('quota', 'interrupted'):
                return 75 if self.row['status'] == 'quota' else 130
        return 1 if any(r['status'] == 'failed' for r in records(self.results)) else 0


def main():  # noqa: C901
    args = sys.argv[1:]
    if args[:1] == ['export']:
        if len(args) != 2:
            print('usage: python3 tools/campaign/campaign.py export <dossier-resultats>', file=sys.stderr)
            return 64
        try:
            csv_path, json_path = export.export(args[1])
        except (OSError, ValueError) as error:
            print('campaign export: ' + str(error), file=sys.stderr)
            return 2
        print(csv_path)
        print(json_path)
        return 0
    if args[:1] == ['etalons']:
        import etalons

        return etalons.main(args[1:])
    discard = '--discard-failed' in args
    ignore_quota = '--ignore-quota' in args
    args = [a for a in args if a not in ('--discard-failed', '--ignore-quota')]
    if len(args) != 1 or args[0].startswith('--'):
        print('usage: python3 tools/campaign/campaign.py <config.json> [--discard-failed] [--ignore-quota]', file=sys.stderr)
        return 64

    def interrupted(signum, frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, interrupted)
    try:
        campaign = Campaign(args[0], discard)
        if not ignore_quota:
            # Avant tout bras : rien n'est créé si un fournisseur du banc est à sec.
            dry = check(campaign.root, campaign.config, campaign.env)
            for provider, reason in dry:
                print(
                    'campaign: %s à sec : %s ; aucun bras lancé, relancer plus tard ou avec --ignore-quota' % (provider, reason),
                    file=sys.stderr,
                )
            if dry:
                return 75
        return campaign.run()
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print('campaign: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
