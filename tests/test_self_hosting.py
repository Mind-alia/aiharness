"""The kit runs its own circuit from scripts/, a byte-for-byte mirror of template/scripts.

The circuit refuses symbolic links inside task worktrees and delegate.py seals six control scripts, so the
mirror is made of real copies. This test is the drift guard: resync with
`rsync -a --exclude __tests__ --exclude __pycache__ template/scripts/ scripts/`.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'template/scripts'
MIRROR = ROOT / 'scripts'
IGNORED = ('__tests__', '__pycache__')


def files_under(base):
    for path in sorted(base.rglob('*')):
        if any(part in IGNORED for part in path.relative_to(base).parts):
            continue
        if path.is_file() or path.is_symlink():
            yield path.relative_to(base)


def in_task_workdir():
    """Ancien worktree sous .workspace/worktrees, ou clone de tâche (son .git porte le pointeur du projet)."""
    return ('.workspace' in ROOT.parts and 'worktrees' in ROOT.parts) or (ROOT / '.git/workspace-root').is_file()


@unittest.skipIf(in_task_workdir(), 'miroir resynchronise a la fusion ; sans objet dans un worktree de tache')
class SelfHosting(unittest.TestCase):
    def test_mirror_has_no_symlink(self):
        for rel in files_under(MIRROR):
            self.assertFalse((MIRROR / rel).is_symlink(), str(rel) + ' est un lien ; le circuit refuse les liens')

    def test_mirror_is_identical_to_template(self):
        template_files = set(files_under(TEMPLATE))
        for rel in template_files:
            copy = MIRROR / rel
            self.assertTrue(copy.is_file(), str(rel) + ' manque dans scripts/ ; resynchroniser le miroir')
            self.assertEqual(copy.read_bytes(), (TEMPLATE / rel).read_bytes(), str(rel) + ' a derive dans scripts/')
        extra = set(files_under(MIRROR)) - template_files - {Path('workspace.py')}
        self.assertEqual(extra, set(), 'fichiers de scripts/ absents du modele')

    def test_profile_is_python(self):
        self.assertEqual((ROOT / 'project.profile').read_text(encoding='utf-8').strip(), 'python')


if __name__ == '__main__':
    unittest.main()
