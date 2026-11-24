"""Relecture d'un rapport : affirmations vérifiées par une autre famille, avec témoins faux (Python 3.14)."""

import argparse
import datetime
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.dont_write_bytecode = True
import consumption
import delegate_run
import report_day
from safety import check_no_symlinks, write_file_secure

REPORT_TASK_PREFIX = 'report-check-'
REPORT_TASK_FALLBACK = 'rapport'
REPORT_TASK_MAX = 64
NON_SLUG = re.compile(r'[^a-z0-9]+')
RULES = 'REGLES-VERIFICATEUR.md'
EXTRACTION = 'CONSIGNE-EXTRACTION.md'
DEFAULT_DECOYS = '~/.config/workspace-ia/report-check/temoins.txt'
DECOY_COUNT = 3
MIN_TRUE = 1
VERDICTS = ('vraie', 'fausse', 'non_verifiable')
RELAYS = {'antigravity': 'gemini.sh', 'codex': 'codex.sh'}
BEGIN, END = '<!-- report-check:debut -->', '<!-- report-check:fin -->'
BLOCK = re.compile(re.escape(BEGIN) + '.*?' + re.escape(END) + '\n?', re.DOTALL)
GENERATED_BEGIN, GENERATED_END = report_day.BLOCK_BEGIN, report_day.BLOCK_END
GENERATED_MARKER = re.compile(re.escape(report_day.BLOCK_BEGIN_PREFIX) + '(?: [^\n]*?)?-->|' + re.escape(GENERATED_END))
USAGE_KEYS = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'output_tokens')
EXTRACTION_TIMEOUT = 600
VERIFIER_TIMEOUT = 1800
RANDOM = random.SystemRandom()


class Stop(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print('report-check : ' + message, file=sys.stderr)
        sys.exit(64)


def max_claims(value):
    if not re.fullmatch(r'[0-9]+', value) or not 1 <= int(value) <= 20:
        raise argparse.ArgumentTypeError('--max doit être un entier entre 1 et 20')
    return int(value)


def parse_args(argv):
    parser = Parser(prog='report-check.sh', allow_abbrev=False)
    parser.add_argument('root', type=Path)
    parser.add_argument('report', type=Path)
    parser.add_argument('--verifier', choices=sorted(RELAYS), default='antigravity')
    parser.add_argument('--repo', action='append', default=[], type=Path)
    parser.add_argument('--max', dest='limit', type=max_claims, default=20)
    return parser.parse_args(argv)


def one_line(text):
    # One line, and nothing that could close or open the block markers.
    text = ' '.join(str(text).split())
    return text.replace('<!--', '<!- -').replace('-->', '- ->')


def under(path, parent):
    return path == parent or parent in path.parents


def read_documents(root):
    texts = []
    for name in (RULES, EXTRACTION):
        for directory in ('docs', 'template/docs'):
            path = root / directory / name
            if path.is_file() and not path.is_symlink():
                texts.append(path.read_text(encoding='utf-8'))
                break
        else:
            raise Stop(3, 'document introuvable : docs/' + name)
    return texts


def read_decoys(roots):
    path = Path(os.environ.get('REPORT_CHECK_DECOYS') or os.path.expanduser(DEFAULT_DECOYS))
    real = Path(os.path.realpath(str(path)))
    if any(under(real, parent) for parent in roots):
        raise Stop(64, 'la liste de témoins doit rester hors des dépôts lus par le vérificateur')
    try:
        lines = real.read_text(encoding='utf-8').splitlines()
    except (OSError, ValueError):
        raise Stop(3, 'liste de témoins illisible : ' + str(path))
    decoys = []
    for line in lines:
        line = one_line(line)
        if line and not line.startswith('#') and line not in decoys:
            decoys.append(line)
    if len(decoys) < DECOY_COUNT:
        raise Stop(3, 'la liste de témoins doit contenir au moins %d affirmations : %s' % (DECOY_COUNT, path))
    return RANDOM.sample(decoys, DECOY_COUNT)


def count_tokens(values):
    numbers = [v for v in values if type(v) is int and v >= 0]
    return sum(numbers) if numbers else None


def report_task_id(report_name):
    slug = NON_SLUG.sub('-', os.path.splitext(str(report_name))[0].lower()).strip('-')
    if not slug:
        return REPORT_TASK_PREFIX + REPORT_TASK_FALLBACK
    return (REPORT_TASK_PREFIX + slug)[:REPORT_TASK_MAX].rstrip('-')


def extract(root, instruction, report_name, report_text, limit):
    prompt = instruction + '\n\nRapport : ' + report_name + '\n\n' + report_text
    command = ['claude', '-p', '--output-format', 'json', '--model', 'haiku', '--permission-mode', 'plan', '--max-turns', '3']
    started = time.monotonic()
    try:
        process = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            cwd=str(root),
            timeout=EXTRACTION_TIMEOUT,
        )
    except FileNotFoundError:
        raise Stop(3, 'claude introuvable')
    except subprocess.TimeoutExpired:
        raise Stop(2, "l'extraction des affirmations a dépassé le délai")
    record = delegate_run.claude_record(process.stdout)
    if record:
        consumption.record_claude(
            root, 'relecture', 'haiku', report_task_id(report_name), record, duration=time.monotonic() - started, exit=process.returncode
        )
    if process.returncode or record.get('is_error') or not isinstance(record.get('result'), str):
        raise Stop(2, "l'extraction des affirmations a échoué (code %d)" % process.returncode)
    usage = record.get('usage')
    tokens = count_tokens([usage.get(key) for key in USAGE_KEYS] if isinstance(usage, dict) else [])
    claims = []
    for line in record['result'].splitlines():
        try:
            claim = json.loads(line)
        except ValueError:
            continue
        citation = claim.get('citation') if isinstance(claim, dict) else None
        if isinstance(citation, str) and citation.strip():
            claim['citation'] = one_line(citation)
            claims.append(claim)
    if not claims:
        raise Stop(2, 'aucune affirmation extraite du rapport')
    return claims[:limit], tokens


def verify(root, verifier, shown_roots, items, rules, task_id=None):  # noqa: PLR0913
    """Run the relay on a prompt kept outside every read repository; return (answer text, tokens)."""
    relay = root / 'scripts' / RELAYS[verifier]
    if not relay.is_file():
        raise Stop(3, 'relais introuvable : scripts/' + relay.name)
    lines = [json.dumps({'id': item['id'], 'citation': item['citation']}, ensure_ascii=False) for item in items]
    prompt = (
        rules
        + '\n\nRacines des dépôts (lecture seule) :\n'
        + '\n'.join('- ' + r for r in shown_roots)
        + '\n\nAffirmations (une ligne JSON par affirmation) :\n'
        + '\n'.join(lines)
        + '\n'
    )
    env = dict(os.environ, WORKSPACE_ROLE='relecture', WORKSPACE_TASK_ID=task_id or REPORT_TASK_PREFIX + REPORT_TASK_FALLBACK)
    env.pop('WORKSPACE_AGENT_ROOT', None)
    with tempfile.TemporaryDirectory(prefix='report-check-') as temporary:
        source, answer = Path(temporary) / 'consigne.md', Path(temporary) / 'reponse.txt'
        source.write_text(prompt, encoding='utf-8')
        try:
            process = subprocess.run(
                ['bash', str(relay), 'review', str(source), str(answer)],
                cwd=str(root),
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=VERIFIER_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise Stop(2, 'le vérificateur a dépassé le délai')
        if process.returncode:
            raise Stop(2, 'le vérificateur a échoué (code %d) : %s' % (process.returncode, process.stderr.strip()[-300:]))
        text = answer.read_text(encoding='utf-8', errors='replace') if answer.is_file() else ''
    metrics = {'tokens': None}
    try:
        delegate_run.read_relay_events(root, verifier, process.stdout, process.stderr, process.returncode, metrics)
    except (OSError, ValueError):
        metrics['tokens'] = None
    return text, consumption.token_total(metrics['tokens'], verifier)


def parse_answer(text, known):
    """First valid verdict per known identifier; lines that are JSON objects are kept for the detail."""
    verdicts, raw = {}, []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        raw.append(line.strip())
        identifier = entry.get('id')
        if identifier in known and identifier not in verdicts and entry.get('verdict') in VERDICTS:
            verdicts[identifier] = {'verdict': entry['verdict'], 'note': one_line(entry.get('note') or '')}
    return verdicts, raw


def summarize(items, verdicts):
    summary = {'claims': 0, 'true': 0, 'false': 0, 'non_verifiable': 0, 'decoys_caught': 0}
    false_lines, missed = [], []
    for item in items:
        found = verdicts.get(item['id'], {'verdict': 'non_verifiable', 'note': ''})
        if item['decoy']:
            if found['verdict'] == 'fausse':
                summary['decoys_caught'] += 1
            else:
                missed.append(item['citation'])
            continue
        summary['claims'] += 1
        summary[{'vraie': 'true', 'fausse': 'false'}.get(found['verdict'], 'non_verifiable')] += 1
        if found['verdict'] == 'fausse':
            false_lines.append('Fausse : « %s » — %s' % (item['citation'], found['note'] or 'sans note'))
    return summary, false_lines, missed


def build_block(summary, false_lines, missed, verifier, now, extraction, verification):  # noqa: PLR0913
    def shown(value):
        return 'inconnu' if value is None else str(value)

    headline = (
        '**Vérification externe** (%s UTC, %s) : %d/%d affirmations vraies, %d fausses, %d non vérifiables ; '
        'témoins %d/%d ; coût : extraction %s tokens, vérification %s tokens.'
        % (
            now.strftime('%Y-%m-%d'),
            verifier,
            summary['true'],
            summary['claims'],
            summary['false'],
            summary['non_verifiable'],
            summary['decoys_caught'],
            DECOY_COUNT,
            shown(extraction),
            shown(verification),
        )
    )
    body = [headline]
    if summary['true'] < MIN_TRUE:
        body.append('Refusé : aucune affirmation vraie (au moins %d exigée).' % MIN_TRUE)
    body += false_lines + ['Témoin manqué : « %s »' % citation for citation in missed]
    return BEGIN + '\n' + ''.join('> ' + line + '\n' for line in body) + END + '\n', headline


def insert_block(text, block):
    text = BLOCK.sub('', text)
    if text.startswith('# '):
        first, _, rest = text.partition('\n')
        return first + '\n' + block + rest
    return block + text


def generated_blocks(text):
    """(start, end) of each generated block, header line to end marker; Stop when they are malformed."""
    found, start = [], None
    for marker in GENERATED_MARKER.finditer(text):
        begins = marker.group(0) != GENERATED_END
        if begins == (start is not None):
            raise Stop(64, 'blocs générés mal formés : ' + ('bloc déjà ouvert' if begins else 'fin sans début'))
        if begins:
            start = marker.start()
        else:
            found.append((start, marker.end()))
            start = None
    if start is not None:
        raise Stop(64, 'blocs générés mal formés : début sans fin')
    return found


def prose(text):
    """The report without its own verdict block and without the generated blocks."""
    text = BLOCK.sub('', text)
    kept, position = [], 0
    for start, end in generated_blocks(text):
        kept.append(text[position:start])
        position = end
        if text.startswith('\n', position):
            position += 1
    return ''.join(kept) + text[position:]


def authenticate(root, text):
    """Regenerate every generated block from its header and refuse any difference."""
    text = BLOCK.sub('', text)
    for number, (start, end) in enumerate(generated_blocks(text), 1):
        block = text[start:end]
        try:
            header = report_day.parse_header(block.split('\n', 1)[0])
            expected = report_day.render(root, header['day'], header['usine'], header['claude_dir'], header['until']).rstrip('\n')
        except ValueError:
            raise Stop(1, 'bloc généré sans en-tête valide (bloc %d) : régénérer par report.sh --day' % number)
        if block != expected:
            old, new = block.split('\n'), expected.split('\n')
            line = next((i for i, pair in enumerate(zip(old, new, strict=False), 1) if pair[0] != pair[1]), min(len(old), len(new)) + 1)
            raise Stop(1, 'bloc généré retouché (bloc %d, ligne %d) : régénérer par report.sh --day' % (number, line))


def write_report(path, text):
    # Replace atomically and keep the report's own permissions.
    handle, temporary = tempfile.mkstemp(prefix='.report-check-', dir=str(path.parent))
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(text)
        shutil.copymode(str(path), temporary)
        os.replace(temporary, str(path))
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def write_detail(root, now, claims, raw, items, summary, extraction, verification, verifier):  # noqa: PLR0913
    base = root / '.workspace' / 'report-check'
    check_no_symlinks(base, root)
    base.mkdir(parents=True, exist_ok=True)
    directory = base / (now.strftime('%Y%m%d-%H%M%S') + '-' + ''.join(RANDOM.choice('0123456789abcdef') for _ in range(6)))
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    write_file_secure(directory / 'claims.jsonl', ''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in claims))
    write_file_secure(directory / 'verdicts.jsonl', ''.join(line + '\n' for line in raw))
    write_file_secure(
        directory / 'key.json', json.dumps({'decoys': [i['id'] for i in items if i['decoy']], 'items': items}, ensure_ascii=False, indent=2)
    )
    summary = dict(
        summary,
        decoys=DECOY_COUNT,
        verifier=verifier,
        date=now.strftime('%Y-%m-%d'),
        extraction_tokens=extraction,
        verification_tokens=verification,
    )
    write_file_secure(directory / 'summary.json', json.dumps(summary, ensure_ascii=False, indent=2))


def run(args):
    root = args.root.resolve()
    report = Path(os.path.abspath(str(args.report)))
    if report.is_symlink() or not report.is_file():
        raise Stop(64, 'le rapport doit être un fichier régulier : ' + str(args.report))
    try:
        report_text = report.read_text(encoding='utf-8')
    except (OSError, ValueError):
        raise Stop(64, 'rapport illisible : ' + str(args.report))
    authenticate(root, report_text)
    checked_text = prose(report_text)
    if not checked_text.strip():
        raise Stop(2, 'aucune prose à vérifier : le rapport ne contient que des blocs générés')
    repos = [Path(os.path.abspath(str(p))) for p in args.repo]
    for repo in repos:
        if not repo.is_dir():
            raise Stop(64, 'dépôt introuvable : ' + str(repo))
    rules, instruction = read_documents(root)
    decoys = read_decoys([root] + [p.resolve() for p in repos])

    claims, extraction = extract(root, instruction, report.name, checked_text, args.limit)
    items = [{'citation': c['citation'], 'decoy': False} for c in claims]
    items += [{'citation': d, 'decoy': True} for d in decoys]
    RANDOM.shuffle(items)
    items = [dict(id='C%02d' % number, **item) for number, item in enumerate(items, 1)]
    shown_roots = [os.path.abspath(str(args.root))] + [str(p) for p in repos]
    answer, verification = verify(root, args.verifier, shown_roots, items, rules, report_task_id(report.name))

    verdicts, raw = parse_answer(answer, {item['id'] for item in items})
    summary, false_lines, missed = summarize(items, verdicts)
    now = datetime.datetime.now(datetime.UTC)
    block, headline = build_block(summary, false_lines, missed, args.verifier, now, extraction, verification)
    write_detail(root, now, claims, raw, items, summary, extraction, verification, args.verifier)
    write_report(report, insert_block(report_text, block))
    enough = summary['true'] >= MIN_TRUE
    print(headline)
    if not enough:
        print('report-check : refusé : aucune affirmation vraie (au moins %d exigée)' % MIN_TRUE, file=sys.stderr)
    return 0 if enough and summary['false'] == 0 and summary['decoys_caught'] == DECOY_COUNT else 1


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return run(args)
    except Stop as stop:
        print('report-check : ' + str(stop), file=sys.stderr)
        return stop.code
    except (OSError, ValueError) as error:
        print('report-check : ' + str(error), file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())
