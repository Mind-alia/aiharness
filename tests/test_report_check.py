import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'template'
BASH = shutil.which('bash')
CLAIMS_MARK = 'Affirmations (une ligne JSON par affirmation) :\n'

# Shared by the fake tools: they record what they receive outside the project and answer from a config file.
FAKE_PRELUDE = '''#!%(python)s
import json, os, sys
config = json.load(open(os.environ['FAKE_CONFIG']))
records = os.environ['FAKE_RECORDS']

def record(name, **fields):
    with open(os.path.join(records, name + '.json'), 'w') as stream:
        json.dump(fields, stream)

def answer(prompt):
    claims = [json.loads(l) for l in prompt.split(%(mark)r, 1)[1].splitlines() if l.strip()]
    lines = []
    for claim in claims:
        citation = claim['citation']
        if citation in config.get('omit', []):
            continue
        if citation in config.get('decoys', []) and config.get('catch_decoys', True):
            verdict = 'fausse'
        elif citation in config.get('false', []):
            verdict = 'fausse'
        elif citation in config.get('unverifiable', []):
            verdict = 'non_verifiable'
        else:
            verdict = 'vraie'
        lines.append(json.dumps({'id': claim['id'], 'verdict': verdict, 'preuve': 'p', 'note': 'note ' + claim['id']}))
    return '\\n'.join(lines)
'''

FAKE_CLAUDE = (
    FAKE_PRELUDE
    + '''
stdin = sys.stdin.read()
record('claude', argv=sys.argv[1:], cwd=os.getcwd(), stdin=stdin)
if config.get('claude_exit'):
    sys.exit(config['claude_exit'])
print(json.dumps({'type': 'result', 'is_error': False, 'result': config['claude_result'],
                  'usage': config.get('claude_usage', {})}))
'''
)

FAKE_AGY = (
    FAKE_PRELUDE
    + '''
prompt = sys.argv[sys.argv.index('-p') + 1]
record('agy', argv=sys.argv[1:], prompt=prompt, cwd=os.getcwd(),
       detail_exists=os.path.isdir(os.path.join(config['project'], '.workspace', 'report-check')))
if config.get('agy_exit'):
    sys.exit(config['agy_exit'])
print(json.dumps({'status': 'SUCCESS', 'response': answer(prompt), 'usage': config.get('agy_usage', {})}))
'''
)

# The relay is run by bash: its first line hands the file to Python, which skips that line (-x).
FAKE_CODEX_RELAY = (
    'exec %(python)s -x "$0" "$@"\n'
    + FAKE_PRELUDE
    + '''
prompt = open(sys.argv[2], encoding='utf-8').read()
record('codex_relay', argv=sys.argv[1:], prompt=prompt)
events = os.path.join('.workspace', 'run', 'codex', 'events.jsonl')  # relative, like codex.sh (cwd = project)
os.makedirs(os.path.dirname(events), exist_ok=True)
with open(events, 'w') as stream:
    stream.write(json.dumps({'type': 'turn.started'}) + '\\n')
    stream.write(json.dumps({'type': 'turn.completed', 'usage': config.get('codex_usage', {})}) + '\\n')
with open(sys.argv[3], 'w', encoding='utf-8') as stream:
    stream.write(answer(prompt) + '\\n')
print('Codex done (review); answer in ' + sys.argv[3] + '; events in ' + events)
'''
)

FAKE_NPX = (
    FAKE_PRELUDE
    + '''
if '--version' in sys.argv:
    sys.exit(0)
prompt = sys.argv[-1]
record('codex', argv=sys.argv[1:], prompt=prompt)
if config.get('codex_exit'):
    sys.exit(config['codex_exit'])
with open(sys.argv[sys.argv.index('-o') + 1], 'w') as stream:
    stream.write(answer(prompt) + '\\n')
print(json.dumps({'type': 'turn.started'}))
print(json.dumps({'type': 'turn.completed', 'usage': config.get('codex_usage', {})}))
'''
)

REAL = [f'Le test {n} est vert.' for n in range(1, 6)]
DECOYS = [f'Le commit decoy{n} a été fusionné.' for n in range(1, 6)]
REPORT = '# Rapport du jour\n\nLe résumé du travail.\n\n## Détail\n\nUn tableau.\n'


def claim_lines(citations):
    return '\n'.join(
        json.dumps({'rapport': 'rapport.md', 'section': 'Détail', 'citation': c, 'type': 'etat'}, ensure_ascii=False) for c in citations
    )


class ReportCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.project = base / 'project'
        self.outside = base / 'outside'
        self.records = self.outside / 'records'
        self.bin = self.outside / 'bin'
        for directory in (self.project / 'docs', self.records, self.bin):
            directory.mkdir(parents=True)
        shutil.copytree(TEMPLATE / 'scripts', self.project / 'scripts', ignore=shutil.ignore_patterns('__tests__', '__pycache__'))
        for name in ('REGLES-VERIFICATEUR.md', 'CONSIGNE-EXTRACTION.md'):
            shutil.copy(TEMPLATE / 'docs' / name, self.project / 'docs' / name)
        self.report = self.project / 'rapport.md'
        self.report.write_text(REPORT)
        self.decoys = self.outside / 'temoins.txt'
        self.decoys.write_text('# témoins\n\n' + '\n'.join(DECOYS) + '\n')
        self.config = {
            'decoys': DECOYS,
            'claude_result': claim_lines(REAL),
            'project': str(self.project),
            'claude_usage': {'input_tokens': 10, 'cache_read_input_tokens': 5, 'cache_creation_input_tokens': 3, 'output_tokens': 2},
            'agy_usage': {'total_tokens': 123},
        }
        self.tool(self.bin / 'claude', FAKE_CLAUDE)
        self.tool(self.bin / 'agy', FAKE_AGY)
        self.tool(self.bin / 'npx', FAKE_NPX)

    def tool(self, path, body):
        path.write_text(body % {'python': sys.executable, 'mark': CLAIMS_MARK})
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def run_check(self, *args, report=None, config=None, decoys=None):
        """Run report-check.sh; the fake tools see the config through the environment."""
        self.config.update(config or {})
        config_file = self.outside / 'config.json'
        config_file.write_text(json.dumps(self.config))
        env = dict(
            os.environ,
            PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
            AGY_BIN=str(self.bin / 'agy'),
            FAKE_CONFIG=str(config_file),
            FAKE_RECORDS=str(self.records),
            REPORT_CHECK_DECOYS=str(decoys or self.decoys),
        )
        return subprocess.run(
            [BASH, str(self.project / 'scripts/report-check.sh'), str(report or self.report), *args],
            cwd=str(self.project),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )

    def recorded(self, name):
        return json.loads((self.records / (name + '.json')).read_text())

    def sent_claims(self):
        prompt = self.recorded('agy')['prompt']
        return [json.loads(line) for line in prompt.split(CLAIMS_MARK, 1)[1].splitlines() if line.strip()]

    def details(self):
        return sorted((self.project / '.workspace/report-check').glob('*'))

    def test_extraction_call_and_input(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        call = self.recorded('claude')
        self.assertEqual(
            call['argv'], ['-p', '--output-format', 'json', '--model', 'haiku', '--permission-mode', 'plan', '--max-turns', '3']
        )
        self.assertEqual(os.path.realpath(call['cwd']), os.path.realpath(str(self.project)))
        instruction = (TEMPLATE / 'docs/CONSIGNE-EXTRACTION.md').read_text()
        self.assertEqual(call['stdin'], instruction + '\n\nRapport : rapport.md\n\n' + REPORT)

    def test_verifier_prompt_and_planted_decoys(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        call = self.recorded('agy')
        rules = (TEMPLATE / 'docs/REGLES-VERIFICATEUR.md').read_text()
        self.assertTrue(call['prompt'].startswith(rules + '\n\nRacines des dépôts (lecture seule) :\n- '))
        self.assertIn('\n- ' + str(self.project) + '\n\nAffirmations', call['prompt'])
        claims = self.sent_claims()
        self.assertEqual([c['id'] for c in claims], ['C%02d' % n for n in range(1, 9)])
        citations = [c['citation'] for c in claims]
        self.assertEqual(sorted(citations), sorted(REAL + [c for c in citations if c in DECOYS]))
        self.assertEqual(len([c for c in citations if c in DECOYS]), 3)
        self.assertFalse(call['detail_exists'], 'la clé ne doit pas exister avant la fin du vérificateur')
        key = json.loads((self.details()[0] / 'key.json').read_text())
        planted = [c['id'] for c in claims if c['citation'] in DECOYS]
        self.assertEqual(sorted(key['decoys']), planted)
        self.assertEqual(len(key['items']), 8)
        self.assertEqual({i['id'] for i in key['items'] if i['decoy']}, set(planted))

    def test_block_after_title_and_stdout(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = self.report.read_text().splitlines()
        self.assertEqual(lines[0], '# Rapport du jour')
        self.assertEqual(lines[1], '<!-- report-check:debut -->')
        self.assertTrue(lines[2].startswith('> **Vérification externe** ('))
        self.assertIn(
            ' UTC, antigravity) : 5/5 affirmations vraies, 0 fausses, 0 non vérifiables ; '
            'témoins 3/3 ; coût : extraction 20 tokens, vérification 123 tokens.',
            lines[2],
        )
        self.assertEqual(lines[3], '<!-- report-check:fin -->')
        self.assertEqual(result.stdout, lines[2][2:] + '\n')
        self.assertEqual(self.report.read_text().replace('\n'.join(lines[1:4]) + '\n', ''), REPORT)

    def test_block_replaced_not_duplicated_and_without_title(self):
        for text in (REPORT, 'Sans titre.\n\n## Détail\n'):
            with self.subTest(text=text[:10]):
                self.report.write_text(text)
                for _ in range(2):
                    self.assertEqual(self.run_check().returncode, 0)
                content = self.report.read_text()
                self.assertEqual(content.count('<!-- report-check:debut -->'), 1)
                self.assertEqual(content.count('<!-- report-check:fin -->'), 1)
                if text.startswith('Sans'):
                    self.assertTrue(content.startswith('<!-- report-check:debut -->\n'))
                    self.assertTrue(content.endswith(text))
                else:
                    self.assertTrue(content.startswith('# Rapport du jour\n<!-- report-check:debut -->\n'))

    def test_zero_true_is_refused_with_reason(self):
        result = self.run_check(config={'unverifiable': REAL})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('report-check : refusé : aucune affirmation vraie (au moins 1 exigée)\n', result.stderr)
        lines = self.report.read_text().splitlines()
        self.assertTrue(lines[2].startswith('> **Vérification externe** ('))
        self.assertIn('0/5 affirmations vraies, 0 fausses, 5 non vérifiables ; témoins 3/3', lines[2])
        self.assertEqual(lines[3], '> Refusé : aucune affirmation vraie (au moins 1 exigée).')
        self.assertEqual(result.stdout, lines[2][2:] + '\n')

    def test_one_true_is_enough(self):
        result = self.run_check(config={'unverifiable': REAL[1:]})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertNotIn('Refusé', self.report.read_text())

    def test_false_claim_is_reported_and_exit_one(self):
        result = self.run_check(config={'false': [REAL[1]]})
        self.assertEqual(result.returncode, 1, result.stderr)
        text = self.report.read_text()
        self.assertIn('4/5 affirmations vraies, 1 fausses, 0 non vérifiables ; témoins 3/3', text)
        identifier = next(c['id'] for c in self.sent_claims() if c['citation'] == REAL[1])
        self.assertIn('> Fausse : « %s » — note %s\n' % (REAL[1], identifier), text)
        self.assertNotIn('Témoin manqué', text)

    def test_missed_decoy(self):
        result = self.run_check(config={'catch_decoys': False})
        self.assertEqual(result.returncode, 1, result.stderr)
        text = self.report.read_text()
        self.assertIn('5/5 affirmations vraies, 0 fausses, 0 non vérifiables ; témoins 0/3', text)
        sent = [c['citation'] for c in self.sent_claims() if c['citation'] in DECOYS]
        for citation in sent:
            self.assertIn('> Témoin manqué : « %s »\n' % citation, text)
        self.assertEqual(text.count('Témoin manqué'), 3)

    def test_omitted_lines_count_as_unverifiable(self):
        # The decoy draw is random: omit one real claim and every possible decoy.
        result = self.run_check(config={'omit': [REAL[0]] + DECOYS})
        self.assertEqual(result.returncode, 1, result.stderr)
        text = self.report.read_text()
        self.assertIn('4/5 affirmations vraies, 0 fausses, 1 non vérifiables ; témoins 0/3', text)
        self.assertEqual(text.count('Témoin manqué'), 3)

    def test_invalid_verdict_is_unverifiable(self):
        self.config['unverifiable'] = [REAL[2]]
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('4/5 affirmations vraies, 0 fausses, 1 non vérifiables', self.report.read_text())

    def test_max_limits_real_claims(self):
        many = [f'Le fait {n} est établi.' for n in range(30)]
        result = self.run_check('--max', '20', config={'claude_result': claim_lines(many)})
        self.assertEqual(result.returncode, 0, result.stderr)
        citations = [c['citation'] for c in self.sent_claims()]
        self.assertEqual(len(citations), 23)
        self.assertEqual(len([c for c in citations if c in many]), 20)
        self.assertEqual(len([c for c in citations if c in DECOYS]), 3)
        self.assertEqual(set(c for c in citations if c in many), set(many[:20]))

    def test_extraction_ignores_non_claim_lines(self):
        noise = ['```', 'texte libre', '{"citation": ""}', '{"autre": 1}', '[1, 2]']
        result = self.run_check(config={'claude_result': '\n'.join(noise[:2] + [claim_lines(REAL[:2])] + noise[2:])})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('2/2 affirmations vraies', self.report.read_text())

    def test_verifier_failure_leaves_report_unchanged(self):
        result = self.run_check(config={'agy_exit': 1})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.report.read_text(), REPORT)
        self.assertEqual(self.details(), [])

    def test_extraction_failure_or_no_claim_leaves_report_unchanged(self):
        for config in ({'claude_exit': 1}, {'claude_result': 'rien à extraire'}):
            with self.subTest(config=config):
                result = self.run_check(config=config)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.report.read_text(), REPORT)
                self.assertFalse((self.records / 'agy.json').exists())

    def test_decoy_list_must_stay_outside_read_repositories(self):
        other = self.outside / 'other'
        other.mkdir()
        cases = [
            (self.project / 'temoins.txt', []),
            (self.project / 'docs/temoins.txt', []),
            (other / 'temoins.txt', ['--repo', str(other)]),
        ]
        for path, args in cases:
            with self.subTest(path=str(path)):
                path.write_text('\n'.join(DECOYS))
                result = self.run_check(*args, decoys=path)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn('la liste de témoins doit rester hors des dépôts lus par le vérificateur', result.stderr)
                self.assertFalse((self.records / 'claude.json').exists())
                self.assertEqual(self.report.read_text(), REPORT)

    def test_decoy_list_too_short_or_missing(self):
        self.decoys.write_text('# a\nun\n\ndeux\n')
        self.assertEqual(self.run_check().returncode, 3)
        self.assertEqual(self.run_check(decoys=self.outside / 'absent.txt').returncode, 3)
        self.assertFalse((self.records / 'claude.json').exists())

    def test_missing_documents(self):
        (self.project / 'docs/REGLES-VERIFICATEUR.md').unlink()
        self.assertEqual(self.run_check().returncode, 3)
        (self.project / 'template/docs').mkdir(parents=True)
        shutil.copy(TEMPLATE / 'docs/REGLES-VERIFICATEUR.md', self.project / 'template/docs')
        self.assertEqual(self.run_check().returncode, 0)

    def test_usage_errors(self):
        for args in (
            ['--verifier', 'gpt'],
            ['--max', '0'],
            ['--max', '21'],
            ['--max', 'x'],
            ['--bogus'],
            ['--repo', str(self.outside / 'absent')],
        ):
            with self.subTest(args=args):
                result = self.run_check(*args)
                self.assertEqual(result.returncode, 64, result.stderr)
        self.assertEqual(self.run_check(report=self.project / 'absent.md').returncode, 64)
        link = self.project / 'lien.md'
        link.symlink_to(self.report)
        self.assertEqual(self.run_check(report=link).returncode, 64)
        link.unlink()
        self.assertEqual(self.report.read_text(), REPORT)

    def test_repo_roots_listed_for_verifier(self):
        other = self.outside / 'other'
        other.mkdir()
        self.assertEqual(self.run_check('--repo', str(other)).returncode, 0)
        head = self.recorded('agy')['prompt'].split('\n\nAffirmations', 1)[0]
        self.assertTrue(head.endswith('\n- %s\n- %s' % (self.project, other)), head[-200:])

    def test_detail_files_and_permissions(self):
        self.assertEqual(self.run_check().returncode, 0)
        directories = self.details()
        self.assertEqual(len(directories), 1)
        directory = directories[0]
        self.assertRegex(directory.name, r'^\d{8}-\d{6}-[0-9a-f]{6}$')
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(sorted(p.name for p in directory.iterdir()), ['claims.jsonl', 'key.json', 'summary.json', 'verdicts.jsonl'])
        for path in directory.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)
            self.assertFalse(path.is_symlink())
        claims = [json.loads(line) for line in (directory / 'claims.jsonl').read_text().splitlines()]
        self.assertEqual([c['citation'] for c in claims], REAL)
        verdicts = [json.loads(line) for line in (directory / 'verdicts.jsonl').read_text().splitlines()]
        self.assertEqual(len(verdicts), 8)
        summary = json.loads((directory / 'summary.json').read_text())
        self.assertEqual((summary['claims'], summary['true'], summary['false'], summary['decoys_caught']), (5, 5, 0, 3))
        self.assertEqual((summary['extraction_tokens'], summary['verification_tokens']), (20, 123))

    def test_unknown_costs(self):
        result = self.run_check(config={'claude_usage': {}, 'agy_usage': {}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('coût : extraction inconnu tokens, vérification inconnu tokens.', result.stdout)

    def test_codex_verifier(self):
        usage = {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 10}
        result = self.run_check('--verifier', 'codex', config={'codex_usage': usage})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(' UTC, codex) : 5/5 affirmations vraies', self.report.read_text())
        self.assertIn('vérification 110 tokens', result.stdout)
        self.assertFalse((self.records / 'agy.json').exists())
        self.assertEqual(self.recorded('codex')['prompt'].count('\n{"id": "C'), 8)

    def test_codex_cached_input_not_counted_twice(self):
        self.tool(self.project / 'scripts' / 'codex.sh', FAKE_CODEX_RELAY)
        usage = {'input_tokens': 1000, 'cached_input_tokens': 800, 'output_tokens': 50, 'reasoning_output_tokens': 10}
        result = self.run_check('--verifier', 'codex', config={'codex_usage': usage})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('vérification 1050 tokens', result.stdout)
        self.assertEqual(self.recorded('codex_relay')['argv'][0], 'review')

    def test_antigravity_total_is_kept(self):
        result = self.run_check(config={'agy_usage': {'total_tokens': 1234}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('vérification 1234 tokens', result.stdout)


if __name__ == '__main__':
    unittest.main()
