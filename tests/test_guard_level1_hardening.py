import os
import unittest

from tests.test_guard_level1 import AWK, CP, Level1Case

EXPANSION = 'expansion dans les arguments de'
TEMP = 'dossier temporaire : lecture et copie seulement'
UNITTEST = 'tests : seulement ceux du projet'


class VerboseExpansionTests(Level1Case):
    def test_dash_v_with_an_expansion_is_refused(self):
        self.assert_refused(
            [
                '[ -v "$x" ]',
                'test -v "$x"',
                'printf -v y "$x"',
                'printf -v y %s "$(cat f)"',
                'printf -vy "$x"',
                'test -v $x',
                'printf -v y %s $1',
            ],
            EXPANSION,
        )

    def test_dash_v_without_expansion_keeps_its_old_refusal(self):
        self.assert_refused(['test -v x', '[ -v x ]', 'printf -v x y'], '-v évalue un indice')

    def test_other_forms_stay_permitted(self):
        self.assert_permitted(['[ -e "$x" ]', "printf '%s\\n' \"$x\"", 'test -f "$x"', "printf '%s' -value"])


class AwkAtTests(Level1Case):
    def test_at_sign_in_the_program_is_refused(self):
        self.assert_refused(
            [
                "awk '@include \"x\"' f",
                "awk '@load \"x\"' f",
                "awk '@namespace \"a\"' f",
                "awk 'BEGIN{f=\"@x\"; @f()}' f",
                "awk '{print @x}' f",
            ],
            AWK,
        )

    def test_awk_without_at_sign_is_permitted(self):
        self.assert_permitted(["awk '{print $1}' f", "awk -F: 'NR > 1' f"])


class CpIntoAFolderTests(Level1Case):
    def setUp(self):
        super().setUp()
        (self.scratch / 'dir').mkdir()

    def test_target_absent_or_regular_is_permitted(self):
        (self.scratch / 'dir' / 'file').write_text('x')
        self.assert_permitted(['cp a %s/dir' % self.tmp, 'cp file %s/dir' % self.tmp, 'cp -r /etc/hosts %s/dir/' % self.tmp])

    def test_target_link_is_refused_even_broken(self):
        outside = self.base / 'outside'
        outside.mkdir()
        os.symlink(outside / 'nothing', self.scratch / 'dir' / 'broken')
        os.symlink(outside, self.scratch / 'dir' / 'live')
        os.symlink(self.root / 'docs', self.scratch / 'dir' / 'inside')
        self.assert_refused(
            ['cp broken %s/dir' % self.tmp, 'cp x/live %s/dir' % self.tmp, 'cp inside %s/dir' % self.tmp, 'cp a broken %s/dir' % self.tmp],
            CP,
        )

    def test_destination_link_is_refused_whatever_its_target(self):
        os.symlink(self.scratch / 'dir', self.scratch / 'toplink')
        self.assert_refused(['cp a %s/toplink' % self.tmp, 'cp a %s/toplink/' % self.tmp], CP)

    def test_relative_destination_after_cd(self):
        os.symlink(self.base / 'outside', self.scratch / 'dir' / 'l')
        self.assert_refused(['cd %s && cp l dir' % self.tmp], CP)
        self.assert_permitted(['cd %s && cp m dir' % self.tmp])


class TemporaryFolderTests(Level1Case):
    def test_writing_commands_are_refused(self):
        self.assert_refused(
            [
                'cd %s && git add x' % self.tmp,
                'cd %s && git commit -m x' % self.tmp,
                'cd %s && git -C . merge x' % self.tmp,
                'cd %s && git worktree add y' % self.tmp,
                'cd %s && git branch z' % self.tmp,
                'cd %s && git mv a b' % self.tmp,
                'cd %s && python3 -m unittest x' % self.tmp,
                'cd %s && sh tests/run.sh fast' % self.tmp,
                'cd %s && bash scripts/gate.sh' % self.tmp,
                'cd %s && python3 scripts/workspace.py' % self.tmp,
                'cd %s && docker ps' % self.tmp,
                'cd %s && mkdir x' % self.tmp,
                'cd %s && ./workspace status' % self.tmp,
                'cd %s && rsync -a --exclude __tests__ --exclude __pycache__ template/scripts/ scripts/' % self.tmp,
                'cd %s && sh -c "git add x"' % self.tmp,
                'cd %s && /usr/bin/python3 -m unittest x' % self.tmp,
            ],
            TEMP,
        )

    def test_reading_and_copy_stay_permitted(self):
        self.assert_refused(['cd %s && git status' % self.tmp, 'cd %s && git log' % self.tmp], TEMP)
        self.assert_permitted(
            ['cd %s && ls' % self.tmp, 'cd %s && cp a b' % self.tmp, 'cd %s && echo x > out' % self.tmp, 'cd %s && cat f' % self.tmp]
        )

    def test_back_in_the_project_the_rule_no_longer_applies(self):
        self.assert_permitted(['cd %s && cd %s && git add x' % (self.tmp, self.root), 'git add x', 'mkdir -p %s/x' % self.tmp])


class UnittestPathTests(Level1Case):
    def test_modules_and_flags_are_permitted(self):
        self.assert_permitted(
            [
                'python3 -m unittest tests.test_x',
                'python3 -m unittest -v -k name tests.test_x',
                'python3 -m unittest discover -s tests',
                'python3 -m unittest discover -p "test_*.py"',
                'python3 -m unittest discover -s tests -t .',
                'python3 -m unittest tests/test_x.py',
                'cd %s/docs && python3 -m unittest discover -s .' % self.root,
            ]
        )

    def test_paths_outside_the_project_are_refused(self):
        self.assert_refused(
            [
                'python3 -m unittest discover -s /tmp/x',
                'python3 -m unittest discover -s ../x',
                'python3 -m unittest /tmp/x.py',
                'python3 -m unittest discover --start-directory=/tmp/x',
                'python3 -m unittest discover --top-level-directory /tmp/x',
                'python3 -m unittest discover --top-level-directory=/tmp/x',
                'python3 -m unittest discover -t /tmp/x',
                'python3 -m unittest discover -s/tmp/x',
                'python3 -m unittest discover -vs /tmp/x',
                'python3 -m unittest discover --start=/tmp/x',
                'python3 -m unittest ../x/y.py',
                'python3 -m unittest discover -s tests -t /etc',
                '/usr/bin/python3 -m unittest /tmp/x.py',
            ],
            UNITTEST,
        )

    def test_link_leaving_the_project_is_refused(self):
        os.symlink(self.base / 'outside', self.root / 'escape')
        self.assert_refused(['python3 -m unittest discover -s escape', 'python3 -m unittest escape/test_x.py'], UNITTEST)


if __name__ == '__main__':
    unittest.main()
