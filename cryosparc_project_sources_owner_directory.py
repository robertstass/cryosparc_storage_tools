#!/usr/bin/env python3
"""Build a simple CryoSPARC project-source CSV from owner:directory roots.

Each input is of the form:
    OWNER:DIRECTORY

The script scans exactly one directory level below DIRECTORY and selects
subdirectories containing project.json. The output can be fed directly to
cryosparc_storage_scan.py.

Examples:
    python cryosparc_project_sources.py RobertS:/users/strubi/well/cryosparc
    python cryosparc_project_sources.py \
        RobertS:/path/a Alice:/path/b \
        -o projects.csv

By default the output is:
    owner,directory
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

from cryosparc_project_io import MINIMAL_CSV_FIELDS as OUTPUT_FIELDS


def parse_source(value: str) -> tuple[str, Path]:
    if ":" not in value:
        raise ValueError(
            f"Invalid source {value!r}; expected OWNER:DIRECTORY"
        )
    owner, directory = value.split(":", 1)
    owner = owner.strip()
    directory = directory.strip()
    if not owner:
        raise ValueError(f"Invalid source {value!r}; owner is empty")
    if not directory:
        raise ValueError(f"Invalid source {value!r}; directory is empty")
    return owner, Path(directory).expanduser()


def discover_projects(owner: str, root: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with os.scandir(root) as entries:
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                project_json = os.path.join(entry.path, "project.json")
                if not os.path.isfile(project_json):
                    continue
            except OSError as exc:
                print(f"WARNING: cannot inspect {entry.path}: {exc}", file=sys.stderr)
                continue
            rows.append((owner, os.path.normpath(entry.path)))
    rows.sort(key=lambda r: r[1].lower())
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Find CryoSPARC project directories containing project.json."
    )
    parser.add_argument(
        "sources",
        nargs="+",
        metavar="OWNER:DIRECTORY",
        help="Root to scan, associated with an owner; repeat for multiple roots.",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("projects.csv"),
        help="Output CSV (default: projects.csv)",
    )
    parser.add_argument(
        "--append", action="store_true",
        help="Append to an existing CSV instead of replacing it.",
    )
    args = parser.parse_args(argv)

    all_rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for raw in args.sources:
        try:
            owner, root = parse_source(raw)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

        if not root.is_dir():
            print(f"WARNING: root directory not found; skipping: {root}", file=sys.stderr)
            continue

        print(f"Scanning {root} for {owner} projects...")
        try:
            rows = discover_projects(owner, root)
        except OSError as exc:
            print(f"WARNING: cannot scan {root}: {exc}", file=sys.stderr)
            continue

        for row in rows:
            if row in seen:
                continue
            seen.add(row)
            all_rows.append(row)
        print(f"  found {len(rows)} project(s)")

    all_rows.sort(key=lambda r: (r[0].lower(), r[1].lower()))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.append and args.output.exists() else "w"
    with args.output.open(mode, encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        if mode == "w" or args.output.stat().st_size == 0:
            writer.writeheader()
        for owner, directory in all_rows:
            writer.writerow({"owner": owner, "directory": directory})

    print(f"Wrote {len(all_rows)} project(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
