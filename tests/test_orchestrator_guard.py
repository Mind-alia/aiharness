import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import test_hooks_guard as hooks

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'template/scripts/lib/hooks_guard.py'
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))

import orchestrator_guard  # noqa: E402

BLOCKED = 2
ALLOWED = 0
EXPLANATION = "liste blanche de l'orchestrateur (docs/ORCHESTRATEUR.md) ; la commande n'est pas lancée, l'usine continue"
OUTSIDE = 'hors liste blanche'
NETWORK = 'réseau hors des relais connus'
FORCE = '--force hors de .workspace/'
DELETION = "suppression : le propriétaire l'exécute"
REDIRECTION = 'redirection hors de la racine'
CD = 'cd : un seul dossier de la racine est admis'
SED_REASON = "sed : seules sed -n 'N,Mp' et sed -n '$p' sont admises"


class GuardCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.root = self.base / 'project'
        (self.root / 'docs').mkdir(parents=True)
        self.scratch = self.base / 'scratch'
        self.scratch.mkdir()

    def isolate_temp(self):
        patcher = mock.patch.object(orchestrator_guard, '_temp_roots', return_value=[str(self.scratch)])
        patcher.start()
        self.addCleanup(patcher.stop)

    def decide(self, command):
        return orchestrator_guard.decide(command, self.root)

    def assert_permitted(self, commands):
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(self.decide(command))

    def assert_refused(self, commands, reason=None):
        for command in commands:
            with self.subTest(command=command):
                found = self.decide(command)
                self.assertIsNotNone(found)
                self.assertNotIn('\n', found)
                if reason:
                    self.assertIn(reason, found)


class PermittedTests(GuardCase):
    def test_circuit_scripts(self):
        self.assert_permitted(
            [
                'bash scripts/orchestrator.sh beat',
                'sh scripts/gate.sh',
                'bash scripts/task-workspace.sh status',
                './scripts/delegate.sh .workspace/tasks/a.md',
                './workspace status',
                'python3 scripts/workspace.py status',
                'python3 tools/orchestrator/run.py --plan x',
                'python3 tools/campaign/campaign.py run',
                'bash ./scripts/x.sh',
                'bash scripts/../scripts/x.sh',
                'bash %s/scripts/x.sh' % self.root,
            ]
        )

    def test_git_reading_and_committing(self):
        self.assert_permitted(
            [
                'git status',
                'git log --oneline -5',
                'git diff HEAD~1',
                'git show HEAD',
                'git rev-parse HEAD',
                'git rev-list --count HEAD',
                'git ls-files',
                'git ls-tree HEAD',
                'git cat-file -p HEAD',
                'git for-each-ref',
                'git show-ref',
                'git merge-base a b',
                'git describe',
                'git blame AGENTS.md',
                'git grep -n x',
                'git add -A',
                "git commit -m 'scripts: miroir resynchronisé'",
                'git merge --no-ff x',
                'git worktree list',
                'git branch',
                'git config --get user.name',
                'git -C /elsewhere status',
                'git -C .workspace/worktrees/x log',
                'git --no-pager log',
            ]
        )

    def test_tests_mirror_and_docker(self):
        self.assert_permitted(
            [
                'python3 -m unittest tests.test_x',
                '/usr/bin/python3 -m unittest tests.test_x tests.test_y',
                'python3.14 -m unittest tests.test_x tests.test_y',
                'sh tests/run.sh fast',
                'sh tests/run.sh full',
                'rsync -a --exclude __tests__ --exclude __pycache__ template/scripts/ scripts/',
                'docker ps -a',
                'docker ps --format "{{.Names}}"',
                'docker stop workspace-sandbox-x',
            ]
        )

    def test_read_only_commands(self):
        self.assert_permitted(
            [
                'ls docs',
                'ls -la | head -5',
                'cat AGENTS.md',
                'head -n 3 AGENTS.md',
                'tail -n 3 AGENTS.md',
                'wc -l AGENTS.md',
                'grep -rn x docs',
                'rg x docs',
                'sort a',
                'uniq a',
                'cut -d: -f1 a',
                'tr a b',
                'diff a b',
                'date +%F',
                'pwd',
                'echo hi',
                'printf "%s\\n" x',
                'test -f AGENTS.md',
                'true',
                'false',
                'which git',
                'stat AGENTS.md',
                'du -sh docs',
                'ps -ef',
                'pgrep -f x',
                'sleep 5',
                'seq 1 3',
                'basename a/b',
                'dirname a/b',
                'realpath docs',
                'jq . x.json',
                'find . -name "*.py"',
                "sed -n '1,5p' AGENTS.md",
                'cd docs',
                'cd . ; ls',
                'cd docs && ls',
            ]
        )

    def test_escaped_alternation_in_double_quotes(self):
        self.assert_permitted(['grep -n "a\\|b" AGENTS.md', 'echo a\\;b'])

    def test_transparent_prefixes_and_assignments(self):
        self.assert_permitted(
            [
                'env -u WORKSPACE_AGENT_ROOT WORKSPACE_IMAGE_PYTHON=x bash scripts/delegate.sh .workspace/tasks/a.md',
                'WORKSPACE_TEST_TMP=/tmp/x sh tests/run.sh fast',
                'timeout 30 bash scripts/gate.sh',
                'timeout -s KILL 30 bash scripts/gate.sh',
                'env -i HOME=/x ls',
                'nohup bash scripts/gate.sh',
                'time ls',
                'nice -n 5 ls',
                'command -v git',
                'env',
                '/usr/bin/env ls',
                'env -u WORKSPACE_AGENT_ROOT WORKSPACE_IMAGE_PYTHON=workspace-python-git:3.14 bash scripts/delegate.sh '
                '.workspace/tasks/a.md',
                'WORKSPACE_IMAGE_PYTHON=workspace-python-git:3.14 bash scripts/task-workspace.sh merge a',
                'PYTHON=/usr/bin/python3 sh tests/run.sh full',
                'WORKSPACE_TEST_TMP=/tmp/x sh tests/run.sh full',
            ]
        )

    def test_nested_shell(self):
        self.assert_permitted(["bash -c 'git status'", 'sh -c "ls docs; pwd"', "bash -lc 'bash scripts/gate.sh'"])

    def test_pipes_lists_and_redirections(self):
        self.assert_permitted(
            [
                'cat AGENTS.md | grep x > /tmp/y',
                'ls && git status',
                'ls || true',
                'ls ; pwd',
                'ls &',
                'echo x > .workspace/tasks/a.md',
                'echo x >> docs/LOG.md',
                'ls 2>&1 | head',
                'ls 2>/dev/null',
                'ls > /dev/null 2>&1',
                'echo x > ' + tempfile.gettempdir() + '/a',
                'echo x > /tmp/a',
                'echo x > %s/docs/out' % self.root,
                'cat < AGENTS.md',
                'cat <<< hello',
                'ls\npwd',
            ]
        )

    def test_temporary_folders_are_slash_tmp_and_the_system_one(self):
        roots = orchestrator_guard._temp_roots()
        self.assertIn(os.path.realpath('/tmp'), roots)
        self.assertIn(os.path.realpath(tempfile.gettempdir()), roots)

    def test_admitted_substitutions(self):
        self.assert_permitted(
            [
                'echo $(seq 1 3)',
                'echo $(date +%F)',
                'echo $(pwd)',
                'echo "$(pwd)/x"',
                'ls $(pwd)',
                "echo '$(curl x)'",
                "echo '`id`'",
                'echo "a $(seq 1 2) b"',
            ]
        )

    def test_for_loops(self):
        self.assert_permitted(
            [
                'for i in $(seq 1 120); do bash scripts/orchestrator.sh beat; sleep 240; done',
                'for f in a b c; do ls $f; done',
                'for f in a b; do for g in c d; do echo $f$g; done; done',
                'for i in 1 2; do echo $i > /tmp/x; done',
                'for i in 1 2\ndo\n  sleep 1\ndone',
                'for i in 1 2; do ls; done | wc -l',
                'for i in 1; do ls; done > /dev/null',
            ]
        )

    def test_forced_commands_under_workspace(self):
        self.assert_permitted(
            [
                'git -C .workspace/worktrees/x worktree remove --force .workspace/worktrees/x',
                'git worktree remove --force .workspace/worktrees/x',
                'git worktree remove -f .workspace/worktrees/x',
                'git add -f .workspace/tasks/a.md',
                'git worktree remove --force %s/.workspace/worktrees/x' % self.root,
            ]
        )

    def test_close_abandoned_force(self):
        self.assert_permitted(
            [
                'bash scripts/task-workspace.sh close controle-x --abandoned --keep-branch --force',
                'WORKSPACE_IMAGE_PYTHON=img bash scripts/task-workspace.sh close a-b --force --abandoned',
                'bash scripts/task-workspace.sh close a1 --force --keep-branch --abandoned',
            ]
        )


class RefusedTests(GuardCase):
    def test_empty_or_unreadable(self):
        self.assert_refused(['', '   ', '\n', 'echo "unclosed', "echo 'unclosed", 'echo x\\', 'ls \\\npwd'], 'vide ou illisible')
        for command in (None, 12, ['ls']):
            self.assertIn('vide ou illisible', self.decide(command))

    def test_deletion_in_all_its_forms(self):
        self.assert_refused(
            [
                'rm file',
                'rm -rf docs',
                'rm -fr docs',
                'rm -r -f docs',
                'rm -Rf docs',
                'rm --recursive --force docs',
                'rm -f docs/x',
                '/bin/rm -rf docs',
                'env rm -rf docs',
                'FOO=1 rm x',
                'timeout 5 rm x',
                'ls && rm -rf docs',
                'ls | rm x',
                'sh -c "rm -rf docs"',
                "bash -c 'ls; rm x'",
                'command rm x',
            ],
            DELETION,
        )

    def test_network_commands(self):
        names = ('curl', 'wget', 'nc', 'ssh', 'scp', 'sftp', 'ftp', 'telnet', 'npx', 'npm', 'pip', 'brew', 'gh')
        self.assert_refused(['%s https://example.invalid' % name for name in names], NETWORK)
        self.assert_refused(
            [
                'git push origin main',
                'git fetch',
                'git pull',
                'git clone https://x y',
                'git ls-remote origin',
                'git -C .workspace/x push',
                'sh -c \'curl https://x\'',
                'cd docs && curl x',
                'env curl x',
                'ls | curl -d @- x',
                'git push --force',
                'FOO=1 npm install',
            ],
            NETWORK,
        )

    def test_force_outside_workspace(self):
        self.assert_refused(
            [
                'git branch -f main HEAD~1',
                'git branch -D main',
                'git add -f docs/x',
                'git add -f .',
                'git worktree remove --force /elsewhere/x',
                'git worktree remove --force',
                'git worktree remove --force .workspace/worktrees/x docs/y',
                'git worktree remove -f %s/docs' % self.root,
                'git worktree remove --force .workspace',
                'bash scripts/x.sh --force',
                'git worktree remove --force ../x',
                'git worktree remove --force ~/x',
                'git -C docs worktree remove --force .workspace/x',
            ],
            FORCE,
        )

    def test_force_refused_beyond_close_abandoned(self):
        self.assert_refused(
            [
                'bash scripts/task-workspace.sh close x --merged --force',
                'bash scripts/task-workspace.sh close x --force',
                'bash scripts/task-workspace.sh close x --abandoned --merged --force',
                'bash scripts/task-workspace.sh merge x --force',
                'bash scripts/task-workspace.sh prepare t.md --force',
                'bash scripts/task-workspace.sh close X_bad --abandoned --force',
                'bash scripts/orchestrator.sh release --force',
                'bash scripts/task-workspace.sh close x --abandoned --force --force',
                'git branch -D x',
            ],
            FORCE,
        )

    def test_everything_else_is_outside_the_allowlist(self):
        self.assert_refused(
            [
                'python3 -c "print(1)"',
                'python3 script.py',
                'python3 -m pip list',
                'python3 scripts/other.py',
                'mv a b',
                'touch x',
                'chmod +x x',
                'tee x',
                'perl -e 1',
                'node -e 1',
                'ln -s a b',
                'docker run x',
                'docker rm x',
                'docker -H tcp://x ps',
                'git checkout x',
                'git reset --hard',
                'git stash',
                'git',
                'git config user.name x',
                'git config',
                'git -c core.pager=x log',
                'git --exec-path=x status',
                'git remote add x y',
                'git tag x',
                'git rebase main',
                'sudo ls',
                'doas ls',
                'xargs ls',
                'bash',
                'sh x.sh',
                'bash other/x.sh',
                'bash scripts/sub/x.sh',
                'bash ../scripts/x.sh',
                'bash -x scripts/x.sh',
                './x',
                './scripts/x.py',
                'workspace status',
                'scripts/x',
                'eval ls',
                'source x',
                '. x',
                'exec ./x',
                'ls; unknown',
                'ls | python3 -c 1',
                '{ ls; }',
                'sh tests/run.sh',
                'sh tests/run.sh slow',
                'sh tests/other.sh fast',
                'rsync -a x y',
                'rsync -a --exclude __tests__ --exclude __pycache__ template/scripts/ scripts/ x',
            ],
            OUTSIDE,
        )
        self.assert_refused(['$CMD x', '"$CMD" x'], 'expansion dans les arguments de')

    def test_cd_only_goes_to_one_folder_of_the_root(self):
        self.assert_refused(['cd', 'cd ..', 'cd /etc', 'cd ~', 'cd -', 'cd docs docs', 'cd -P docs'], CD)
        self.assert_refused(['cd $HOME'], 'expansion dans les arguments de cd')

    def test_option_that_runs_or_writes(self):
        self.assert_refused(
            [
                'find . -delete',
                'find . -exec ls {} \\;',
                'find . -execdir ls {} \\;',
                'find . -ok ls {} \\;',
                'find . -okdir ls {} \\;',
                'find . -fprint x',
                'find . -fprintf x y',
                'find . -fls x',
                'sed -i s/a/b/ x',
                'sed --in-place s/a/b/ x',
                'sed -ni s/a/b/p x',
                'sed -i.bak s/a/b/ x',
                'sort -o out in',
                'sort --output=out in',
                'sort -ro out in',
                'rg --pre cat x',
                'uniq in out',
                'sort --compress-program=x in',
                'sort -T /x in',
                'sort --temporary-directory=/x in',
                'sort --files0-from=x',
                'sed -f script.sed x',
                'sed --file=script.sed x',
                'sed -nf s.sed x',
                "sed 'w out' x",
                "sed '1w out' x",
                "sed -n '/a/w out' x",
                "sed 's/a/b/w out' x",
                "sed 's/a/b/e' x",
                "sed '1e id' x",
                "sed -e 'W out' x",
                "sed '$e id' x",
                "sed -n 'p;w out' x",
                'date -s 2020-01-01',
                'git log --output=x',
                'git diff --ext-diff',
                'git grep -Ocat x',
                'git grep --open-files-in-pager=cat x',
            ]
        )

    def test_sed_and_find_stay_usable_without_those_options(self):
        self.assert_permitted(["sed -n '1,5p' x", 'find docs -type f -name "*.md"', 'sort -n x', 'sort -k2 x'])

    def test_forms_admitted_by_level_1(self):
        self.assert_permitted(['awk 1', 'if true; then ls; fi', '! ls'])
        self.assert_refused(["awk 'BEGIN{system(\"ls\")}'", 'if true; then rm x; fi', '! rm x'])


SED_PERMITTED = [
    "sed -n '1,5p' docs/LOG.md",
    "sed -n '$p' docs/LOG.md",
    "sed -n '12p' a b",
    "sed -n '3,$p' docs/LOG.md",
    'sed -n "1,5p" docs/LOG.md',
    "cat docs/LOG.md | sed -n '1,3p'",
    "sed -n '1,5p'",
    "sed -n '1,20p' docs/LOG.md",
    "sed -n '$p' x",
    "sed -n '0p' x",
    "sed -n '10,200p' a b c",
]
SED_REFUSED = [
    "sed -n '1,5p;7p' f",
    "sed -E 's/a/b/' f",
    "sed 's/a/b/' f",
    "sed -n '/x/p' f",
    "sed -n '[a]p' f",
    "sed -n '1,5w /tmp/x' f",
    "sed -n -e '1p' f",
    "sed '1,5p' f",
    "sed -n '1,5p' -i f",
    "sed -n '1, 5p' f",
    'sed -n p f',
    "sed -n '1,5p' --in-place f",
    "sed -n '-1p' f",
    "sed -n '1,5P' f",
    "sed -n '1~2p' f",
    "sed -n '1,+3p' f",
    "sed -n '0x10p' f",
    "sed -n '1p;3p' f",
    "sed -n ' 1p' f",
    "sed -n '1p ' f",
    "sed -n '1p\n' f",
    "sed -n '[1p' f",
    "sed -n '1,5p' -- f",
    "sed -n '1,5p' -",
    "sed -n '$,3p' f",
    "sed -n '1,$,3p' f",
    "sed -n '$$p' f",
    "sed -nE '1p' f",
    "sed -n -E '1p' f",
    "sed -En '1p' f",
    "sed -r -n '1p' f",
    "sed -e '1p' f",
    "sed -n -n '1p' f",
    "sed -n '1p' -s f",
    "sed -n '1p' --expression=w f",
    "sed --quiet '1p' f",
    "sed -n --posix '1p' f",
    "sed -n '=' f",
    "sed -n 'p' f",
    "sed -n '1,5r x' f",
    "sed -n '1,5e id' f",
    "sed -n '1,5d' f",
    "sed -n '1,5=' f",
    "sed -n '١p' f",
    'sed',
    'sed -n',
    "sed -n ''",
    "sed '' f",
    "sed -n 1,5p;w x f",
    "sed 's/e/x/' x",
    "sed -n '/error/p' x",
    "sed 's/a/b/g' x",
    "sed -E 's/a+/b/g' x",
    'sed -r s/a/b/ x',
    'sed -nE p x',
    "sed -n -e '/x/p' x",
    "sed -e 's/a/b/' -e 's/c/d/' x",
    "sed -es/a/b/ x",
    'sed -e s/a/b/ x',
    "sed --expression='s/a/b/' x",
    "sed --expression 's/a/b/' x",
    "sed '$!d' x",
    "sed '/a/,/b/d' x",
    "sed '2,+3d' x",
    "sed '0~2p' x",
    "sed -n '1!G;h;$p' x",
    "sed ':a;N;$!ba;s/\\n/ /g' x",
    "sed 's|/a|/b|g' x",
    "sed 's/a/b/2g' x",
    "sed 'y/abc/xyz/' x",
    "sed '/x/{p;d}' x",
    "sed 'a text with w and e' x",
    "sed -n '/a/Ip' x",
    "sed '1d;# w out' x",
    "sed -n p -- x",
]
HARDENED_PERMITTED = [
    'sort -n -k2 -t, f',
    'sort -nr x',
    'sort -k2,2n x',
    'sort -k 2 -t , x',
    'sort -t: -k3n x',
    'sort -u -f -b x',
    'sort -h -V -M -g -d -i -s x',
    'sort -- x',
    'sort -',
    'test -f docs/LOG.md',
    '[ -d docs ]',
    '[ -f a ] && echo yes',
    'test -n x',
    '[ a = b ]',
    'test ! -e x',
    'printf "%s\\n" x',
    "printf '%s' -value",
    'printf -- -v',
]
HARDENED_REFUSED = [
    "sed -e 'e id' x",
    "sed --expression='1e id' x",
    "sed --expression '1e id' x",
    "sed -e'1e id' x",
    "sed 's/a/b/w /tmp/x' f",
    'sed --in-pl s/a/b/ f',
    'sed --in-place s/a/b/ f',
    'sed -i s/a/b/ f',
    'sed -s p x',
    'sed --follow-symlinks p x',
    'sed -f script x',
    'sed -n -f script x',
    'sed --quiet p x',
    'sed -z p x',
    "sed 'rfile' x",
    "sed 'r /etc/passwd' x",
    "sed 'R /etc/passwd' x",
    "sed '1R x' x",
    "sed -n '$r x' x",
    "sed 'wout' x",
    "sed 'w/tmp/x' x",
    "sed '1wout' x",
    "sed 'Wout' x",
    "sed 'eid' x",
    "sed '1e' x",
    "sed -e p -e 'w out' x",
    "sed -n -e p -e '1e id' x",
    "sed 's/a/b/e' x",
    "sed 's/a/b/gw out' x",
    "sed 's,a,b,ew' x",
    "sed '/a/,/b/w out' x",
    "sed '1,3e id' x",
    "sed '2,+3w out' x",
    "sed '0~2w out' x",
    "sed '1!w out' x",
    "sed '{w out}' x",
    "sed '/x/{p;w out}' x",
    "sed 'b l;w out' x",
    "sed ':a w out' x",
    "sed 'y/a/b/;w out' x",
    "sed 's/a/b/;e id' x",
    "sed 'v' x",
    "sed 'k' x",
    "sed '/unclosed' x",
    "sed 's/a/b' x",
    "sed '1,' x",
    "sed '1' x",
    "sed e x",
    "sed -e",
    "sed",
    "sed --expression",
    "sed -n",
    "sed '$N;$e id' x",
    "sed 's/a\\/b/c/;w out' x",
    'sort --out=/tmp/x f',
    'sort --compress-prog=sh f',
    'sort -o /tmp/x f',
    'sort --output /tmp/x f',
    'sort --check f',
    'sort -T /tmp f',
    'sort -S 1G f',
    'sort -z f',
    'sort -m f',
    'sort -c f',
    'sort -nro x f',
    'sort --random-source=x f',
    'sort --files0-from=x',
    'sort --parallel=2 f',
    'sort --reverse f',
    "test -v 'a[$(id)]'",
    "[ -v 'a[$(id)]' ]",
    'test -v x',
    '[ -v x ]',
    'test -f x -a -v y',
    '[ ! -v x ]',
    "printf -v x '%s' y",
    "printf -vx '%s' y",
    "printf -v 'a[$(id)]' '%s' y",
    'printf -v x y',
]


class HardeningTests(GuardCase):
    def test_hardened_commands_stay_permitted(self):
        self.assert_permitted(HARDENED_PERMITTED)

    def test_hardened_commands_are_refused(self):
        self.assert_refused(HARDENED_REFUSED)

    def test_sed_is_reduced_to_two_display_forms(self):
        self.assert_permitted(SED_PERMITTED)
        self.assert_refused(SED_REFUSED)

    def test_sed_reason(self):
        self.assert_refused(
            [
                'sed -i s/a/b/ f',
                'sed --in-pl s/a/b/ f',
                'sed -f s x',
                "sed -e 'e id' x",
                "sed 'wout' x",
                "sed 'rfile' x",
                "sed -n '[a]p' f",
                "cat f | sed -n '1p;3p'",
            ],
            SED_REASON,
        )

    def test_sed_helpers_of_the_former_analysis_are_gone(self):
        for name in ('_sed_scripts', '_sed_after', '_sed_address', '_sed_blanks', '_sed_runs_or_writes'):
            with self.subTest(name=name):
                self.assertFalse(hasattr(orchestrator_guard, name))

    def test_sort_reason(self):
        self.assert_refused(['sort --out=/tmp/x f', 'sort -o x f'], 'option interdite pour sort')

    def test_name_evaluation_reason(self):
        self.assert_refused(["test -v 'a[$(id)]'", "[ -v x ]", "printf -v x y"], '-v évalue un indice')

    def test_bracket_is_a_read_only_command_with_the_same_path_rules(self):
        self.assert_permitted(['[ -d docs ]', '[ -f docs/x ] || echo no'])
        self.assert_refused(['[ -d docs ] > /etc/x'], REDIRECTION)
        self.assert_refused(['[[ -d docs ]]', '[x -d docs ]'], OUTSIDE)

    def test_sed_script_of_several_lines_is_refused(self):
        self.assert_refused(["sed -n 'p;# c\nw out' x", "sed -n '1p\n3p' x", "sed -n '1p\nw out' x"], SED_REASON)

    def test_sed_script_inside_a_nested_shell_and_a_loop_is_judged_too(self):
        self.assert_refused(["sh -c \"sed -e 'e id' x\"", "for f in a b; do sed 'wout' $f; done", "for f in a b; do sed -n p $f; done"])
        self.assert_permitted(["sh -c \"sed -n '1,3p' x\""])
        # Niveau 1, règle 7 (resserrement) : une expansion ne va plus à `sed`.
        self.assert_refused(["for f in a b; do sed -n '1,3p' $f; done"], 'expansion dans les arguments de sed')


class SubstitutionTests(GuardCase):
    def test_other_substitutions_are_refused(self):
        self.assert_refused(
            [
                'echo $(curl x)',
                'echo `date`',
                'echo "`date`"',
                'echo "$(rm x)"',
                'echo $(touch x)',
                'echo $((1+2))',
                'echo $(seq 1 3; ls)',
                'echo $(seq 1 $(touch x))',
                'echo $(FOO=1 date)',
                'echo $(date > x)',
                'echo $(date | cat)',
                'echo $()',
                'cat <(curl x)',
                'tee >(cat)',
                'echo ${x:-$(touch x)}',
                "echo $'x'",
                'echo $"x"',
                'echo "$(date "$(touch x)")"',
                'echo ok; (ls)',
                '(ls)',
                'ls $(pwd',
            ]
        )

    def test_substitution_reasons(self):
        self.assertIn('$(seq', self.decide('echo $(touch x)'))
        self.assertIn('une commande de lecture', self.decide('echo $(touch x)'))
        self.assertIn('accent grave', self.decide('echo `date`'))

    def test_quotes_and_escapes_are_followed(self):
        self.assert_permitted(["echo '$(curl x)'", "echo '`x`'", 'echo \\`x', 'echo "\\`x"', 'echo "\\$(x)"'])
        self.assert_refused(['echo "\\\\$(touch x)"', "echo '$(x)' $(touch x)", 'echo "$(touch x)"'])

    def test_comment_does_not_hide_commands(self):
        self.assert_refused(['echo a#b ; curl x', 'ls # ; curl x', 'ls #\ncurl x'])


class LoopTests(GuardCase):
    def test_unbounded_loops_are_refused(self):
        self.assert_refused(
            [
                'until false; do sleep 1; done',
                'while true; do sleep 1; done',
                'for i in 1; do while true; do sleep 1; done; done',
                'ls; while :; do ls; done',
            ],
            'attente sans borne',
        )

    def test_every_command_of_the_body_is_checked(self):
        self.assert_refused(['for i in 1 2; do curl x; done'], NETWORK)
        self.assert_refused(['for i in 1 2; do ls; rm -rf docs; done'], DELETION)
        self.assert_refused(['for i in 1 2; do python3 -c 1; done'], OUTSIDE)
        self.assert_refused(['for i in 1; do echo x > /etc/passwd; done'], REDIRECTION)
        self.assert_refused(['for i in $(touch x); do ls; done'], '$(seq')

    def test_malformed_loops_are_refused(self):
        self.assert_refused(
            [
                'for i in 1 2',
                'for i in 1 2; do sleep 1',
                'for i in 1 2; sleep 1; done',
                'done',
                'do ls',
                'ls; done',
                'for i; do ls; done',
                'for in x; do ls; done',
                'for 1x in a; do ls; done',
                'for PATH in /tmp; do ls; done',
                'for i in 1; do for j in 2; do ls; done',
                'for i in 1 do ls; done',
            ],
            '',
        )


class PrefixTests(GuardCase):
    def test_dangerous_assignments_are_refused(self):
        self.assert_refused(
            [
                'PATH=/tmp ls',
                'env PATH=/tmp ls',
                'LD_PRELOAD=x ls',
                'DYLD_INSERT_LIBRARIES=x ls',
                'BASH_ENV=x bash scripts/x.sh',
                'GIT_SSH_COMMAND=x git status',
                'GIT_EXTERNAL_DIFF=x git diff',
                'PYTHONPATH=x python3 -m unittest',
                'PAGER=x git log',
                'GIT_PAGER=x git log',
                'EDITOR=x git commit',
                'IFS=x ls',
                'SHELL=x ls',
            ],
            'dangereuse',
        )

    def test_harmless_assignments_are_allowed(self):
        self.assert_permitted(['MYPATH=x ls', 'WORKSPACE_IMAGE_PYTHON=x bash scripts/x.sh', 'LANG=C ls'])

    def test_prefixes_with_unknown_options_or_privileges_are_refused(self):
        self.assert_refused(
            [
                'sudo ls',
                'sudo -n ls',
                'doas ls',
                'xargs ls',
                'stdbuf -oL ls',
                'env -S "ls -la"',
                'env -a x ls',
                'env -P /x ls',
                'env -C /tmp ls',
                'env --chdir=/tmp ls',
                'timeout --kill ls',
                'timeout -x 5 ls',
                'nice -n5 ls',
                'exec -a x ls',
                'nohup -x ls',
                'time -f x ls',
                '/bin/env ls',
                './env ls',
            ],
            OUTSIDE,
        )


class PathTests(GuardCase):
    def setUp(self):
        super().setUp()
        self.isolate_temp()

    def test_redirections_outside_are_refused(self):
        self.assert_refused(
            [
                'echo x > /etc/passwd',
                'echo x >> ~/notes',
                'echo x > ~/x',
                'echo x > ../outside',
                'echo x > $HOME/x',
                'ls > "$TMP/x"',
                'ls 2> /var/log/x',
                'ls &> /etc/x',
                'ls >| /etc/x',
                'cd docs && echo x > ../../x',
                'echo x > /tmp/../etc/x',
                'ls > /dev/tty',
                'echo x > %s/../x' % self.scratch,
            ],
            REDIRECTION,
        )

    def test_temporary_folders_are_allowed(self):
        self.assert_permitted(['echo x > %s/a/b' % self.scratch, 'echo x >> %s/z' % self.scratch, 'echo x > ../project/docs/x'])

    def test_a_link_leaving_the_root_is_followed(self):
        outside = self.base / 'outside'
        outside.mkdir()
        os.symlink(outside, self.root / 'docs/link')
        self.assert_refused(['echo x > docs/link/x'], REDIRECTION)
        self.assert_refused(['cd docs/link'], CD)

    def test_cd_moves_the_base_of_relative_paths(self):
        (self.root / 'sub/scripts').mkdir(parents=True)
        self.assert_refused(['cd sub && bash scripts/x.sh', 'cd sub; ./scripts/x.sh'], OUTSIDE)
        self.assert_permitted(['cd sub && cd .. && bash scripts/x.sh'])

    def test_script_path_must_resolve_inside_the_root(self):
        self.assert_refused(['bash /other/scripts/x.sh', 'bash scripts/x.sh/../../../x.sh', 'bash ~/scripts/x.sh'], OUTSIDE)
        self.assert_refused(['bash $HOME/scripts/x.sh'], 'expansion dans les arguments de bash')

    def test_a_linked_scripts_folder_leaving_the_root_is_refused(self):
        outside = self.root.parent / 'elsewhere'
        outside.mkdir()
        os.symlink(outside, self.root / 'scripts')
        self.assert_refused(['bash scripts/x.sh', './scripts/x.sh'], OUTSIDE)


class NestingTests(GuardCase):
    def test_nesting_is_bounded(self):
        command = 'curl x'
        for _ in range(7):
            command = 'sh -c ' + json.dumps(command)
        self.assert_refused([command])
        command = 'ls'
        for _ in range(3):
            command = 'sh -c ' + json.dumps(command)
        self.assert_permitted([command])

    def test_nested_scripts_follow_every_rule(self):
        self.assert_refused(
            [
                'sh -c "while true; do ls; done"',
                "sh -c 'echo $(touch x)'",
                "bash -c 'rm x'",
                'sh -c "git push"',
                'sh -c "sh -c \\"curl x\\""',
            ]
        )

    def test_decide_accepts_a_string_root(self):
        self.assertIsNone(orchestrator_guard.decide('git status', str(self.root)))


class HookCase(hooks.HooksGuardTests):
    def check(self, command, tool='Bash', raw=None, args=None):
        payload = raw if raw is not None else json.dumps({'tool_name': tool, 'tool_input': {'command': command}, 'cwd': str(self.root)})
        env = {key: value for key, value in os.environ.items() if key not in hooks.STRIPPED_ENV}
        done = subprocess.run(
            [sys.executable, str(SCRIPT), 'orchestrator-check'] + (args if args is not None else [str(self.root)]),
            input=payload,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
        )
        return done.returncode, done.stdout, done.stderr

    def journal(self):
        path = self.root / '.workspace/events.jsonl'
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


class OrchestratorCheckTests(HookCase):
    def test_permitted_command_exits_0_silently_and_leaves_no_event(self):
        self.assertEqual(self.check('git status'), (ALLOWED, '', ''))
        self.assertEqual(self.journal(), [])

    def test_each_refusal_exits_2_with_one_line_and_is_journaled(self):
        for command in ('curl https://example.invalid', 'rm -rf docs', 'git push origin main'):
            with self.subTest(command=command):
                code, out, err = self.check(command)
                self.assertEqual((code, out), (BLOCKED, ''))
                self.assertEqual(len(err.splitlines()), 1, err)
                self.assertIn(EXPLANATION, err)
        events = self.journal()
        self.assertEqual([item['event'] for item in events], ['tool_refused'] * 3)
        self.assertEqual(
            [item['detail']['target'] for item in events], ['curl https://example.invalid', 'rm -rf docs', 'git push origin main']
        )
        for item in events:
            self.assertEqual(item['task_id'], 'orchestrator')
            self.assertEqual(sorted(item['detail']), ['reason', 'role', 'target', 'tool'])
            self.assertEqual((item['detail']['role'], item['detail']['tool']), ('orchestrator', 'Bash'))
        self.assertEqual([item['detail']['reason'] for item in events], [NETWORK, DELETION, NETWORK])

    def test_hardened_cases_exit_2_when_refused_and_0_when_permitted(self):
        for command in (
            "sed -e 'e id' x",
            "sed --expression='1e id' x",
            "sed 's/a/b/w /tmp/x' f",
            'sed --in-pl s/a/b/ f',
            'sort --out=/tmp/x f',
            'sort --compress-prog=sh f',
            'sort -o /tmp/x f',
            "test -v 'a[$(id)]'",
            "printf -v x '%s' y",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.check(command)[0], BLOCKED)
        for command in ("sed -n '1,20p' docs/LOG.md", 'sort -n -k2 -t, f', 'test -f docs/LOG.md', '[ -d docs ]'):
            with self.subTest(command=command):
                self.assertEqual(self.check(command), (ALLOWED, '', ''))

    def test_sed_cases_exit_0_when_permitted_and_2_when_refused(self):
        for command in SED_PERMITTED:
            with self.subTest(command=command):
                self.assertEqual(self.check(command), (ALLOWED, '', ''))
        for command in SED_REFUSED:
            with self.subTest(command=command):
                self.assertEqual(self.check(command)[0], BLOCKED)

    def test_reason_comes_first_on_the_line(self):
        _, _, err = self.check('rm -rf docs')
        self.assertIn(DELETION, err)
        self.assertLess(err.index(DELETION), err.index(EXPLANATION))

    def test_task_id_is_the_session_of_the_lock_even_when_the_holder_is_not_this_process(self):
        self.hold_lock(self.live_non_ancestor(), session='orchestrateur-7')
        self.assertEqual(self.check('curl x')[0], BLOCKED)
        self.assertEqual(self.journal()[0]['task_id'], 'orchestrateur-7')
        self.assertEqual(self.journal()[0]['orchestrator'], 'orchestrateur-7')

    def test_unreadable_lock_gives_the_orchestrator_task_id(self):
        lock = self.root / '.workspace/orchestrator.json'
        lock.parent.mkdir()
        lock.write_text('pas du json')
        self.assertEqual(self.check('curl x')[0], BLOCKED)
        self.assertEqual(self.journal()[0]['task_id'], 'orchestrator')

    def test_target_is_bounded_to_200_characters(self):
        command = 'curl ' + 'x' * 400
        self.assertEqual(self.check(command)[0], BLOCKED)
        self.assertEqual(self.journal()[0]['detail']['target'], command[:200])

    def test_unwritable_journal_does_not_change_the_decision(self):
        (self.root / '.workspace').mkdir()
        (self.root / '.workspace/events.jsonl').mkdir()
        code, _, err = self.check('curl x')
        self.assertEqual(code, BLOCKED)
        self.assertEqual(len(err.splitlines()), 1, err)

    def test_root_that_does_not_exist_still_refuses(self):
        code, _, err = self.check('curl x', args=[str(self.root / 'absent')])
        self.assertEqual(code, BLOCKED)
        self.assertEqual(len(err.splitlines()), 1, err)

    def test_the_lock_is_not_needed(self):
        self.assertEqual(self.check('git status')[0], ALLOWED)
        self.assertEqual(self.check('python3 -c 1')[0], BLOCKED)

    def test_other_tools_are_not_judged(self):
        payload = json.dumps({'tool_name': 'Write', 'tool_input': {'file_path': 'scripts/x'}})
        self.assertEqual(self.check(None, raw=payload), (ALLOWED, '', ''))

    def test_unreadable_input_and_wrong_arguments_are_refused(self):
        for raw in ('', 'pas du json', '[]'):
            with self.subTest(raw=raw):
                self.assertEqual(self.check(None, raw=raw)[0], BLOCKED)
        self.assertEqual(self.check('git status', args=[])[0], BLOCKED)
        self.assertEqual(self.check('git status', args=['a', 'b'])[0], BLOCKED)

    def test_bash_payload_without_a_command_string_is_not_judged(self):
        payload = json.dumps({'tool_name': 'Bash', 'tool_input': {'description': 'rien'}})
        self.assertEqual(self.check(None, raw=payload)[0], ALLOWED)

    def test_empty_command_is_refused(self):
        self.assertEqual(self.check('')[0], BLOCKED)


PROBE = (
    "import runpy, sys\n"
    "sys.argv = sys.argv[1:]\n"
    "try:\n"
    "    runpy.run_path(sys.argv[0], run_name='__main__')\n"
    "except SystemExit as stop:\n"
    "    print(stop.code, 'orchestrator_guard' in sys.modules)\n"
)


class OrchestratorSessionTests(HookCase):
    """`orchestrator-session <kit>` : session ouverte depuis un autre dossier que le kit (ici le dossier parent)."""

    def setUp(self):
        super().setUp()
        self.elsewhere = self.root.parent

    def session(self, command, project=None, tool='Bash', raw=None, extra_env=None, args=None, probe=False):  # noqa: PLR0913
        payload = (
            raw
            if raw is not None
            else json.dumps({'tool_name': tool, 'tool_input': {'command': command}, 'cwd': str(project or self.elsewhere)})
        )
        env = {key: value for key, value in os.environ.items() if key not in hooks.STRIPPED_ENV}
        env.update(extra_env or {})
        if project is not None:
            env['CLAUDE_PROJECT_DIR'] = str(project)
        line = (
            [sys.executable]
            + (['-c', PROBE] if probe else [])
            + [str(SCRIPT), 'orchestrator-session']
            + (args if args is not None else [str(self.root)])
        )
        done = subprocess.run(line, input=payload, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        return done.returncode, done.stdout, done.stderr

    def test_without_a_lock_everything_is_off_topic_and_nothing_is_written(self):
        self.assertEqual(self.session('rm -rf /'), (ALLOWED, '', ''))
        self.assertFalse((self.root / '.workspace').exists())
        self.assertEqual(self.journal(), [])

    def test_without_a_lock_the_allowlist_is_not_even_imported(self):
        code, out, _ = self.session('rm -rf /', probe=True)
        self.assertEqual((code, out), (0, '0 False\n'))
        self.hold_lock(os.getpid())
        out = self.session('git status', probe=True)[1]
        self.assertEqual(out, '0 True\n')

    def test_holder_is_refused_a_command_outside_the_list_and_it_is_journaled(self):
        self.hold_lock(os.getpid(), session='orchestrateur-9')
        code, out, err = self.session('rm -rf x')
        self.assertEqual((code, out), (BLOCKED, ''))
        self.assertEqual(len(err.splitlines()), 1, err)
        self.assertIn(DELETION, err)
        self.assertIn(EXPLANATION, err)
        (item,) = self.journal()
        self.assertEqual((item['event'], item['task_id']), ('tool_refused', 'orchestrateur-9'))
        self.assertEqual(item['detail'], {'role': 'orchestrator', 'tool': 'Bash', 'reason': DELETION, 'target': 'rm -rf x'})

    def test_holder_may_run_listed_commands(self):
        self.hold_lock(os.getpid())
        for command in ('git status', "sed -n '1,5p' docs/LOG.md", 'bash scripts/orchestrator.sh beat'):
            with self.subTest(command=command):
                self.assertEqual(self.session(command), (ALLOWED, '', ''))
        self.assertEqual(self.journal(), [])

    def test_holder_is_judged_when_the_project_dir_is_given_by_the_environment(self):
        self.hold_lock(os.getpid())
        self.assertEqual(self.session('curl x', project=self.elsewhere)[0], BLOCKED)

    def test_the_kit_itself_is_left_to_its_own_pre_tool_hook(self):
        self.hold_lock(os.getpid())
        self.assertEqual(self.session('rm -rf x', project=self.root), (ALLOWED, '', ''))
        self.assertEqual(
            self.session('rm -rf x', raw=json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'rm -rf x'}, 'cwd': str(self.root)})),
            (ALLOWED, '', ''),
        )
        self.assertEqual(self.journal(), [])

    def test_an_executor_is_not_judged(self):
        self.hold_lock(os.getpid())
        self.assertEqual(self.session('rm -rf x', extra_env={'WORKSPACE_TASK_ID': 'T1'}), (ALLOWED, '', ''))
        self.assertEqual(self.journal(), [])

    def test_lock_of_another_process_or_unreadable_gives_no_judgement(self):
        self.hold_lock(self.live_non_ancestor())
        self.assertEqual(self.session('rm -rf x'), (ALLOWED, '', ''))
        self.hold_lock(hooks.dead_pid())
        self.assertEqual(self.session('rm -rf x'), (ALLOWED, '', ''))
        (self.root / '.workspace/orchestrator.json').write_text('pas du json')
        self.assertEqual(self.session('rm -rf x'), (ALLOWED, '', ''))
        self.assertEqual(self.journal(), [])

    def test_unreadable_input_other_tools_and_missing_command_are_off_topic(self):
        self.hold_lock(os.getpid())
        for raw in (
            '',
            'pas du json',
            '[]',
            json.dumps({'tool_name': 'Write', 'tool_input': {'file_path': 'scripts/x'}}),
            json.dumps({'tool_name': 'Bash', 'tool_input': {'description': 'rien'}}),
            json.dumps({'tool_name': 'Bash'}),
            json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 3}}),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(self.session(None, raw=raw), (ALLOWED, '', ''))
        self.assertEqual(self.journal(), [])

    def test_wrong_argument_count_is_a_usage_error_naming_the_mode(self):
        for args in ([], ['a', 'b']):
            with self.subTest(args=args):
                code, out, err = self.session('git status', args=args)
                self.assertEqual((code, out), (64, ''))
                self.assertIn('orchestrator-session', err)

    def test_usage_message_lists_the_new_mode(self):
        code, _, err = hooks.run_hook('nope', self.root, {})
        self.assertEqual(code, 64)
        self.assertIn('orchestrator-session', err)


class LockHolderTests(HookCase):
    def test_holder_is_refused_a_command_outside_the_list_with_one_line(self):
        self.hold_lock(os.getpid(), session='orchestrateur-42')
        code, out, err = self.bash('python3 -c "print(1)"')
        self.assertEqual((code, out), (BLOCKED, ''))
        self.assertEqual(len(err.splitlines()), 1, err)
        self.assertIn(OUTSIDE, err)
        self.assertIn(EXPLANATION, err)

    def test_holder_refusal_is_journaled_under_the_session(self):
        self.hold_lock(os.getpid(), session='orchestrateur-42')
        self.bash('curl https://example.invalid')
        (item,) = self.journal()
        self.assertEqual((item['event'], item['task_id']), ('tool_refused', 'orchestrateur-42'))
        self.assertEqual(
            item['detail'], {'role': 'orchestrator', 'tool': 'Bash', 'reason': NETWORK, 'target': 'curl https://example.invalid'}
        )

    def test_holder_keeps_the_exception_for_the_other_tools(self):
        self.hold_lock(os.getpid())
        for tool, tool_input in (
            ('Write', {'file_path': 'scripts/x.sh', 'content': ''}),
            ('Edit', {'file_path': '.workspace/tasks/a.md'}),
            ('MultiEdit', {'file_path': 'project.profile'}),
            ('Read', {'file_path': 'scripts/gate.sh'}),
        ):
            with self.subTest(tool=tool):
                self.assertEqual(self.pre_tool(tool, tool_input), (ALLOWED, '', ''))
        self.assertEqual(self.journal(), [])

    def test_holder_may_run_listed_commands_and_write_the_task_sheets(self):
        self.hold_lock(os.getpid())
        for command in (
            'git status',
            'echo x > .workspace/tasks/a.md',
            'bash scripts/orchestrator.sh beat',
            'for i in $(seq 1 3); do sleep 1; done',
        ):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command), (ALLOWED, '', ''))

    def test_holder_through_a_shell_is_judged_too(self):
        self.hold_lock(os.getpid())
        code, _, err = self.pre_tool('Bash', {'command': 'curl x'}, via=hooks.VIA_SHELL)
        self.assertEqual(code, BLOCKED)
        self.assertIn(EXPLANATION, err)

    def test_holder_with_a_task_id_is_an_executor_not_an_orchestrator(self):
        self.hold_lock(os.getpid())
        extra = {'WORKSPACE_TASK_ID': 'T1'}
        self.assertEqual(self.bash('python3 -c 1', extra), (ALLOWED, '', ''))
        self.assertEqual(self.bash('rm scripts/x', extra)[0], BLOCKED)
        self.assertEqual(self.journal(), [])

    def test_without_the_lock_nothing_changes(self):
        self.assertEqual(self.bash('python3 -c "print(1)"'), (ALLOWED, '', ''))
        self.assertEqual(self.bash('curl x'), (ALLOWED, '', ''))
        self.assertEqual(self.bash('rm scripts/x')[0], BLOCKED)
        self.assertEqual(self.journal(), [])

    def test_lock_of_a_live_non_ancestor_or_dead_process_gives_no_judgement(self):
        self.hold_lock(self.live_non_ancestor())
        self.assertEqual(self.bash('curl x'), (ALLOWED, '', ''))
        self.hold_lock(hooks.dead_pid())
        self.assertEqual(self.bash('curl x'), (ALLOWED, '', ''))

    def test_unwritable_journal_does_not_change_the_holder_decision(self):
        self.hold_lock(os.getpid())
        (self.root / '.workspace/events.jsonl').mkdir()
        code, _, err = self.bash('curl x')
        self.assertEqual(code, BLOCKED)
        self.assertEqual(len(err.splitlines()), 1, err)

    def test_holder_bash_payload_without_a_command_is_allowed(self):
        self.hold_lock(os.getpid())
        self.assertEqual(self.pre_tool('Bash', {'description': 'rien'}), (ALLOWED, '', ''))


class RequiredFormsTests(GuardCase):
    """Formes que docs/ORCHESTRATEUR.md exige : attente bornée, clone de tâche, mkdir temporaire, git mv."""

    def setUp(self):
        super().setUp()
        self.isolate_temp()
        self.work = self.base / 'work'
        (self.work / 'demo').mkdir(parents=True)
        (self.work / 'other-1' / 'demo').mkdir(parents=True)
        patcher = mock.patch.object(orchestrator_guard.workdir, 'work_base', return_value=self.work)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_break_and_continue_inside_a_for_loop(self):
        self.assert_permitted(
            [
                "for i in $(seq 1 50); do grep -q '^exit' /tmp/x.output && break; sleep 10; done",
                'for i in $(seq 1 3); do test -f /tmp/x && break; sleep 1; done',
                'for i in $(seq 1 3); do continue 2; done',
            ]
        )

    def test_break_and_continue_elsewhere_are_refused(self):
        self.assert_refused(['break', 'true && break', 'continue', 'sh -c "break"'], "hors d'une boucle for")
        self.assert_refused(
            [
                'for i in 1; do break x; done',
                'for i in 1; do break 0; done',
                'for i in 1; do break 1 2; done',
                'for i in 1; do continue -1; done',
            ],
            "hors d'une boucle for",
        )

    def test_cd_into_a_task_clone(self):
        work = str(self.work)
        self.assert_permitted(
            [
                'cd %s/demo && WORKSPACE_TEST_TMP=/tmp/tt PYTHON=/usr/bin/python3 sh tests/run.sh full' % work,
                'cd %s/demo && git diff --stat && git log --oneline -3' % work,
                'cd %s/demo && cd %s && ls docs' % (work, self.root),
                'cd %s/demo && cd %s && bash scripts/orchestrator.sh beat' % (work, self.root),
                'cd %s/demo && sh tests/run.sh fast' % work,
                "cd %s/demo && python3 -m unittest tests.test_x && sed -n '1,5p' AGENTS.md && cat AGENTS.md" % work,
                'cd %s/demo && git config --get user.name && git show HEAD && git grep -n x' % work,
            ]
        )

    def test_circuit_scripts_and_git_writing_are_refused_in_a_task_clone(self):
        work = str(self.work)
        reason = 'dans un clone de tâche : tests et lecture seulement'
        scripts = [
            'bash scripts/orchestrator.sh status',
            'sh scripts/gate.sh',
            './scripts/x.sh',
            './workspace status',
            'python3 scripts/workspace.py status',
            'python3 tools/orchestrator/run.py',
            'python3 tools/campaign/campaign.py',
            'bash scripts/task-workspace.sh merge demo',
            'bash %s/scripts/x.sh' % self.root,
        ]
        git = ['git commit -m x', 'git add .', 'git merge x', 'git worktree list', 'git branch x', 'git branch', 'git mv a b']
        self.assert_refused(['cd %s/demo && %s' % (work, item) for item in scripts + git], reason)
        self.assert_permitted(['git commit -m x', 'git branch x'])

    def test_git_dash_c_cannot_write_in_a_task_clone_or_outside_the_project(self):
        clone = str(self.work / 'demo')
        reason = 'dans un clone de tâche : tests et lecture seulement'
        self.assert_refused(
            [
                'git -C %s commit -m x' % clone,
                'git -C %s add .' % clone,
                'git -C %s branch y' % clone,
                'git -C %s worktree list' % clone,
                'git -C /etc commit -m x',
                'git -C ~ commit -m x',
                'cd docs && git -C ../../ailleurs add x',
                'git -C docs -C %s merge x' % clone,
            ],
            reason,
        )
        self.assert_refused(['git -C $HOME commit -m x'], 'expansion dans les arguments de git')
        self.assert_permitted(
            [
                'git -C %s log --oneline -3' % clone,
                'git -C %s diff --stat' % clone,
                'git -C docs add a.md',
                'git -C . commit -q -m x',
                'cd docs && git -C .. add x',
            ]
        )

    def test_git_writing_subcommand_takes_a_single_dash_c(self):
        reason = 'dans un clone de tâche : tests et lecture seulement'
        self.assert_refused(
            [
                'git -C docs -C .. add x',
                'git -C . -C docs commit -m x',
                'git -C docs -C ../../ailleurs commit',
                'git -C . -C . merge x',
                'git -C . -C . worktree list',
                'git -C . -C . branch y',
                'git -C docs -C .. mv a b',
            ],
            reason,
        )
        self.assert_permitted(['git -C docs -C .. log --oneline -1', 'git -C docs add a.md', 'git -C . -C docs status'])

    def test_git_mv_keeps_the_circuit_paths(self):
        for command in (
            'git mv scripts/lib/hooks_guard.py x.py',
            'git mv docs/a.md .claude/settings.json',
            'git mv AGENTS.md A.md',
            'git mv a.md project.profile',
            'git mv .workspace/x y',
            'git mv docs/a.md .git/x',
            'git mv scripts x',
            'git mv x %s/scripts/y' % self.root,
            'git -C docs mv a b',
            'git mv -f scripts/x y',
        ):
            with self.subTest(command=command):
                self.assertIn('git mv : chemin du circuit protégé', self.decide(command))
        self.assert_permitted(['git mv docs/a.md docs/b.md', 'git mv docs/AGENTS.md docs/scripts', 'git mv a.md docs/'])

    def test_cd_elsewhere_is_still_refused(self):
        work = str(self.work)
        self.assert_refused(
            [
                'cd ' + work,
                'cd %s/../autre' % work,
                'cd %s/demo/..' % work,
                'cd %s/demo && cd ..' % work,
                'cd %s/demo && cd -' % work,
                'cd %s/demo && cd' % work,
            ],
            CD,
        )
        self.assert_refused(['cd %s/demo && rm -rf x' % work], DELETION)
        self.assert_refused(['cd %s/demo && ls > /etc/x' % work], REDIRECTION)

    def test_relative_paths_follow_the_task_clone(self):
        work = str(self.work)
        self.assert_refused(['cd %s/demo && cd .. && bash scripts/x.sh' % work], CD)
        self.assert_refused(['cd %s/demo && ./other/scripts/x.sh' % work], OUTSIDE)
        self.assert_permitted(['cd %s/demo && ls > docs/x' % work])

    def test_mkdir_only_under_a_temporary_folder(self):
        scratch = str(self.scratch)
        self.assert_permitted(['mkdir -p %s/tt' % scratch, 'mkdir %s/a %s/b' % (scratch, scratch), 'mkdir -p %s/a/b/c' % scratch])
        reason = 'mkdir : seulement sous un dossier temporaire'
        self.assert_refused(
            [
                'mkdir x',
                'mkdir docs/x',
                'mkdir -m 777 %s/x' % scratch,
                'mkdir -p ~/x',
                'mkdir',
                'mkdir -p',
                'mkdir -pv %s/x' % scratch,
                'mkdir %s/../x' % scratch,
                'mkdir %s/a docs/b' % scratch,
            ],
            reason,
        )
        self.assert_refused(['mkdir -p $HOME/x'], 'expansion dans les arguments de mkdir')
        self.assert_refused(['cp a $HOME/x'], 'expansion dans les arguments de cp')
        self.assert_refused(['git mv -k a $HOME/x'], 'expansion dans les arguments de git')

    def test_git_mv(self):
        self.assert_permitted(['git mv docs/a.md docs/b.md'])
        self.assert_refused(['git mv -f docs/a.md docs/b.md', 'git mv --force docs/a.md docs/b.md'], FORCE)


if __name__ == '__main__':
    unittest.main()
