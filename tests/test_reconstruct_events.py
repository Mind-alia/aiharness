"""Contrats du journal reconstruit, sur des dépôts jetables."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools/mission/reconstruct_events.py"


class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(dir=os.environ.get("WORKSPACE_TEST_TMP")))
        self.addCleanup(shutil.rmtree, self.temp)
        self.repo = self.temp / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.email", "test@example.org")
        self.git("config", "user.name", "Test")
        self.commit("base", "base", "2026-10-01T10:00:00+02:00")
        self.out = self.temp / "events.jsonl"

    def git(self, *args, date=None):
        env = os.environ.copy()
        if date:
            env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        return subprocess.check_output(["git", "-C", str(self.repo), *args], env=env, stderr=subprocess.PIPE).decode().strip()

    def commit(self, path, content, date):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
        self.git("add", path)
        self.git("commit", "-m", path, date=date)
        return self.git("rev-parse", "HEAD")

    def run_tool(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.repo), "--out", str(self.out), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def events(self):
        return [json.loads(line) for line in self.out.read_text().splitlines()]

    def test_empty_and_invalid(self):
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "tâches : 0\névénements : 0\nrapports lus : 0\n")
        self.assertEqual(self.out.read_bytes(), b"")
        self.out.unlink()
        result = self.run_tool("--branch", "absent")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.out.exists())

    def test_output_guards(self):
        destination = self.temp / "destination"
        destination.write_text("intact")
        self.out.symlink_to(destination)
        self.assertEqual(self.run_tool().returncode, 2)
        self.assertEqual(destination.read_text(), "intact")
        self.out.unlink()
        self.out = self.repo / ".workspace" / "events.jsonl"
        self.assertEqual(self.run_tool().returncode, 2)
        self.assertFalse(self.out.parent.exists())

    def test_merges_reports_and_read_only(self):
        self.git("checkout", "-b", "workspace-task/alpha")
        first = self.commit("task", "one", "2026-10-02T10:00:00+02:00")
        self.git("checkout", "main")
        self.git("merge", "--no-ff", "workspace-task/alpha", "-m", "merge: task alpha done", date="2026-10-03T10:00:00+02:00")
        self.git("branch", "-D", "workspace-task/alpha")
        report = """# Pôle 12
| Tokens | Travail | Durée totale | Agent | Tentatives |
|---|---|---|---|---|
| - | `alpha` | - | Codex | - |
| 1 200 | `alpha` | 2 min | autre | 2 |
| 0 | `report-only` | 120 s | - | 1 |
| - | total invalid | - | - | - |
"""
        path = "docs/journal/2026-10-04/RAPPORT-X.md"
        self.commit(path, report, "2026-10-04T10:00:00+02:00")
        (self.repo / path).write_text("working tree ignored")
        status = self.git("status", "--porcelain")
        refs = self.git("for-each-ref")
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.events()
        self.assertEqual(len(events), 4)
        prepared, merge, summary, report_only = events
        self.assertEqual(prepared["at"], "2026-10-02T08:00:00+00:00")
        self.assertEqual(prepared["detail"]["commit"], first)
        self.assertIsNone(prepared["detail"]["branch"])
        self.assertEqual(merge["detail"]["files"], ["task"])
        self.assertEqual(summary["detail"], dict(source=path, executor="Codex", attempts=2, duration_seconds=120, tokens=1200))
        self.assertEqual(summary["orchestrator"], "Pôle 12")
        self.assertEqual(report_only["task_id"], "report-only")
        for event in events:
            self.assertEqual(set(event), {"at", "event", "task_id", "orchestrator", "detail", "reconstructed"})
            self.assertIs(event["reconstructed"], True)
        self.assertEqual(self.git("status", "--porcelain"), status)
        self.assertEqual(self.git("for-each-ref"), refs)
        self.assertFalse((self.repo / ".workspace").exists())

    def test_unmerged_duplicate_and_unknown_summary(self):
        self.git("checkout", "-b", "workspace-task/lost")
        self.commit("lost", "x", "2026-10-02T10:00:00+00:00")
        self.git("branch", "archive/workspace-task/lost-tentative2")
        self.git("checkout", "main")
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.events()
        self.assertEqual([e["event"] for e in events], ["task_prepared", "delegation_summary"])
        self.assertEqual(events[0]["task_id"], "lost")
        self.assertEqual(events[1]["detail"]["attempts"], "non mesuré")

    def test_report_citation_suppresses_unknown_git_summary(self):
        self.git("commit", "--allow-empty", "-m", "merge: task alpha", date="2026-10-02T00:00:00+00:00")
        self.commit("docs/design/RAPPORT-empty.md", "| tâche | tokens |\n|---|---|\n| alpha | - |\n", "2026-10-03T00:00:00+00:00")
        self.assertEqual(self.run_tool().returncode, 0)
        self.assertEqual([e["event"] for e in self.events()], ["merge"])


if __name__ == "__main__":
    unittest.main()
