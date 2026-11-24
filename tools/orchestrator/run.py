"""Orchestrateur programme (T14) : exécute un plan de fiches, reprend après `kill -9`, s'arrête là où le propriétaire décide.

Usage : python3 tools/orchestrator/run.py <plan> [--root <projet>] [--detach] [--session S] [--by B] [--model M]
Codes : 0 terminé, 1 fiche en échec, 2 erreur, 3 verrou ou passage tenu, 4 en attente du propriétaire (fusion à valider,
proposition à accepter ou question d'exécutant), 64 usage ou plan, 75 quota, 143 SIGTERM.

Par vague du plan : préparation de chaque fiche à son tour, délégations ensemble quand la vague en compte plusieurs,
puis pour chacune vérification sur l'hôte, relecture ciblée si le plan la demande, et arrêt « prêt à fusionner » tant
que le propriétaire n'a pas écrit `- fusion validée: <id>` dans le plan. Un premier échec est diagnostiqué par Sonnet avant la
reprise ; un second arrête la fiche. Aucun modèle d'orchestration n'est ouvert : seuls le diagnostic et la relecture
appellent un modèle (review.py).
"""

import copy
import os
import signal
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True

import base_replay  # noqa: E402
import journal  # noqa: E402
import notices  # noqa: E402
import plan as planning  # noqa: E402
import quality  # noqa: E402
import report  # noqa: E402
import resume  # noqa: E402
import review  # noqa: E402
import steps  # noqa: E402
import triage  # noqa: E402

EXIT_FAILED, EXIT_ERROR, EXIT_HELD, EXIT_WAITING, EXIT_USAGE, EXIT_QUOTA, EXIT_TERM = 1, 2, 3, 4, 64, 75, 143
PRE_STEPS = ('prepare', 'delegate', 'verify')
# Issues d'une fiche qui arrêtent le plan tout de suite, et celles qui attendent le propriétaire (arrêt en fin de vague).
STOPS = ('quota', 'question', triage.ENVIRONMENT, triage.FICHE, triage.DIAGNOSIS)
WAITING = ('question', 'validation', 'acceptance', triage.ENVIRONMENT, triage.FICHE, triage.DIAGNOSIS)
LABELS = {
    triage.DIAGNOSIS: "le diagnostic ne met pas l'exécutant en cause : ni reprise ni tentative comptée ; rejouée à la relance",
    triage.ENVIRONMENT: 'vérification rouge sur sa base aussi (environnement), aucune tentative comptée ; rejouée telle quelle à la relance',
    triage.FICHE: 'test existant hors périmètre rouge (fiche), aucune tentative comptée ; question dans docs/QUESTIONS.md',
    'validation': 'prête à fusionner, attend la ligne « - fusion validée: {} » dans le plan',
    'acceptance': 'proposition prête, attend la ligne « - proposition acceptée: {} » dans le plan',
}


class Terminated(BaseException):
    """SIGTERM reçu : le journal passe à `stopped`, l'enfant n'est pas tué."""


class Runner:
    """Un passage du plan : tient le journal et enchaîne les étapes de chaque fiche."""

    def __init__(self, root, plan, data):
        self.root, self.plan, self.data = root, plan, data
        self.beat = steps.Beat(root)
        # Les délégations d'une vague écrivent le journal depuis plusieurs fils.
        self.lock = threading.RLock()
        self.parallel = False
        # T117 : (code, tests rouges) de la vérification sur chaque base de tâche, une fois par passage.
        self.baselines = {}
        self.data.setdefault('children', {})

    # --- journal -------------------------------------------------------------------------------

    def save(self):
        with self.lock:
            self.data['updated_at'] = journal.now()
            journal.save(self.root, self.data)

    def decide(self, text):
        with self.lock:
            self.data['decisions'].append(text)
            print(text, flush=True)
            self.save()

    def record(self, task_id, step, status, **extra):
        with self.lock:
            self.data['tasks'][task_id]['steps'][step] = dict({'status': status, 'at': journal.now()}, **extra)
            self.save()

    def keep(self, task_id, step, status):
        """Marque l'étape sans rien lancer, sauf si le journal la donne déjà faite ou sautée."""
        if self.data['tasks'][task_id]['steps'].get(step, {}).get('status') not in ('done', 'skipped'):
            self.record(task_id, step, status)

    def at(self, task_id, step):
        with self.lock:
            self.data['current'] = {'task': task_id, 'step': step}
            self.save()
            if task_id is not None:
                entry = self.data['tasks'][task_id]
                state = journal.state_of(entry)
                if state == 'queued':
                    self.move(task_id, 'in_progress', step)
                elif state == 'awaiting_user':
                    self.move(task_id, 'in_progress', journal.resume_reason(entry))

    def move(self, task_id, to, reason):
        """Seule voie d'un changement d'état de fiche : transition, sauvegarde et événement, seulement si l'état change."""
        with self.lock:
            if not journal.transition(self.data, task_id, to, reason):
                return
            last = self.data['tasks'][task_id]['transitions'][-1]
            self.save()
            extra = {'run': 'state', 'task': task_id, 'from': last['from'], 'to': to, 'reason': reason}
            journal.emit(self.root, 'state', self.plan, {'task': task_id}, extra)

    def started(self, task_id, reason):
        """Une fiche encore `queued` qui atteint un arrêt sans étape annoncée passe d'abord `in_progress`."""
        if journal.state_of(self.data['tasks'][task_id]) == 'queued':
            self.move(task_id, 'in_progress', reason)

    def move_all(self, source, to, reason):
        for task_id in self.data['order']:
            if journal.state_of(self.data['tasks'][task_id]) == source:
                self.move(task_id, to, reason)

    def track(self, task_id, step, call):
        """Exécute `call(log, started)` comme étape suivie : `current` avant, l'enfant pendant, rien après (règle 7)."""
        self.at(task_id, step)
        log = journal.logs(self.root) / f'{task_id}-{step}.log'

        def started(pid):
            child = {'pid': pid, 'task': task_id, 'step': step, 'started_at': journal.now(), 'log': log.relative_to(self.root).as_posix()}
            with self.lock:
                self.data['children'][task_id] = child
                if not self.parallel:
                    self.data['child'] = child
                self.save()

        code = call(log, started)
        with self.lock:
            self.data['children'].pop(task_id, None)
            if not self.parallel:
                self.data['child'] = None
        return code

    def command(self, task_id, step, argv, extra=None):
        env = steps.circuit_env(self.plan['image'], extra)
        return self.track(task_id, step, lambda log, started: steps.spawn(argv, log, env, self.root, started))

    # --- reprise -------------------------------------------------------------------------------

    def wait_orphan(self):
        """Règle 8 : attend, sans les tuer, les enfants que le passage précédent a laissés."""
        orphans = list((self.data.get('children') or {}).values())
        if self.data.get('child') and self.data['child'] not in orphans:
            orphans.append(self.data['child'])
        for child in orphans:
            self.wait_child(child)
        self.data['child'] = None
        self.data['children'] = {}
        self.save()

    def wait_child(self, child):
        name = 'étape {} de {}'.format(child.get('step'), child.get('task'))
        if steps.child_alive(child.get('pid')):
            print('{} encore en cours (pid {}) : j\'attends sa fin'.format(name, child.get('pid')), flush=True)
            while steps.child_alive(child.get('pid')):
                time.sleep(steps.seconds('ORCHESTRATOR_POLL_SECONDS', steps.POLL_DEFAULT))
            self.decide(
                '{} : le processus enfant {} de l\'ancien passage a fini seul, attendu sans être tué'.format(name, child.get('pid'))
            )
        else:
            self.decide(
                '{} : le processus enfant {} de l\'ancien passage n\'existe plus, état relu dans le circuit'.format(name, child.get('pid'))
            )

    # --- plan et vagues ------------------------------------------------------------------------

    def run_plan(self):
        for wave in planning.waves(self.plan):
            stop = self.run_wave(wave)
            if stop is not None:
                return self.finish(stop)
        return self.finish(None)

    def pending(self, items):
        return [item for item in items if item['id'] not in self.data['done'] and item['id'] not in self.data['failed']]

    def run_wave(self, wave):
        """Raison d'arrêt du plan (quota, question, attente du propriétaire) ou None pour passer à la vague suivante."""
        if len(self.pending(wave)) > 1:
            stop = self.delegate_together(self.pending(wave))
            if stop is not None:
                return stop
        waiting = None
        for item in self.pending(wave):
            outcome = 'retry'
            while outcome == 'retry':
                outcome = self.attempt(item, self.data['tasks'][item['id']])
            if outcome in STOPS:
                return outcome
            if outcome in WAITING:
                waiting = waiting or outcome
        return waiting

    def delegate_together(self, items):
        """Vague de plusieurs fiches : chaque préparation à son tour (verrou du registre), puis les délégations ensemble.

        Aucune fusion ni recopie du miroir n'a lieu pendant ces délégations : elles casseraient leurs sceaux.
        """
        launch = []
        for item in items:
            entry = self.data['tasks'][item['id']]
            state, outcome = self.prepared(item, entry)
            if outcome in STOPS:
                return outcome
            if outcome is None and self.to_delegate(item, entry, state):
                launch.append((item, entry))
        if len(launch) < 2:
            return None
        names = ', '.join(item['id'] for item, _ in launch)
        self.decide('vague {} : {} délégations lancées ensemble ({})'.format(launch[0][0]['wave'], len(launch), names))
        errors = []
        threads = [threading.Thread(target=self.wave_launch, args=(item, entry, errors), daemon=True) for item, entry in launch]
        self.parallel = True
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                while thread.is_alive():
                    thread.join(0.5)
        finally:
            self.parallel = False
        if errors:
            raise RuntimeError('délégation de la vague : ' + '; '.join(errors))
        return None

    def to_delegate(self, item, entry, state):
        status = steps.delegate_status(self.root, item['id'])
        return not steps.delegation_ok(state, status, entry.get('since')) and not self.open_question(state, status, item['id'])

    def wave_launch(self, item, entry, errors):
        try:
            with self.lock:
                entry['wave_started'] = journal.precise()
                self.save()
            code = self.command(item['id'], 'delegate', steps.delegate_cmd(item['fiche']), self.delegate_env(item, entry))
            self.record(item['id'], 'delegate', 'ran', exit=code)
        except Exception as error:
            errors.append('{} : {}'.format(item['id'], error))

    # --- fiches --------------------------------------------------------------------------------

    def give_up(self, task_id, entry, why):
        if entry['failures'] >= 2:
            self.release(task_id, entry)
        entry['reason'] = why
        self.data['failed'].append(task_id)
        self.data['current'] = None
        self.started(task_id, 'échec')
        self.move(task_id, 'failed', why)
        self.decide(f'fiche {task_id} en échec : {why}')
        return 'failed'

    def wait_for_owner(self, task_id, entry, reason, notice):
        entry['notice'] = notice
        self.data['current'] = None
        self.started(task_id, reason)
        self.move(task_id, 'awaiting_user', reason)
        self.decide(f'fiche {task_id} : {LABELS[reason].format(task_id)} ; avis `{notice}`')
        return reason

    def failure(self, item, entry, why, step):
        """Règle 14 : première erreur, diagnostic puis abandon et reprise, sauf diagnostic qui accuse le circuit, la fiche
        ou l'environnement (arrêt sans rien compter, E15) ; seconde erreur, fiche en échec."""
        entry['failures'] += 1
        if entry['failures'] >= 2:
            return self.give_up(item['id'], entry, why)
        entry['reason'] = why
        entry['steps'].pop('close', None)
        if (cause := self.diagnose(item, entry, why, step)) != triage.EXECUTOR:
            return triage.stop_on_diagnosis(self, item, entry, cause)
        self.decide('fiche {} : {} ; première erreur, tâche abandonnée puis reprise une fois'.format(item['id'], why))
        return self.abandon(item, entry)

    def diagnose(self, item, entry, why, step):
        """T14 B1 : Sonnet lit les journaux de l'échec ; son diagnostic entre dans la consigne de la reprise. Rend la cause
        de sa ligne `CAUSE:` (`exécutant` sans diagnostic ou si le modèle échoue)."""
        if not self.plan['diagnostic']:
            return triage.EXECUTOR
        task_id = item['id']
        self.at(task_id, 'diagnose')
        path, error, cause = review.diagnose(self.root, item, why, journal.logs(self.root) / f'{task_id}-{step}.log')
        if path is None:
            self.decide(f'fiche {task_id} : diagnostic impossible ({error}), reprise sans diagnostic')
            return cause
        entry['stage_context'] = path
        who = 'de Sonnet' if error is None else f'du programme (Sonnet : {error}, tests rouges seuls)'
        self.decide(f'fiche {task_id} : diagnostic {who} écrit dans `{path}`, cause {cause}')
        return cause

    def abandon(self, item, entry):
        task_id = item['id']
        code = self.command(task_id, 'close', steps.close_cmd(task_id))
        if code != 0:
            return self.give_up(task_id, entry, '{} ; clôture : code {}'.format(entry['reason'], code))
        for name in PRE_STEPS:
            entry['steps'].pop(name, None)
        # Le statut de la délégation abandonnée ne vaut jamais pour la reprise, même lancé dans la seconde du `prepare`.
        entry['since'] = journal.precise()
        self.record(task_id, 'close', 'done', exit=code)
        return 'retry'

    def release(self, task_id, entry):
        """T120 : fiche arrêtée après deux erreurs ; son diff complet est exporté, puis son clone fermé (preuves gardées dans
        `.workspace/run/delegate/<id>/`, branche gardée) pour libérer son périmètre. Diff impossible : clone gardé."""
        if (steps.task_state(self.root, task_id) or {}).get('status') not in ('prepared', 'running'):
            return
        self.at(task_id, 'export')
        try:
            entry['patch'] = steps.export_patch(self.root, task_id, journal.logs(self.root) / f'{task_id}-export.log')
        except (OSError, ValueError) as error:
            self.decide(f'fiche {task_id} : diff non exporté ({error}), clone gardé')
            return
        code = self.command(task_id, 'close', steps.close_cmd(task_id))
        self.record(task_id, 'close', 'done' if code == 0 else 'failed', exit=code)
        self.decide(f"fiche {task_id} : diff complet dans `{entry['patch']}`, clone fermé (code {code}), preuves gardées")

    def attempt(self, item, entry):
        state, outcome = self.prepared(item, entry)
        if outcome is not None:
            return outcome
        for step in (self.delegate, self.verify, self.review):
            outcome = step(item, entry, state)
            if outcome is not None:
                return outcome
        return self.merge(item, entry)

    def prepared(self, item, entry):
        """(état de la tâche, None) quand elle est préparée, sinon (état, issue de la fiche)."""
        task_id, state = item['id'], steps.task_state(self.root, item['id'])
        status = state.get('status') if state else None
        if status == 'merged':
            for name in PRE_STEPS + ('merge',):
                self.keep(task_id, name, 'skipped' if name == 'verify' and self.plan['verify'] is None else 'done')
            entry['merge_commit'] = entry['merge_commit'] or steps.merge_commit(self.root, task_id)
            return state, self.mirror(task_id, entry)
        first_failure = entry['failures'] == 1 and not entry.get('correction')
        if first_failure and entry['steps'].get('close', {}).get('status') != 'done' and status in ('prepared', 'running'):
            return state, self.abandon(item, entry)
        if status in ('prepared', 'running'):
            self.keep(task_id, 'prepare', 'skipped')
            return state, None
        if item.get('demande'):
            outcome = self.from_demande(item, entry)
            if outcome is not None:
                return state, outcome
        code = self.command(task_id, 'prepare', steps.prepare_cmd(item['fiche'], status == 'abandoned'))
        state = steps.task_state(self.root, task_id)
        if code != 0 or not state or state.get('status') != 'prepared':
            self.record(task_id, 'prepare', 'failed', exit=code)
            return state, self.give_up(task_id, entry, f'préparation : code {code}')
        self.record(task_id, 'prepare', 'done', exit=code)
        return state, None

    def from_demande(self, item, entry):
        """T14 B4 : demande, proposition du planificateur, plan-check, accord du propriétaire, puis fiche du circuit."""
        task_id = item['id']
        if entry['steps'].get('accept', {}).get('status') == 'done':
            return None
        folder = notices.proposal_dir(self.root, task_id)
        if not (folder / 'task.md').is_file():
            executor = item['executor'] or 'claude-sonnet'
            code = self.command(task_id, 'propose', steps.propose_cmd(item['demande'], task_id, executor))
            if code != 0 or not (folder / 'task.md').is_file():
                self.record(task_id, 'propose', 'failed', exit=code)
                return self.give_up(task_id, entry, f'proposition depuis la demande : code {code}')
            self.record(task_id, 'propose', 'done', exit=code)
        check_log = journal.logs(self.root) / f'{task_id}-plan_check.log'
        if entry['steps'].get('plan_check', {}).get('status') != 'done':
            notices.stage_tests(self.root, task_id)
            code = self.command(task_id, 'plan_check', steps.plan_check_cmd(f'.workspace/proposals/{task_id}/task.md'))
            if code != 0:
                self.record(task_id, 'plan_check', 'failed', exit=code)
                return self.give_up(task_id, entry, f'plan-check de la proposition : verdict « à reprendre » (code {code})')
            self.record(task_id, 'plan_check', 'done', exit=code)
        if task_id not in planning.decisions(self.root, self.plan['path'])['accepted']:
            notice = notices.proposal(self.root, self.plan, item, check_log.relative_to(self.root).as_posix())
            return self.wait_for_owner(task_id, entry, 'acceptance', notice)
        self.at(task_id, 'accept')
        try:
            notices.accept(self.root, item)
        except (OSError, ValueError) as error:
            return self.give_up(task_id, entry, f'proposition acceptée non copiée : {error}')
        self.record(task_id, 'accept', 'done')
        return None

    def delegate_env(self, item, entry):
        """Exécutant imposé (plan, ou correcteur d'une ronde de correction) et contexte d'étape (diagnostic, corrections)."""
        extra = {}
        executor = entry.get('override') or item.get('executor')
        if executor:
            extra['WORKSPACE_AGENT_OVERRIDE'] = executor
        if entry.get('stage_context'):
            extra['WORKSPACE_STAGE_CONTEXT'] = str(journal.inside(self.root, entry['stage_context']))
        return extra

    def open_question(self, state, status, task_id):
        """La dernière délégation de cette préparation attend une réponse que le propriétaire n'a pas encore donnée."""
        if not status or status.get('outcome') != 'waiting_answer' or not steps.ran_after(state, status):
            return False
        return bool(notices.unanswered(self.root, task_id, status.get('waiting_questions') or []))

    def delegate(self, item, entry, state):
        """None si la délégation est réussie, sinon l'issue de la fiche (règle 11)."""
        task_id = item['id']
        status = steps.delegate_status(self.root, task_id)
        if steps.delegation_ok(state, status, entry.get('since')):
            entry['agent'] = (status or {}).get('agent') or entry['agent']
            self.keep(task_id, 'delegate', 'done')
            return None
        start = entry.pop('wave_started', None)
        if start is not None and steps.ran_since(status, start):
            code = (entry['steps'].get('delegate') or {}).get('exit')
        else:
            if self.open_question(state, status, task_id):
                return self.question(task_id, status)
            start = journal.precise()
            code = self.command(task_id, 'delegate', steps.delegate_cmd(item['fiche']), self.delegate_env(item, entry))
        return self.delegation_result(item, entry, start, code)

    def delegation_result(self, item, entry, start, code):
        task_id = item['id']
        status, state = steps.delegate_status(self.root, task_id), steps.task_state(self.root, task_id)
        entry['agent'] = (status or {}).get('agent') or entry['agent']
        if steps.quota_since(self.root, task_id, start):
            self.record(task_id, 'delegate', 'failed', exit=code, outcome='quota')
            self.decide(f'quota atteint pendant la délégation de {task_id} : arrêt propre du plan')
            return 'quota'
        if steps.delegation_ok(state, status, entry.get('since')):
            self.record(task_id, 'delegate', 'done', exit=code)
            entry.update(override=None, stage_context=None)
            return None
        result = (status or {}).get('outcome')
        self.record(task_id, 'delegate', 'failed', exit=code, outcome=result)
        if result == 'waiting_answer':
            return self.question(task_id, status)
        outcome = base_replay.guard(self, item, entry) if result == 'verify_failed' else None
        if outcome is not None:
            return outcome
        return self.failure(item, entry, 'délégation : {}'.format(result or f'code {code}'), 'delegate')

    def question(self, task_id, status):
        """T14 B2 : la tâche attend une réponse du propriétaire ; le plan s'arrête après l'étape en cours, sans aucune fusion."""
        numbers = notices.unanswered(self.root, task_id, (status or {}).get('waiting_questions') or [])
        added = notices.questions(self.root, task_id, numbers)
        self.data['current'] = None
        self.started(task_id, 'question')
        self.move(task_id, 'awaiting_user', 'question')
        self.decide(
            'fiche {} : question(s) {} en attente de réponse ({} ligne(s) ajoutée(s) à docs/QUESTIONS.md) ; le plan '
            "s'arrête sans fusion, la tâche reprend à la relance une fois la réponse donnée".format(
                task_id, ', '.join(numbers) or '?', added
            )
        )
        return 'question'

    def verify(self, item, entry, _state):
        """None si la vérification passe (ou est sautée), sinon l'issue de la fiche (règle 12)."""
        task_id = item['id']
        if self.plan['verify'] is None:
            self.keep(task_id, 'verify', 'skipped')
            return None
        if entry['steps'].get('verify', {}).get('status') == 'done':
            return None
        code, output = self.run_verify(task_id)
        if code != 0 and quality.category(output) == quality.FORMAT and self.format_task(task_id):
            code, output = self.run_verify(task_id)
        if code == 0:
            self.record(task_id, 'verify', 'done', exit=code)
            return None
        kind = quality.category(output)
        # T117 : un échec de qualité connu juge le code de la tâche (outil absent : test sauté, jamais rouge) ; tout autre
        # est classé d'après la même vérification rejouée sur la base.
        category, detail = (triage.EXECUTOR, None) if kind else self.triage(item, entry, output)
        self.record(task_id, 'verify', 'failed', exit=code, category=category)
        if category != triage.EXECUTOR:
            notice = triage.notice(self.root, task_id, category, detail, item['fiche'])
            notices.blocked(self.root, task_id, category, notice)
            return self.wait_for_owner(task_id, entry, category, notice)
        if kind is not None:
            return self.quality_round(item, entry, kind, output)
        return self.failure(item, entry, f'vérification : code {code}', 'verify')

    def triage(self, item, entry, output):
        """T117 : (catégorie, détail) d'une vérification rouge, d'après la même vérification rejouée sur la base de la tâche."""
        task_id, sha = item['id'], steps.base_commit(self.root, item['id'])
        if sha is not None and sha not in self.baselines:
            log = journal.logs(self.root) / f'{task_id}-verify_base.log'
            offset = quality.size(log)
            code = self.track(task_id, 'verify_base', lambda log, started: steps.verify_base(self.root, sha, self.plan, log, started))
            self.baselines[sha] = None if code is None else (code, triage.red_tests(quality.read_from(log, offset)))
        changed = (steps.delegate_status(self.root, task_id) or {}).get('files_changed')
        category, detail = triage.classify(self.root, (output, item['fiche'], changed), self.baselines.get(sha))
        entry['verify_category'] = category
        self.decide(f'fiche {task_id} : vérification rouge, catégorie {category} ({triage.summary(detail)})')
        return category, detail

    def run_verify(self, task_id):
        """(code, sortie de ce passage) : le journal de vérification d'une fiche s'allonge à chaque tentative."""
        env = steps.verify_env()
        log = journal.logs(self.root) / f'{task_id}-verify.log'
        offset = quality.size(log)
        code = self.track(
            task_id,
            'verify',
            lambda log, started: steps.verify(self.root, task_id, self.plan['verify'], self.plan['resync'], log, env, started),
        )
        return code, quality.read_from(log, offset)

    def format_task(self, task_id):
        """T99 : seul le format ruff est rouge ; `ruff format` sur les fichiers de la tâche dans son clone, sans modèle
        et sans compter d'échec. Vrai si ruff a mis en forme : la vérification est alors relancée."""
        self.at(task_id, 'format')
        clone = steps.task_clone(self.root, task_id)
        names = quality.python_files(clone, (steps.delegate_status(self.root, task_id) or {}).get('files_changed'))
        if not names:
            return False
        code = quality.format_files(clone, names, journal.logs(self.root) / f'{task_id}-verify.log')
        self.decide(
            f'fiche {task_id} : seul le format ruff était rouge ; `ruff format` lancé par le programme sur {len(names)} fichier(s) du clone '
            f'(code {code}), vérification relancée sans modèle ni échec compté'
        )
        return code == 0

    def quality_round(self, item, entry, kind, output):
        """T99, T114 et T118 : échec de catégorie connue, sans diagnostic de modèle ; la sortie de l'outil part telle quelle
        dans une ronde de correction courte sur le clone existant, qui compte comme un échec. Un budget de types dépassé
        y part une fois sans compter, avec les erreurs pyright des fichiers de la tâche (l'exécutant ne les voyait pas)."""
        task_id = item['id']
        free = kind == quality.TYPES and not entry.get('types_round')
        entry['failures'] += 0 if free else 1
        why = f'vérification : {quality.LABELS[kind]} rouge'
        if entry['failures'] >= 2:
            return self.give_up(task_id, entry, why + ' après un premier échec')
        details = None
        if free:
            entry['types_round'] = True
            changed = (steps.delegate_status(self.root, task_id) or {}).get('files_changed')
            details = quality.type_errors(steps.task_clone(self.root, task_id), changed)
        path = quality.context(self.root, task_id, kind, output, details)
        return self.correction_round(task_id, entry, path, why + ", sortie de l'outil sans diagnostic")

    def review(self, item, entry, _state):
        """T14 B5 : relecture Opus d'une fiche marquée importante ; des corrections passent par une ronde de correction."""
        task_id = item['id']
        if not item.get('review') or entry['steps'].get('review', {}).get('status') == 'done':
            return None
        self.at(task_id, 'review')
        # T113 : après une ronde de correction demandée par la relecture, la seconde ne juge que cette ronde.
        fixes = entry.get('review_fixes')
        verdict, path, error = review.recheck(self.root, item, fixes) if fixes else review.review(self.root, item)
        if verdict is None:
            entry['review_verdict'] = f'impossible ({error}) : à faire à la main'
            self.record(task_id, 'review', 'failed', reason=error)
            # T101 : une relecture ratée (`error_max_turns`…) figure dans « Défauts rencontrés » du rapport.
            self.decide(f'fiche {task_id} : relecture ciblée ratée ({error}), à faire à la main avant la fusion')
            return None
        if verdict == 'bon':
            entry['review_verdict'] = 'bon (Opus)'
            self.record(task_id, 'review', 'done', verdict=verdict)
            return None
        self.record(task_id, 'review', 'failed', verdict=verdict, corrections=path)
        entry['failures'] += 1
        if entry['failures'] >= 2:
            return self.give_up(task_id, entry, f'relecture ciblée : corrections demandées (`{path}`) après un premier échec')
        entry['review_verdict'] = 'à corriger : ronde de correction par {} (`{}`)'.format(self.plan['correcteur'], path)
        entry['review_fixes'] = path
        return self.correction_round(task_id, entry, path, 'la relecture Opus demande des corrections')

    def correction_round(self, task_id, entry, path, cause):
        """Des corrections (relecture, échec de qualité) repartent par le circuit : même tâche et même clone, exécutant
        bon marché, contexte `path`, puis tout revérifié."""
        corrector = self.plan['correcteur']
        for name in ('delegate', 'verify', 'review'):
            entry['steps'].pop(name, None)
        entry.update(stage_context=path, override=corrector, since=journal.precise(), correction=True)
        self.decide(f'fiche {task_id} : {cause} (`{path}`), ronde de correction par {corrector}')
        return 'retry'

    def merge(self, item, entry):
        """T14 B0 : la fusion attend la ligne « fusion validée » du propriétaire ; le programme lance alors la commande du circuit."""
        task_id = item['id']
        if task_id not in planning.decisions(self.root, self.plan['path'])['merge_validated']:
            notice = notices.ready(self.root, self.plan, item, entry)
            self.record(task_id, 'merge', 'waiting_validation', notice=notice)
            return self.wait_for_owner(task_id, entry, 'validation', notice)
        code = self.command(task_id, 'merge', steps.merge_cmd(task_id))
        state = steps.task_state(self.root, task_id)
        if not state or state.get('status') != 'merged':
            self.record(task_id, 'merge', 'failed', exit=code)
            return self.give_up(task_id, entry, 'fusion : code {}, état {}'.format(code, (state or {}).get('status')))
        self.record(task_id, 'merge', 'done', exit=code)
        entry['merge_commit'] = steps.merge_commit(self.root, task_id)
        entry['notice'] = None
        return self.mirror(task_id, entry)

    def mirror(self, task_id, entry):
        """Règle 13 : recopie du miroir, commit, puis la fiche passe dans `done`."""
        self.at(task_id, 'resync')
        if self.plan['resync']:
            steps.resync(self.root)
            self.record(task_id, 'resync', 'done')
        else:
            self.record(task_id, 'resync', 'skipped')
        self.data['current'] = {'task': task_id, 'step': 'commit'}
        entry['commit'] = steps.commit_scripts(self.root, task_id)
        self.record(task_id, 'commit', 'done' if entry['commit'] else 'skipped')
        self.data['done'].append(task_id)
        self.data['current'] = None
        self.save()
        self.move(task_id, 'done', 'fusionnée')
        return 'done'

    # --- fin -----------------------------------------------------------------------------------

    def stage(self, name, action):
        final = self.data['final']
        if final.get(name, {}).get('status') in ('done', 'skipped'):
            return
        self.at(None, name)
        status, extra = action(final.get(name, {}).get('status') == 'running')
        final[name] = dict({'status': status, 'at': journal.now()}, **extra)
        self.save()

    def finish(self, reason):
        """Règle 15 : rapport, relecture, commit, entrée du LOG, libération du verrou, puis journal clos.

        Un arrêt pour attendre le propriétaire écrit le rapport seulement : relecture, commit et LOG viennent à la vraie fin.
        """
        plan, data = self.plan, self.data
        status = 'stopped' if reason else 'finished'
        if reason == 'quota':
            self.move_all('in_progress', 'blocked', 'quota')

        def write(_resumed):
            view = dict(copy.deepcopy(data), status=status, finished_at=journal.now(), stop_reason=reason)
            report.write(self.root, view, plan['report'])
            return 'done', {}

        self.stage('report', write)
        if reason not in WAITING:
            self.closing_stages()
        self.beat.stop()
        steps.orchestrator(self.root, 'release')
        data.update(status=status, finished_at=journal.now(), current=None, stop_reason=reason)
        self.save()
        journal.emit(self.root, status, plan, None)
        if reason == 'quota':
            return EXIT_QUOTA
        if reason in WAITING:
            return EXIT_WAITING
        return EXIT_FAILED if data['failed'] else 0

    def closing_stages(self):
        plan = self.plan

        def check(resumed):
            if not plan['report_check']:
                return 'skipped', {}
            if resumed and self.wait_check():
                return 'done', {'exit': None}
            code = self.mark_running('report_check', lambda: report.check(self.root, plan['report']))
            if code != 0:
                self.decide(
                    'report-check.sh a rendu le code {} sur {} : verdict à lire, le plan n\'échoue pas'.format(code, plan['report'])
                )
            return 'done', {'exit': code}

        def commit(_resumed):
            if not plan['commit_report']:
                return 'skipped', {}
            return 'done', {'commit': steps.commit_report(self.root, plan['report'], plan['session'])}

        def log(_resumed):
            # T14 B3 : l'entrée de docs/LOG.md, ajoutée en bas et commitée.
            return ('done', {}) if notices.log_entry(self.root, self.data, plan['report']) else ('skipped', {})

        self.stage('report_check', check)
        self.stage('report_commit', commit)
        self.stage('log', log)

    def mark_running(self, name, call):
        self.data['final'][name] = {'status': 'running', 'at': journal.now()}
        self.save()
        return call()

    def wait_check(self):
        """Une relecture lancée par le passage tué peut tourner encore : l'attendre plutôt que la relancer."""
        needle = self.plan['report']
        if not steps.find_processes('report-check', needle):
            return False
        print('report-check.sh encore en cours : j\'attends sa fin', flush=True)
        while steps.find_processes('report-check', needle):
            time.sleep(steps.seconds('ORCHESTRATOR_POLL_SECONDS', steps.POLL_DEFAULT))
        self.decide('report-check.sh lancé par le passage tué a fini seul : relecture non rejouée, code inconnu')
        return True

    def stop(self, reason):
        self.move_all('in_progress', 'blocked', reason)
        self.data.update(status='stopped', stop_reason=reason)
        self.save()
        journal.emit(self.root, 'stopped', self.plan, self.data.get('current'))


def on_term(_signum, _frame):
    raise Terminated()


def execute(root, plan):
    old, code = resume.previous(root, plan)
    if code is not None:
        return code
    lock = steps.read_lock(root)
    if lock is not None and lock.get('session') != plan['session']:
        print('verrou tenu par la session {}'.format(lock.get('session') or 'inconnue'), file=sys.stderr)
        return EXIT_HELD
    claimed, output = steps.claim(root, plan, os.getpid(), lock is not None)
    if claimed != 0:
        print(f'verrou refusé : {output}', file=sys.stderr)
        return EXIT_HELD
    data = journal.new(plan) if old is None else resume.resumed(old, WAITING)
    runner = Runner(root, plan, data)
    signal.signal(signal.SIGTERM, on_term)
    try:
        runner.save()
        runner.beat.start()
        journal.emit(root, 'started' if old is None else 'resumed', plan, data.get('current'))
        if old is not None:
            runner.move_all('blocked', 'in_progress', 'reprise')
        runner.wait_orphan()
        return runner.run_plan()
    except Terminated:
        runner.stop('signal')
        return EXIT_TERM
    except Exception as error:
        traceback.print_exc()
        try:
            runner.stop(f'erreur : {error}')
        except (OSError, ValueError):
            pass
        return EXIT_ERROR


def show_status(root, plan, given):
    """`--status` : état des fiches d'après le journal, en lecture seule (ni réservation ni écriture)."""
    try:
        data = journal.load(root)
    except ValueError as error:
        print(f'journal refusé : {error}', file=sys.stderr)
        return EXIT_ERROR
    for line in journal.status_lines(data, given, plan['path']):
        print(line)
    return 0


def main(argv):
    args = planning.parse_args(argv)
    root = os.path.abspath(args.root or os.getcwd())
    if not os.path.isdir(root):
        print(f'racine absente : {root}', file=sys.stderr)
        return EXIT_USAGE
    try:
        plan = planning.load(root, args.plan, {'session': args.session, 'by': args.by, 'model': args.model})
    except planning.PlanError as error:
        print(f'plan refusé : {error}', file=sys.stderr)
        return EXIT_USAGE
    if args.status:
        return show_status(root, plan, args.plan)
    if args.detach:
        options = [
            item for name in ('session', 'by', 'model') if getattr(args, name) is not None for item in ('--' + name, getattr(args, name))
        ]
        return journal.detach(root, [sys.executable, os.path.abspath(__file__), args.plan, '--root', root] + options)
    if os.environ.get('ORCHESTRATOR_DETACHED'):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        return execute(root, plan)
    except Exception:
        traceback.print_exc()
        return EXIT_ERROR


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
