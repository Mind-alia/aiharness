"""Reproducible adversarial tests, using the existing exchange delegation circuit.

Python 3.14, standard library only. extra.txt lists original test destinations;
fixtures live under acceptance/<id>/breaker and are exposed only by verification.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from contextlib import contextmanager
from glob import escape
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True
from secrets import known_values, mask_secrets

import workdir
from class_policy import policy
from scope import changed_paths, fingerprint
from task import acceptance_feedback, read_task, safe_path, validate_acceptance_name
from task import run_acceptance as original_acceptance

GIT = workdir.git_command()


PYTHON_RUNNER = '''import io,json,sys,unittest
from pathlib import Path
p=Path(sys.argv[1])
suite=unittest.defaultTestLoader.discover(str(p.parent),pattern=p.name)
r=unittest.TextTestRunner(stream=io.StringIO()).run(suite)
print(json.dumps({'breaker_result':True,'tests':r.testsRun,'failures':len(r.failures)+sum(not isinstance(t,unittest.loader._FailedTest) for t,_ in r.errors),'errors':sum(isinstance(t,unittest.loader._FailedTest) for t,_ in r.errors),'skipped':len(r.skipped),'message':'\\n'.join(t for _,t in r.failures+r.errors)}))
sys.exit(1 if not r.wasSuccessful() else 0)
'''


def test_command(name):
    if name.endswith('.py'):
        return ['python3', '-B', '-c', PYTHON_RUNNER, name]
    if re.search(r'\.(?:test|spec)\.[cm]?[jt]sx?$', name):
        return ['npx', '--no-install', 'vitest', 'run', name, '--reporter=json', '--outputFile.json=/dev/stdout']
    raise ValueError('Format de test non pris en charge : ' + name)


TEST_DIRECTORIES = ('tests', 'test', '__tests__', '__breaker__')
TEST_NAME = re.compile(r'test_.*\.py|.*_test\.py|.*\.(?:test|spec)\.(?:js|ts|jsx|tsx|mjs|cjs|mts|cts)')


def is_test_path(name):
    path = PurePosixPath(name)
    return any(part in TEST_DIRECTORIES for part in path.parts[:-1]) or bool(TEST_NAME.fullmatch(path.name))


def is_bytecode(name):
    path = PurePosixPath(name)
    return '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo')


def task_changes(worktree, base):
    names = set()
    for args in (['diff', '--name-only', '-z', '--no-renames', base, '--'], ['ls-files', '--others', '--exclude-standard', '-z']):
        code, out, err = execute([*GIT, *args], cwd=worktree)
        if code:
            raise ValueError(err)
        names.update(name for name in out.split('\0') if name)
    return sorted(name for name in names if not name.startswith('.workspace/') and '__breaker__' not in PurePosixPath(name).parts)


def change_fingerprint(worktree, base):
    digest = hashlib.sha256(base.encode('utf-8'))
    for name in task_changes(worktree, base):
        digest.update(json.dumps([name, fingerprint(safe_path(Path(worktree) / name))]).encode('utf-8'))
    return digest.hexdigest()


def merge_refusal(root, task_id, worktree, base):
    try:
        path = safe_path(Path(root) / '.workspace/breaker' / task_id / 'report.json')
        report = json.loads(path.read_text(encoding='utf-8'))
        if (
            isinstance(report, dict)
            and report.get('verdict') == 'no_implementation'
            and report.get('base_commit') == base
            and report.get('fingerprint') == change_fingerprint(worktree, base)
        ):
            return 'no_implementation : aucun test ne distingue le diff de la base (relancer scripts/breaker.sh après correction).'
    except (OSError, ValueError, TypeError):
        pass
    return None


def passes(code, data):
    return code == 0 and data.get('tests', 0) >= 1 and not data.get('failures') and not data.get('errors')


def instrument_names(task, root):
    return manifest_names(task, root, 'instrument.txt')


def extra_names(task, root):
    return manifest_names(task, root, 'extra.txt')


def manifest_names(task, root, manifest):
    path = safe_path(Path(root) / '.workspace/acceptance' / task['id'] / manifest)
    if not path.exists():
        return []
    names = path.read_text(encoding='utf-8').splitlines()
    for name in names:
        validate_acceptance_name(name)
        if '__breaker__' not in Path(name).parts:
            raise ValueError('Test casseur hors périmètre : ' + name)
        test_command(name)
    if len(names) != len(set(names)):
        raise ValueError('Tests casseurs répétés')
    return names


def has_acceptance(task, root):
    return bool(task['acceptance'] or extra_names(task, root))


def run_acceptance(task, root, worktree, execute, exchange=False, skip=()):  # noqa: PLR0913
    if task['acceptance']:
        code, feedback = original_acceptance(task, root, worktree, execute, exchange)
        if code:
            return code, feedback
    names = [name for name in extra_names(task, root) if name not in skip]
    with staged_tests(task, root, worktree, names):
        for name in names:
            code, out, err = execute(test_args(task, root, name, exchange), cwd=worktree)
            data = result_data(out)
            if code or data.get('tests') != 1 or data.get('errors') or data.get('skipped'):
                return code or 1, diagnostic(out, err)
    return 0, ''


def run_instruments(task, root, worktree, execute, exchange=False):
    """Play the instrument tests; a failing test is reported, never raised."""
    names = instrument_names(task, root)
    results = []
    if not names:
        return results
    with staged_tests(task, root, worktree, names):
        for name in names:
            code, out, err = execute(test_args(task, root, name, exchange), cwd=worktree)
            data = result_data(out)
            ok = not (code or data.get('tests') != 1 or data.get('errors') or data.get('skipped') or data.get('failures'))
            results.append({'test': name, 'passed': bool(ok)})
    return results


def test_args(task, root, name, exchange):
    args = test_command(name)
    if exchange:
        args = ['bash', str(Path(root) / 'scripts/sandbox.sh'), 'exec', task['id'], '--'] + args
    return args


@contextmanager
def staged_tests(task, root, worktree, names):
    """Copy breaker tests into the worktree; always remove them afterwards."""
    files, directories = [], []
    try:
        pairs = []
        for name in names:
            src = safe_path(Path(root) / '.workspace/acceptance' / task['id'] / 'breaker' / name)
            dst = safe_path(Path(worktree) / name)
            if not src.is_file() or dst.exists():
                raise ValueError('Acceptation casseur absente ou destination occupée : ' + name)
            pairs.append((src, dst))
        for src, dst in pairs:
            missing, parent = [], dst.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for parent in reversed(missing):
                parent.mkdir()
                directories.append(parent)
            with dst.open('xb') as stream:
                files.append(dst)
                stream.write(src.read_bytes())
        yield
    finally:
        for path in reversed(files):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        for path in reversed(directories):
            try:
                path.rmdir()
            except OSError:
                pass


def result_data(out):
    for line in reversed(out.splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get('breaker_result') is True:
            return data
    try:
        data = json.JSONDecoder().raw_decode(out, out.index('{'))[0]
        results = [a for t in data['testResults'] for a in t['assertionResults']]
        failed = [a for a in results if a['status'] == 'failed']
        return dict(
            tests=len(results),
            failures=len(failed),
            errors=int(data.get('numRuntimeErrorTestSuites', 0)),
            skipped=sum(a['status'] not in ('passed', 'failed') for a in results),
            message='\n'.join(m for a in failed for m in a.get('failureMessages', [])),
        )
    except (ValueError, KeyError, TypeError):
        return dict(tests=0, failures=0, errors=1, skipped=0, message='Diagnostic de test absent.')


def diagnostic(out, err):
    data = result_data(out)
    return acceptance_feedback(data.get('message', '') + '\n' + err)


def execute(args, cwd=None, env=None):
    # The agent and delegation broker are children of this process. Stop the
    # whole group before cleanup so a late agent write cannot recreate tests.
    process = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)

    def stop():
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        # The leader may exit while a descendant ignores SIGTERM.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()

    try:
        out, err = process.communicate(timeout=300)
        return process.returncode, out, err
    except subprocess.TimeoutExpired:
        stop()
        return 124, '', 'Délai de vérification dépassé.'
    except BaseException:
        stop()
        raise


def masked_report(report, values):
    # Mask decoded strings before JSON escaping (quotes and newlines in secrets).
    result = {
        key: [{field: mask_secrets(value, values) for field, value in entry.items()} for entry in report[key]]
        for key in ('retained', 'discarded')
    }
    if 'error' in report:
        result['error'] = mask_secrets(report['error'], values)
    for key in ('verdict', 'base_commit', 'fingerprint', 'class'):
        if key in report:
            result[key] = report[key]
    if 'discriminant' in report:
        found = report['discriminant']
        result['discriminant'] = found and {k: mask_secrets(v, values) for k, v in found.items()}
    return result


def private_write(path, text):
    safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(text)
    path.chmod(0o600)


def run(root, options):  # noqa: C901, PLR0915
    from mission import load_task_state

    task_id = options.task_id
    state = load_task_state(root, task_id)
    if state['status'] not in ('prepared', 'done'):
        raise ValueError('La tâche doit être prepared ou done.')
    wt = safe_path(workdir.state_worktree(root, state))
    if not wt.is_dir() or wt != workdir.task_worktree(root, task_id):
        raise ValueError('Worktree de tâche absent ou incohérent.')
    workdir.check_no_symlinks_below(workdir.work_base(root), wt)
    source = safe_path(root / state['source_task'])
    original = read_task(source)
    if original['id'] != task_id:
        raise ValueError('Identifiant de tâche incohérent.')
    rules = policy(original, root)
    if rules['breaker'] == 'none':
        skipped = dict(verdict='skipped_small', retained=[], discarded=[], **{'class': 'small'})
        private_write(
            safe_path(root / '.workspace/breaker' / task_id) / 'report.json', json.dumps(skipped, ensure_ascii=False, indent=2) + '\n'
        )
        print('fiche petite : pas de casseur')
        return 0
    code, diff, err = execute([*GIT, 'diff', '--no-ext-diff', '--no-renames', state['base_commit'], '--'], cwd=wt)
    if code:
        raise ValueError(err)
    code, names, err = execute([*GIT, 'diff', '--name-only', '-z', '--no-renames', state['base_commit'], '--'], cwd=wt)
    if code:
        raise ValueError(err)
    folders = sorted({str(Path(n).parent / '__breaker__') for n in names.split('\0') if n})
    if not folders:
        raise ValueError('Aucun fichier modifié par la tâche.')
    for folder in folders:
        path = safe_path(wt / folder)
        if path.exists():
            raise ValueError('Dossier casseur déjà présent : ' + folder)

    def breaker_directories():
        found = set()
        for current, dirs, _files in os.walk(wt, followlinks=False):
            dirs[:] = [d for d in dirs if d not in ('.git', '.workspace', 'node_modules')]
            if '__breaker__' in dirs:
                found.add((Path(current) / '__breaker__').relative_to(wt).as_posix())
                dirs.remove('__breaker__')
        return found

    existing_folders = breaker_directories()
    directory = safe_path(root / '.workspace/breaker' / task_id)
    contract = directory / 'task.md'
    base = state['base_commit']
    header = dict(
        id=task_id,
        agent=options.agent,
        mode='write',
        isolation='exchange',
        network='off',
        allowed=[escape(p) + '/**' for p in folders],
        attempts=1,
        verify='true',
        verify_profile='off',
        verify_lint='off',
    )
    body = (
        'Cherche à CASSER le résultat : cas limites, valeurs extrêmes, entrées absurdes, '
        'plantages, contournements de contrat, fuites de secrets.\n'
        'Lis la fiche originale ci-dessous et le diff. Écris au plus %d tests, un fichier neuf par test, '
        'une faille par test, nom explicite. Chaque test DEVRAIT passer selon la consigne mais échoue. '
        'Python : unittest ; JavaScript/TypeScript : Vitest (*.test.ts, *.spec.js). '
        'Aucun changement de production. Aucun commit ni commande Git. '
        'Lance les tests via bash scripts/run-in-sandbox.sh -- …\n\nFiche originale :\n%s\nDiff :\n%s'
        % (options.max_findings, source.read_text(), diff)
    )
    if options.agent != 'none':
        private_write(
            contract, '---\n' + '\n'.join(k + ': ' + json.dumps(v, ensure_ascii=False) for k, v in header.items()) + '\n---\n' + body
        )
    report = dict(retained=[], discarded=[], base_commit=base, **{'class': rules['class']}, fingerprint=change_fingerprint(wt, base))
    changes = task_changes(wt, base)
    attempted = False
    candidates = {}

    def sandbox(*args):
        return execute(['bash', str(root / 'scripts/sandbox.sh'), *args], cwd=wt)

    def production_state():
        return {
            name: fingerprint(safe_path(wt / name))
            for name in changed_paths(wt)
            if not name.startswith('.workspace/') and not any(name == f or name.startswith(f + '/') for f in folders)
        }

    baseline = production_state()
    initial_files = {name: ((wt / name).read_bytes(), state[0] & 0o777) for name, state in baseline.items() if state is not None}
    code, index_name, err = execute([*GIT, 'rev-parse', '--git-path', 'index'], cwd=wt)
    if code:
        raise ValueError('Index Git introuvable : ' + err)
    index_path = Path(index_name.strip())
    index_path = safe_path(index_path if index_path.is_absolute() else wt / index_path)
    initial_index = index_path.read_bytes()

    def restore_production():  # noqa: C901
        # Restore task-time bytes, never HEAD: the implementation may be dirty.
        current = {
            name
            for name in changed_paths(wt)
            if not name.startswith('.workspace/') and not any(name == f or name.startswith(f + '/') for f in folders)
        }
        for name in sorted(current - set(initial_files)):
            path = wt / name
            try:
                safe_path(path.parent)
            except ValueError:
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        for name, (content, mode) in initial_files.items():
            path = wt / name
            try:
                if fingerprint(safe_path(path)) == baseline[name]:
                    continue
            except ValueError:
                pass
            parent = wt
            for part in Path(name).parts[:-1]:
                parent = parent / part
                if parent.is_symlink() or parent.is_file():
                    parent.unlink()
                parent.mkdir(exist_ok=True)
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            path.write_bytes(content)
            path.chmod(mode)
        safe_path(index_path).write_bytes(initial_index)

    def check_production():
        if production_state() != baseline:
            raise ValueError('Modification de production hors périmètre pendant la confirmation.')

    base_content = {}

    def base_files(everything=False):
        # Production only, unless `everything` (the hidden tests judge the complete base).
        if everything not in base_content:
            found = base_content[everything] = {}
            for name in changes:
                if is_test_path(name) and not everything:
                    continue
                listing = subprocess.run(
                    [*GIT, '--literal-pathspecs', 'ls-tree', '-z', base, '--', name], cwd=wt, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                entry = listing.stdout.split(b'\0')[0]
                if listing.returncode or not entry:
                    found[name] = None
                    continue
                mode, kind = entry.split(b'\t')[0].split()[:2]
                shown = subprocess.run([*GIT, 'show', base + ':' + name], cwd=wt, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if kind != b'blob' or mode == b'120000' or shown.returncode:
                    raise ValueError('Fichier de la base non restituable : ' + name)
                found[name] = (shown.stdout, int(mode, 8) & 0o777)
        return base_content[everything]

    def on_base(action, everything=False):
        # Files go back to <base> only while `action` runs: production, or every change when `everything`.
        files = base_files(everything)
        created = []
        try:
            for name, found in files.items():
                path = wt / name
                safe_path(path.parent)
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
                if found is None:
                    continue
                missing, parent = [], path.parent
                while not parent.exists():
                    missing.append(parent)
                    parent = parent.parent
                for parent in reversed(missing):
                    parent.mkdir()
                    created.append(parent)
                path.write_bytes(found[0])
                path.chmod(found[1])
            result = action()
        finally:
            try:
                restore_production()
            finally:
                for parent in reversed(created):
                    try:
                        parent.rmdir()
                    except OSError:
                        pass
        check_production()
        return result

    def test_passes(command):
        code, out, err = sandbox('exec', task_id, '--', *command)
        return passes(code, result_data(out))

    def find_discriminant():  # noqa: C901
        if has_acceptance(original, root):
            name = (original['acceptance'] or extra_names(original, root))[0]
            if run_acceptance(original, root, wt, execute, True)[0] == 0:
                check_production()
                if on_base(lambda: run_acceptance(original, root, wt, execute, True)[0], everything=True) != 0:
                    return dict(source='acceptance', test=name)
        for name in changes:
            if not is_test_path(name) or not (wt / name).is_file():
                continue
            try:
                command = test_command(name)
            except ValueError:
                continue
            passed = test_passes(command)
            check_production()
            if passed and not on_base(lambda command=command: test_passes(command)):
                return dict(source='projet', test=name)
        for entry in report['discarded']:
            if entry.get('reason') != 'passe':
                continue
            name = entry['test']
            command = test_command(name)
            path = safe_path(wt / name)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                private_write(path, candidates[name].decode('utf-8'))
                passed = on_base(lambda command=command: test_passes(command))
            finally:
                clean()
            if not passed:
                return dict(source='casseur', test=name)
        return None

    def clean():
        cleanup_folders = sorted(set(folders) | (breaker_directories() - existing_folders))
        for folder in cleanup_folders:
            path = wt / folder
            # Never follow a parent replaced by the agent or a test.
            try:
                safe_path(path.parent)
            except ValueError:
                continue
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        # These paths were absent before this run: remove any agent staging too.
        execute([*GIT, '--literal-pathspecs', 'reset', '-q', 'HEAD', '--'] + cleanup_folders, cwd=wt)

    try:
        attempted = True
        if options.agent != 'none':
            env = dict(os.environ, WORKSPACE_ROLE='relecture')
            code, out, err = execute(['bash', str(root / 'scripts/delegate.sh'), str(contract)], cwd=root, env=env)
            status_path = root / '.workspace/run/delegate' / task_id / 'status.json'
            status = json.loads(safe_path(status_path).read_text()) if status_path.is_file() else {}
            if code or status.get('outcome') not in ('done', 'done_with_warning'):
                raise ValueError('Délégation casseur refusée : ' + status.get('outcome', 'failed'))
            check_production()
            for folder in folders:
                for path in sorted((wt / folder).rglob('*')):
                    safe_path(path)
                    name = path.relative_to(wt).as_posix()
                    if path.is_file() and not is_bytecode(name):
                        candidates[name] = path.read_bytes()
            clean()
        code, out, err = sandbox('start', task_id)
        if code:
            raise ValueError('Impossible de démarrer le conteneur de confirmation.')
        suite_ok = True
        if options.agent != 'none':
            code, out, err = execute(['bash', str(root / 'scripts/verify-task.sh'), '--sandbox', task_id, str(source)], cwd=wt)
            suite_ok = code == 0
            check_production()
        for index, (name, content) in enumerate(candidates.items()):
            entry = dict(test=name)
            if not suite_ok:
                entry['reason'] = 'ne compile pas'
            elif index >= options.max_findings:
                entry['reason'] = 'limite dépassée'
            else:
                try:
                    command = test_command(name)
                except ValueError:
                    entry['reason'] = 'ne compile pas'
                else:
                    path = safe_path(wt / name)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    results = []
                    for _ in range(2):
                        private_write(path, content.decode('utf-8'))
                        code, out, err = sandbox('exec', task_id, '--', *command)
                        check_production()
                        data = result_data(out)
                        results.append((code, data))
                    clean()
                    if any(d.get('errors') or d.get('tests') != 1 or d.get('skipped') or c not in (0, 1) for c, d in results):
                        entry['reason'] = 'ne compile pas'
                    elif all(c == 0 and not d['failures'] for c, d in results):
                        entry['reason'] = 'passe'
                    elif all(c == 1 and d['failures'] == 1 for c, d in results):
                        entry['assertion'] = acceptance_feedback(results[-1][1].get('message', ''))
                    else:
                        entry['reason'] = 'instable'
            report['discarded' if 'reason' in entry else 'retained'].append(entry)
        report['discriminant'] = None if report['retained'] else find_discriminant()
        report['verdict'] = 'failles' if report['retained'] else 'sans_faille' if report['discriminant'] else 'no_implementation'
        if options.promote and report['retained']:
            acceptance = safe_path(root / '.workspace/acceptance' / task_id)
            manifest = 'instrument.txt' if rules['breaker'] == 'instrument' else 'extra.txt'
            existing = manifest_names(original, root, manifest)
            for entry in report['retained']:
                name = entry['test']
                dest = safe_path(acceptance / 'breaker' / name)
                private_write(dest, candidates[name].decode('utf-8'))
                if name not in existing:
                    existing.append(name)
            private_write(acceptance / manifest, '\n'.join(existing) + '\n')
    except BaseException as error:
        report['retained'] = []
        report['error'] = str(error)
        raise
    finally:
        try:
            try:
                clean()
            finally:
                restore_production()
        except BaseException as error:
            report['retained'] = []
            report['error'] = str(error)
            raise
        finally:
            if attempted:
                sandbox('stop', task_id)
            values = known_values(root)
            private_write(directory / 'report.json', json.dumps(masked_report(report, values), ensure_ascii=False, indent=2) + '\n')
    print('%d faille(s) reproductible(s), %d test(s) écarté(s).' % (len(report['retained']), len(report['discarded'])))
    if report['verdict'] == 'no_implementation':
        print('no_implementation : aucun test ne distingue le diff de la base.')
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('task_id')
    parser.add_argument(
        '--agent', choices=('codex', 'claude-haiku', 'claude-sonnet', 'claude-opus', 'antigravity', 'none'), default='codex'
    )
    parser.add_argument('--max-findings', type=int, default=5)
    parser.add_argument('--promote', action='store_true')
    options = parser.parse_args()
    if not re.fullmatch('[a-z0-9-]+', options.task_id) or options.max_findings < 1:
        parser.error('Identifiant de tâche ou limite invalide.')
    root = options.root.resolve()

    def interrupted(signum, frame):
        raise KeyboardInterrupt('Casseur interrompu')

    signal.signal(signal.SIGTERM, interrupted)
    try:
        lock = safe_path(root / '.workspace/orchestration/.lock')
        with lock.open('r+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return run(root, options)
    except (OSError, ValueError, KeyboardInterrupt) as error:
        print(mask_secrets('breaker.sh: tâche refusée : ' + str(error), known_values(root)), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
