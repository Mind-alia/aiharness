import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.test_orchestrator_guard import CD, DELETION, OUTSIDE, GuardCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'template/scripts/lib'))

import orchestrator_guard  # noqa: E402

CP = 'cp : seulement vers un dossier temporaire'
AWK = 'awk : écriture ou commande interdite'
IF = 'if mal formé'
EXPANSION = 'expansion dans les arguments de'
SUBSTITUTION = 'une commande de lecture'


class Level1Case(GuardCase):
    def setUp(self):
        super().setUp()
        self.isolate_temp()
        self.tmp = str(self.scratch)


class CdTests(Level1Case):
    def test_cd_into_a_temporary_folder(self):
        (self.scratch / 'sub').mkdir()
        self.assert_permitted(
            [
                'cd %s' % self.tmp,
                'cd %s/sub && ls' % self.tmp,
                'cd %s && cd sub && cd .. && ls' % self.tmp,
                'cd %s && cd %s && ls docs' % (self.tmp, self.root),
            ]
        )

    def test_commands_after_such_a_cd_keep_the_same_lists(self):
        self.assert_refused(['cd %s && rm x' % self.tmp], DELETION)
        self.assert_refused(['cd %s && curl x' % self.tmp], 'réseau')
        # Resserrement (liste blanche du dossier temporaire) : tout ce qui n'est pas lecture ou copie reçoit la raison dédiée.
        self.assert_refused(
            ['cd %s && python3 -c 1' % self.tmp, 'cd %s && bash scripts/x.sh' % self.tmp, 'cd %s && git status' % self.tmp],
            'dossier temporaire : lecture et copie seulement',
        )
        self.assert_permitted(['cd %s && echo x > out' % self.tmp])

    def test_relative_paths_resolve_from_the_temporary_folder(self):
        self.assert_refused(['cd %s && echo x > ../../etc/x' % self.tmp], 'redirection')

    def test_other_folders_are_still_refused(self):
        self.assert_refused(
            ['cd /etc', 'cd %s' % self.base, 'cd %s/../x' % self.tmp, 'cd %s && cd /etc' % self.tmp, 'cd %s && cd ..' % self.tmp], CD
        )

    def test_a_project_inside_the_temporary_folder_is_not_a_temporary_folder(self):
        with mock.patch.object(orchestrator_guard, '_temp_roots', return_value=[str(self.base)]):
            self.assert_refused(['cd %s' % self.base, 'cd %s/..' % self.base], CD)
            self.assert_permitted(['cd %s' % self.scratch, 'cd %s/docs && ls' % self.root])
            self.assert_refused(['cd %s && rm x' % self.scratch], DELETION)


class CpTests(Level1Case):
    def test_copy_to_a_temporary_folder(self):
        self.assert_permitted(
            [
                'cp a %s/x' % self.tmp,
                'cp -r docs %s/d' % self.tmp,
                'cp -p -R a b %s' % self.tmp,
                'cp /etc/hosts %s/h' % self.tmp,
                'cp a %s/new/x' % self.tmp,
                'cd %s && cp /etc/hosts h' % self.tmp,
            ]
        )

    def test_copy_elsewhere_is_refused(self):
        self.assert_refused(
            [
                'cp a docs/x',
                'cp a /etc/x',
                'cp -f a %s/x' % self.tmp,
                'cp -a a %s/x' % self.tmp,
                'cp a',
                'cp',
                'cp -r',
                'cp a %s/../x' % self.tmp,
                'cp a ~/x',
                'cp a %s/*' % self.tmp,
                'cp a %s/{x,y}' % self.tmp,
                'cp a %s' % self.root,
                'cp a %s/x' % self.root,
                'cp a b',
                'cp --parents a %s/x' % self.tmp,
                'cp -rf a %s/x' % self.tmp,
                'cp -- a %s/x' % self.tmp,
                'cp -t %s a' % self.tmp,
            ],
            CP,
        )

    def test_a_link_leaving_the_temporary_folder_is_followed(self):
        outside = self.base / 'outside'
        outside.mkdir()
        os.symlink(outside, self.scratch / 'link')
        os.symlink(self.root / 'docs', self.scratch / 'inproject')
        self.assert_refused(['cp a %s/link/x' % self.tmp, 'cp a %s/inproject/x' % self.tmp], CP)

    def test_a_project_inside_the_temporary_folder_is_refused(self):
        with mock.patch.object(orchestrator_guard, '_temp_roots', return_value=[str(self.base)]):
            self.assert_refused(['cp a %s/x' % self.root, 'cp a %s/docs/x' % self.root, 'cp a %s' % self.root], CP)
            self.assert_permitted(['cp a %s/x' % self.scratch])


class SubstitutionTests(Level1Case):
    def test_substitution_of_a_read_command(self):
        self.assert_permitted(
            [
                'x=$(cat f)',
                'echo $(cat f)',
                'x=$(jq -r .a f)',
                'for f in $(ls docs); do echo $f; done',
                'echo "$(head -n 1 f)"',
                'x=$(echo $(cat f))',
                'x=$(grep -c a f)',
                'x=$(awk 1 f)',
            ]
        )

    def test_other_substitutions_are_refused(self):
        self.assert_refused(
            [
                'x=$(python3 -c 1)',
                'x=$(cat f > g)',
                '$(rm x)',
                'x=$(cat f; ls)',
                'x=$(cat f | wc -l)',
                'x=$(FOO=1 cat f)',
                'x=$(cat $(rm x))',
                'x=$(cd /tmp)',
                'x=$(if true; then ls; fi)',
            ],
            SUBSTITUTION,
        )

    def test_options_are_still_judged_inside(self):
        self.assert_refused(
            ['x=$(date -s 2020-01-01)', 'x=$(awk "BEGIN{system(1)}")', 'x=$(find . -delete)', 'x=$(rg --pre x y)', 'x=$(sed -i s/a/b/ f)']
        )


class IfTests(Level1Case):
    def test_if_and_negation(self):
        self.assert_permitted(
            [
                'if true; then ls; fi',
                '! ls',
                'if ! grep -q x f; then echo no; fi',
                'if test -f f; then ls; else pwd; fi',
                'if true; then ls; elif false; then pwd; else echo x; fi',
                'if true\nthen\n  ls\nfi',
                'for i in 1 2; do if test -f $i; then echo $i; fi; done',
                'if true; then if false; then ls; fi; fi',
                'if true; then ! ls; fi',
                'ls && ! grep x f',
            ]
        )

    def test_the_command_after_each_keyword_is_judged(self):
        self.assert_refused(
            [
                'if rm x; then ls; fi',
                'if true; then rm x; fi',
                'if true; then ls; else rm x; fi',
                'if true; then ls; elif rm x; then ls; fi',
                '! rm x',
                'if ! rm x; then ls; fi',
            ],
            DELETION,
        )
        self.assert_refused(['if true; then python3 -c 1; fi', 'if curl x; then ls; fi'])

    def test_malformed_if(self):
        self.assert_refused(
            [
                'if true; then ls',
                'if true',
                'fi',
                'then ls',
                'else ls',
                'elif true; then ls',
                'ls; fi',
                'if true; then ls; fi; fi',
                'if true; else ls; fi',
                'if true; then ls; else ls; elif x; fi',
                'if true; then ls; else ls; else ls; fi',
                'if true; fi',
                'sh -c "if true; then ls"',
            ],
            IF,
        )

    def test_other_compound_forms_stay_refused(self):
        self.assert_refused(
            [
                'while true; do ls; done',
                'until false; do ls; done',
                'case x in x) ls;; esac',
                '{ ls; }',
                'if true; then while true; do ls; done; fi',
            ]
        )


class SetTests(Level1Case):
    def test_set_positional(self):
        self.assert_permitted(['set -- a b c', 'set --'])
        self.assert_refused(['set -e', 'set +x', 'set', 'set -o pipefail', 'set a b', 'set -- $(touch x)'])
        self.assert_refused(['set -- $(cat f)', 'set -- $1'], EXPANSION)
        self.assert_refused(['set -e', 'set'], OUTSIDE)


class AwkTests(Level1Case):
    def test_awk_reads(self):
        self.assert_permitted(
            [
                'awk 1',
                "awk '$3 > 5 {print $1}' f",
                "awk -F: '{print $1}' f",
                "awk -F : '{print $1}' f",
                "awk -v n=3 '$1 > n' f",
                "awk -vn=3 '$1 > n' f",
                "awk -F, -v a=1 '{print $2, a}' f",
                'awk "{print NR}" f',
                "cat f | awk '{s += $1} END {print s}'",
                "awk '$1 > 2' f",
                "awk '{ if ($1 > 2) print $1 }' f",
                "awk -- '{print}' f",
                "awk 'NR > 1; {print}' f",
            ]
        )

    def test_awk_that_writes_or_runs_is_refused(self):
        self.assert_refused(
            [
                "awk '{print > \"x\"}' f",
                "awk '{print >> \"x\"}' f",
                "awk '{printf \"%s\", $1 > \"x\"}' f",
                "awk '{print $1 > \"/dev/stderr\"}' f",
                'awk -f prog.awk f',
                'awk -fprog.awk f',
                'awk -f',
                "awk 'BEGIN{system(\"ls\")}'",
                "awk '{\"date\" | getline x}' f",
                "awk '{getline x < \"f\"}' f",
                "awk '{print | \"sh\"}' f",
                "awk 'END{close(\"x\")}' f",
                "awk 'END{fflush()}' f",
                "awk '{print $1 || $2}' f",
                "awk -v x=1 -f prog f",
                "awk -F: -f prog f",
                "awk --file=prog f",
                "awk -i inplace '{print}' f",
                "awk '@include \"x\"' f",
                "awk -F: '{print $1; print > \"x\"}' f",
            ],
            AWK,
        )

    def test_awk_runs_through_a_pipe_or_a_loop_like_any_command(self):
        self.assert_refused(["ls | awk '{system(\"x\")}'", "for f in a; do awk '{print > \"x\"}' f; done", "sh -c \"awk -f p f\""], AWK)


class ExpansionTests(Level1Case):
    def test_expansion_reaches_only_pure_read_commands(self):
        self.assert_permitted(
            [
                'x=$(cat f); echo $x',
                'for f in $(ls docs); do cat $f; done',
                'if [ -e "$x" ]; then ls; fi',
                "awk '{print $1}' f",
                "sed -n '$p' f",
                'x=$(cat f)',
                'x=$(cat f) echo $x',
                'echo "$(cat f)"',
                'grep -q "$x" f',
                'echo \\$x',
                'test -f "$1" && ls',
            ]
        )

    def test_expansion_elsewhere_is_refused(self):
        self.assert_refused(
            [
                'find docs $(echo -delete)',
                'x=-delete; find docs $x',
                'cd %s && cp a $(echo %s/scripts/f)' % (self.tmp, self.root),
                'for i in $(cat l); do find docs $i; done',
                'sed -n "$p" f',
                'cp a "$x"',
                'x=$(cat f) find docs',
                'awk $x f',
                'sort $x',
                'uniq $x',
                'rg $x',
                'cd $x',
                'mkdir $x',
                'git status $x',
                'bash scripts/x.sh $x',
                'python3 -m unittest $x',
                'docker ps $x',
                '$x ls',
                'sh -c "ls $x"',
                'env $x ls',
                'x=$(cat f) sort',
                'echo x | sort $(cat f)',
            ],
            EXPANSION,
        )

    def test_deletion_and_network_keep_their_reason(self):
        self.assert_refused(['rm $x'], DELETION)


class ResumeDirtyTests(Level1Case):
    def test_delegate_with_resume_dirty_is_permitted_before_or_after_the_sheet(self):
        self.assert_permitted(
            [
                'bash scripts/delegate.sh .workspace/tasks/demo.md --resume-dirty',
                'bash scripts/delegate.sh --resume-dirty .workspace/tasks/demo.md',
            ]
        )


if __name__ == '__main__':
    unittest.main()
