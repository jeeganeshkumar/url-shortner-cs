"""Run and review the greenfield delivery workflow."""

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "url_shortener"
GRAPH = {
    "requirements": (),
    "design": ("requirements",),
    "implementation": ("design",),
    "tests": ("implementation",),
    "documentation": ("implementation",),
    "release": ("tests", "documentation"),
}
ARTIFACTS = {
    "requirements": (PACKAGE / "requirements.md",),
    "design": (PACKAGE / "design.md",),
    "implementation": (PACKAGE / "__init__.py", PACKAGE / "service.py"),
    "tests": (PACKAGE / "tests/__init__.py", PACKAGE / "tests/test_service.py"),
    "documentation": (ROOT / "README.md",),
    "release": (PACKAGE / "workflow.py",),
}


def snapshot():
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for paths in ARTIFACTS.values()
        for path in paths
    }


def validate(stage):
    paths = ARTIFACTS[stage]
    if any(not path.is_file() for path in paths):
        raise ValueError(f"Missing {stage} artifact")
    if stage == "tests":
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "url_shortener.tests.test_service", "-v"],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30,
        )
        if result.returncode:
            raise ValueError(result.stdout.strip())
        return result.stdout.strip()
    if stage == "implementation":
        compile(paths[-1].read_bytes(), str(paths[-1]), "exec")
    else:
        content = paths[0].read_text().strip()
        if not content:
            raise ValueError(f"Empty {stage} artifact")
        if stage == "documentation":
            for command in ("url_shortener.service", "url_shortener.workflow", "unittest"):
                if command not in content:
                    raise ValueError(f"README is missing {command}")
    return ", ".join(str(path.relative_to(ROOT)) for path in paths)


def record(db, run_id, stage, status, detail=""):
    with db:
        db.execute(
            "INSERT INTO events (run_id, stage, status, detail) VALUES (?, ?, ?, ?)",
            (run_id, stage, status, detail),
        )


def run(db):
    original = json.dumps(snapshot(), sort_keys=True)
    with db:
        run_id = db.execute("INSERT INTO runs (inputs) VALUES (?)", (original,)).lastrowid
    print(f"Run {run_id}", flush=True)
    passed = set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        while True:
            ready = [
                stage for stage, dependencies in GRAPH.items()
                if stage not in passed and passed.issuperset(dependencies)
            ]
            if ready == ["release"]:
                if json.dumps(snapshot(), sort_keys=True) != original:
                    record(db, run_id, "release", "failed", "Inputs changed during validation")
                    return 1
                record(db, run_id, "release", "awaiting_approval")
                print(f"Release awaiting human approval for run {run_id}")
                return 0
            jobs = {}
            for stage in ready:
                record(db, run_id, stage, "running")
                jobs[pool.submit(validate, stage)] = stage
            failed = False
            for job in as_completed(jobs):
                stage = jobs[job]
                try:
                    detail = job.result()
                except Exception as error:
                    record(db, run_id, stage, "failed", str(error))
                    print(f"{stage} failed\n{error}", file=sys.stderr)
                    failed = True
                else:
                    record(db, run_id, stage, "passed", detail)
                    passed.add(stage)
                    print(f"{stage} passed")
            if failed:
                record(db, run_id, "release", "blocked", "Validation failed")
                return 1


def approve(db, run_id, reviewer, reason):
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            """SELECT runs.inputs, events.status FROM runs
               JOIN events ON events.run_id = runs.id
               WHERE runs.id = ? AND events.stage = 'release'
               ORDER BY events.id DESC LIMIT 1""",
            (run_id,),
        ).fetchone()
        if row is None or row[1] != "awaiting_approval":
            raise ValueError("Run is not awaiting approval")
        if json.loads(row[0]) != snapshot():
            raise ValueError("Inputs changed. Validate a new run before approving.")
        db.execute(
            "INSERT INTO events (run_id, stage, status, detail) VALUES (?, 'release', 'approved', ?)",
            (run_id, json.dumps({"reviewer": reviewer, "reason": reason})),
        )
    print(f"Run {run_id} approved for release readiness")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="workflow.sqlite3")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run")
    status = commands.add_parser("status")
    status.add_argument("run_id", type=int)
    approval = commands.add_parser("approve")
    approval.add_argument("run_id", type=int)
    approval.add_argument("--reviewer", required=True)
    approval.add_argument("--reason", required=True)
    args = parser.parse_args()
    with closing(sqlite3.connect(args.db)) as db:
        db.executescript(
            """CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, inputs TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, stage TEXT NOT NULL,
                status TEXT NOT NULL, detail TEXT NOT NULL,
                timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            );"""
        )
        try:
            if args.command == "run":
                return run(db)
            if args.command == "approve":
                if not args.reviewer.strip() or not args.reason.strip():
                    raise ValueError("Reviewer and reason must be nonempty")
                approve(db, args.run_id, args.reviewer, args.reason)
            else:
                events = db.execute(
                    "SELECT timestamp, stage, status, detail FROM events WHERE run_id = ? ORDER BY id",
                    (args.run_id,),
                ).fetchall()
                if not events:
                    raise ValueError("Unknown run")
                for timestamp, stage, state, detail in events:
                    print(f"{timestamp} {stage} {state}")
                    if detail:
                        print(detail)
        except (ValueError, OSError) as error:
            print(error, file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
