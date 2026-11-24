import unittest

from tests.test_guard_level1 import Level1Case

TEMP = 'dossier temporaire : lecture et copie seulement'
UNITTEST = 'tests : seulement ceux du projet'


class TemporaryWhitelistTests(Level1Case):
    def in_temp(self, command):
        return 'cd %s && %s' % (self.tmp, command)

    def test_reading_and_copy_are_permitted(self):
        self.assert_permitted(
            [
                self.in_temp(item)
                for item in (
                    'ls',
                    'cat f',
                    'cp %s/f c' % self.root,
                    'grep x f',
                    'echo x > f',
                    'set -- a',
                    'FOO=1',
                    'if test -f f; then echo y; fi',
                    'for i in 1 2; do echo $i; done',
                )
            ]
        )

    def test_everything_else_is_refused(self):
        self.assert_refused(
            [
                self.in_temp(item)
                for item in (
                    'sh ./scripts/x.sh',
                    'bash //tmp/x.sh',
                    'python3 x.py',
                    'git status',
                    '/usr/bin/git add x',
                    'env ls',
                    'nice ls',
                    'set -e',
                    'mkdir x',
                    'docker ps',
                    'python3 -m unittest tests.test_x',
                    'sh tests/run.sh fast',
                )
            ],
            TEMP,
        )


class UnittestArgumentTests(Level1Case):
    def test_bounded_forms_stay_permitted(self):
        self.assert_permitted(
            [
                'python3 -m unittest tests.test_x',
                'python3 -m unittest discover -s tests -p "test_*.py"',
                '/usr/bin/python3 -m unittest -v tests.test_x tests.test_y',
                'python3 -m unittest -k name tests.test_x',
            ]
        )

    def test_escaping_or_glued_arguments_are_refused(self):
        self.assert_refused(
            [
                'python3 -m unittest ' + item
                for item in (
                    'discover -s=/tmp/x',
                    'discover -s/tmp/x',
                    'discover ~/x',
                    'discover ..',
                    'discover tests/../..',
                    'discover --start-directory=/tmp/x',
                    'discover -t=..',
                    'discover -s tests -p a/b',
                    'discover -sx',
                    'discover -tx',
                    'discover -s tests -t=x',
                    'discover --start=tests',
                    '-x tests.test_x',
                    '=x',
                )
            ],
            UNITTEST,
        )


if __name__ == '__main__':
    unittest.main()
