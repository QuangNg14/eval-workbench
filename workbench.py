"""Generate a blind review packet, then reveal only after a complete ballot."""

import argparse
import json
import re
import sys
from pathlib import Path

from eval_core import ROOT, adjudicate, assignment, ballot, frozen, make_key, metrics, read_object
from presentation import report_html, report_text, review_html

RUNS = ROOT / "runs"


def run_path(name: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", name) or Path(name).is_reserved():
        raise ValueError("Use a simple run name (letters, numbers, hyphens or underscores).")
    return RUNS / name


def write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def prepare(run: Path, data: dict, demo: bool) -> None:
    key = make_key(demo)
    packet = assignment(data, key)
    if run.exists():
        raise ValueError(f"Run already exists: {run.name}. Use a fresh name.")
    run.mkdir(parents=True)
    reviewer = run / "reviewer"
    reviewer.mkdir()
    write_json(run / "key.json", key)
    write_json(reviewer / "ballot.json", ballot(data, packet))
    (reviewer / "review.html").write_text(review_html(data, packet), encoding="utf-8")
    sys.stdout.write(f"Review page and companion ballot: {reviewer}\n")


def score(run: Path, data: dict) -> None:
    path = run / "report.html"
    if path.exists():
        raise ValueError(f"Run already revealed: {path}. Start a new run for another review.")
    packet = assignment(data, read_object(run / "key.json"))
    rows = adjudicate(data, packet, read_object(run / "reviewer" / "ballot.json"))
    groups = metrics(data, rows)
    html = report_html(packet, rows, groups)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(html)
    sys.stdout.write(report_text(packet, rows, groups) + f"\nReport: {path}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local comparison of frozen synthetic Leaf Room outputs.")
    sub = parser.add_subparsers(dest="action", required=True)
    for name, help_text in (
        ("prepare", "Create a fresh blind packet and blank manual ballot"),
        ("score", "Validate the completed ballot, reveal roles and save the report once"),
        ("demo", "Generate and score a repeatable, explicitly scripted example"),
    ):
        sub.add_parser(name, help=help_text).add_argument("run", help="Name under the ignored runs directory")
    args = parser.parse_args(argv)
    try:
        run, data = run_path(args.run), frozen()
        if args.action != "score":
            prepare(run, data, demo=args.action == "demo")
        if args.action != "prepare":
            score(run, data)
    except (ValueError, OSError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
