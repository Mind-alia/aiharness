"""Reconstruit un journal explicitement étiqueté à partir de preuves Git."""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

TASK = r"[A-Za-z0-9][A-Za-z0-9._-]*"
UNKNOWN = "non mesuré"
FIELDS = ("executor", "attempts", "duration_seconds", "tokens")


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", "replace").strip())
    return result.stdout.decode("utf-8", "replace")


def instant(value):
    return dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00")).astimezone(dt.UTC).isoformat()


def normalize(value):
    return "".join(c for c in unicodedata.normalize("NFD", value.lower()) if not unicodedata.combining(c))


def cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def report_rows(content):  # noqa: C901
    rows, headers = {}, None
    cited = set()
    for line in content.splitlines():
        if "|" not in line:
            headers = None
            continue
        values = cells(line)
        names = [normalize(v) for v in values]
        task_columns = [i for i, name in enumerate(names) if name in ("tache", "task", "travail")]
        if task_columns:
            headers = {"task": task_columns[0]}
            for i, name in enumerate(names):
                if name in ("executant", "agent", "executor"):
                    headers["executor"] = i
                elif name in ("tentatives", "attempts"):
                    headers["attempts"] = i
                elif name.startswith(("duree", "duration")):
                    headers["duration_seconds"] = i
                elif "token" in name:
                    headers["tokens"] = i
            continue
        if headers is None or headers["task"] >= len(values):
            continue
        cell = values[headers["task"]]
        quoted = re.findall(r"`([^`]+)`", cell)
        task = quoted[-1].strip() if quoted else cell.strip()
        if not re.fullmatch(TASK, task):
            continue
        cited.add(task)
        row = rows.setdefault(task, dict.fromkeys(FIELDS, UNKNOWN))
        for field in FIELDS:
            index = headers.get(field)
            raw = values[index].strip() if index is not None and index < len(values) else ""
            value = UNKNOWN
            if field == "executor":
                if raw and raw not in ("-", UNKNOWN):
                    value = raw
            elif field == "attempts" and re.fullmatch(r"[0-9]+", raw):
                value = int(raw)
            elif field == "tokens" and re.fullmatch(r"[0-9\s]+", raw):
                digits = "".join(raw.split())
                if digits:
                    value = int(digits)
            elif field == "duration_seconds":
                match = re.fullmatch(r"([0-9]+)\s*(s|min)?", raw)
                if match:
                    value = int(match[1]) * (60 if match[2] == "min" else 1)
            if row[field] == UNKNOWN:
                row[field] = value
    return rows, cited


def reconstruct(root, branch):  # noqa: C901, PLR0915
    events, prepared, cited = [], set(), set()

    def add(task, kind, at, detail, orchestrator="inconnu"):
        events.append(dict(at=instant(at), event=kind, task_id=task, orchestrator=orchestrator, detail=detail, reconstructed=True))

    refs = git(root, "for-each-ref", "--format=%(refname:short)%09%(objectname)", "refs/heads/").splitlines()
    branches = [line.split("\t", 1) for line in refs]

    def prepare(task, commits, name=None):
        if not commits:
            return
        commit = commits[-1]
        if commit in prepared:
            return
        prepared.add(commit)
        if name is None:
            name = next(
                (
                    b
                    for b, h in branches
                    if h == commit and (b == "workspace-task/" + task or b.startswith("archive/workspace-task/" + task))
                ),
                None,
            )
        add(
            task,
            "task_prepared",
            git(root, "show", "-s", "--format=%cI", commit),
            dict(source="git", basis="premier commit de la branche", commit=commit, branch=name),
        )

    merges = []
    for line in git(root, "log", "--format=%H%x09%P%x09%cI%x09%s", branch).splitlines():
        commit, parents, date, subject = line.split("\t", 3)
        match = re.match(r"^merge: task (\S+)", subject)
        if not match or not re.fullmatch(TASK, match[1]):
            continue
        task = match[1]
        parents = parents.split()
        if len(parents) == 2:
            prepare(task, git(root, "rev-list", parents[1], "--not", parents[0]).splitlines())
        files = git(root, "diff-tree", "--no-commit-id", "--name-only", "-z", "-r", "-m", "--first-parent", commit).split("\0")
        merges.append((task, date, dict(source="git", commit=commit, files=sorted(set(f for f in files if f)))))
    for name, _ in branches:
        match = re.fullmatch(r"(?:archive/)?workspace-task/(" + TASK + r")", name)
        if match:
            task = re.sub(r"-tentative[0-9]+$", "", match[1])
            prepare(task, git(root, "rev-list", name, "--not", branch).splitlines(), name)
    for task, date, detail in merges:
        add(task, "merge", date, detail)

    paths = git(root, "ls-tree", "-r", "--name-only", "-z", branch).split("\0")
    reports = sorted(p for p in paths if re.fullmatch(r"docs/(?:design/|journal/(?:.*/)? )RAPPORT-[^/]*\.md".replace("? ", "?"), p))
    for path in reports:
        content = git(root, "show", branch + ":" + path)
        rows, mentioned = report_rows(content)
        cited.update(mentioned)
        dates = git(root, "log", "--diff-filter=A", "--format=%cI", branch, "--", path).splitlines()
        date = dates[-1] if dates else None
        if date is None:
            match = re.search(r"\d{4}-\d{2}-\d{2}", path)
            if match:
                try:
                    date = instant(match[0] + "T00:00:00+00:00")
                except ValueError:
                    pass
        if date is None:
            continue
        match = re.search(r"(?:Pinceau|Pôle)\s+[0-9]+", content)
        orchestrator = match[0] if match else "inconnu"
        for task, row in rows.items():
            if any(v != UNKNOWN for v in row.values()):
                add(task, "delegation_summary", date, dict(source=path, **row), orchestrator)
    latest = {}
    for event in events:
        if event["detail"]["source"] == "git":
            task = event["task_id"]
            latest[task] = max(latest.get(task, event["at"]), event["at"])
    for task, date in latest.items():
        if task not in cited:
            add(task, "delegation_summary", date, dict(source="git", **dict.fromkeys(FIELDS, UNKNOWN)))
    priority = {"task_prepared": 0, "merge": 1, "delegation_summary": 2}
    events.sort(key=lambda e: (e["at"], priority[e["event"]]))
    return events, len(reports)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository")
    parser.add_argument("--out", required=True)
    parser.add_argument("--branch")
    args = parser.parse_args(argv)
    try:
        root = Path(git(args.repository, "rev-parse", "--show-toplevel").strip()).resolve()
        branch = args.branch
        if branch is None:
            heads = git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads/").splitlines()
            branch = next((b for b in ("main", "master") if b in heads), "HEAD")
        branch = git(root, "rev-parse", "--verify", "--end-of-options", branch + "^{commit}").strip()
        output = Path(args.out)
        target = output.resolve()
        workspace = (root / ".workspace").resolve()
        if output.is_symlink() or target == workspace or workspace in target.parents:
            raise ValueError("sortie interdite : lien symbolique ou .workspace")
        events, reports = reconstruct(root, branch)
        payload = "".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n" for e in events)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(str(output), flags, 0o666), "w", encoding="utf-8") as stream:
            stream.write(payload)
    except (OSError, ValueError, RuntimeError) as error:
        print("reconstruct_events: " + str(error), file=sys.stderr)
        return 2
    print("tâches : " + str(len({e["task_id"] for e in events})))
    print("événements : " + str(len(events)))
    print("rapports lus : " + str(reports))
    return 0


if __name__ == "__main__":
    sys.exit(main())
