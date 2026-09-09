#!/usr/bin/env python3
"""Find CryoSPARC Live sessions with reclaimable extracted-particle .mrc stacks.

This is a fast, strictly read-only companion to cryosparc_storage_scan.py and
cryosparc_live_clear_particles.py. It takes the same project input as
cryosparc_storage_scan.py (a CryoSPARC projectData CSV/HTML export, or a
minimal owner,directory CSV), then for each project looks for Live session
directories (S1, S2, ...) and checks their extract/blob particle directories
for *.mrc files, using the same 'estimate' sampling logic that
cryosparc_live_clear_particles.py uses.

Unlike cryosparc_storage_scan.py, this script never shells out to `du` and
never measures whole job directories -- it only looks inside each session's
extract/blob directory, so it is normally much quicker to run, especially
with --scan-mode skip. It does not delete or modify anything; use the
reported live_uid/project_dir with cryosparc_live_clear_particles.py to
actually reclaim space.

Some S<n> directories will have no extract/blob directory at all (never
Live-processed, or a different job type), and others will have an extract/blob
directory whose .mrc files were already deleted (e.g. by
cryosparc_live_clear_particles.py). Both cases are reported without error.

Examples
--------
# Estimate reclaimable particle-stack space for every project in the CSV/HTML
python cryosparc_live_scan_particles.py projectData.csv

# Only projects owned by RobertS, sampling estimate mode explicitly
python cryosparc_live_scan_particles.py projectData.csv --owner RobertS --scan-mode estimate

# Fastest pass: just report which sessions still have particle files, no sizing
python cryosparc_live_scan_particles.py projectData.csv --scan-mode skip

# Exact sizes (slower: stats every .mrc file instead of sampling)
python cryosparc_live_scan_particles.py projectData.csv --scan-mode full

# Also save the findings to a text file
python cryosparc_live_scan_particles.py projectData.csv --output findings.txt
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from cryosparc_project_io import Project, filter_projects, load_projects
from cryosparc_live_clear_particles import (
    LIVE_UID_RE,
    estimate_mrc_size_by_blob_dir,
    find_mrc_files,
    get_live_workspace,
    human_size,
)


@dataclass(frozen=True)
class LiveSession:
    project: Project
    live_uid: str
    path: Path


@dataclass
class LiveSessionScan:
    session: LiveSession
    # "no_extract" | "no_blob" | "empty" | "particles_found" | "error"
    status: str
    mrc_count: int = 0
    bytes_total: int | None = None
    is_estimate: bool = False
    sampled_files: int = 0
    unreadable_samples: int = 0
    workspace_status: str = ""
    error: str = ""


def discover_live_sessions(project: Project) -> list[LiveSession]:
    """Discover immediate S<number> Live session directories using scandir()."""
    sessions: list[LiveSession] = []
    try:
        with os.scandir(project.directory) as entries:
            for entry in entries:
                if not LIVE_UID_RE.fullmatch(entry.name):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                sessions.append(LiveSession(project=project, live_uid=entry.name, path=Path(entry.path)))
    except OSError:
        return []
    sessions.sort(key=lambda s: int(LIVE_UID_RE.fullmatch(s.live_uid).group(1)))
    return sessions


def workspace_status_for(session: LiveSession) -> str:
    workspace = get_live_workspace(session.project.directory, session.live_uid)
    if workspace is None:
        return ""
    status = workspace.get("status")
    return status if isinstance(status, str) else ""


def scan_session(session: LiveSession, scan_mode: str) -> LiveSessionScan:
    """Inspect one Live session's extract/blob directory. Read-only: no writes, no deletes."""
    extract_dir = session.path / "extract"
    blob_dir = extract_dir / "blob"
    workspace_status = workspace_status_for(session)

    if not extract_dir.is_dir():
        return LiveSessionScan(session=session, status="no_extract", workspace_status=workspace_status)
    if not blob_dir.is_dir():
        return LiveSessionScan(session=session, status="no_blob", workspace_status=workspace_status)

    try:
        targets = find_mrc_files(blob_dir)
    except OSError as exc:
        return LiveSessionScan(session=session, status="error", workspace_status=workspace_status, error=str(exc))

    if not targets:
        return LiveSessionScan(session=session, status="empty", workspace_status=workspace_status)

    if scan_mode == "skip":
        return LiveSessionScan(
            session=session, status="particles_found", workspace_status=workspace_status,
            mrc_count=len(targets),
        )

    if scan_mode == "full":
        total = 0
        unreadable = 0
        for path in targets:
            try:
                total += path.stat().st_size
            except OSError:
                unreadable += 1
        return LiveSessionScan(
            session=session, status="particles_found", workspace_status=workspace_status,
            mrc_count=len(targets), bytes_total=total, is_estimate=False,
            unreadable_samples=unreadable,
        )

    # scan_mode == "estimate" (default)
    estimated_total, sampled_files, unreadable_samples, _group_results = estimate_mrc_size_by_blob_dir(
        blob_dir, targets
    )
    return LiveSessionScan(
        session=session, status="particles_found", workspace_status=workspace_status,
        mrc_count=len(targets), bytes_total=estimated_total, is_estimate=True,
        sampled_files=sampled_files, unreadable_samples=unreadable_samples,
    )


def format_size(scan: LiveSessionScan) -> str:
    if scan.bytes_total is None:
        return "-"
    prefix = "~" if scan.is_estimate else ""
    return f"{prefix}{human_size(scan.bytes_total)}"


def format_findings(scans: Sequence[LiveSessionScan], scan_mode: str) -> list[str]:
    lines: list[str] = []
    reclaimable = [s for s in scans if s.status == "particles_found"]
    reclaimable_sorted = sorted(reclaimable, key=lambda s: (s.bytes_total or 0), reverse=True)

    lines.append(f"Scan mode: {scan_mode}")
    lines.append(f"Live session directories examined: {len(scans)}")
    lines.append(f"Sessions with reclaimable particle .mrc files: {len(reclaimable)}")

    if scan_mode != "skip":
        known_total = sum(s.bytes_total or 0 for s in reclaimable if s.bytes_total is not None)
        label = "Estimated" if scan_mode == "estimate" else "Exact"
        lines.append(f"{label} total reclaimable space: {human_size(known_total)}")
    lines.append("")

    if reclaimable_sorted:
        lines.append("Sessions with particle .mrc files (largest first):")
        for scan in reclaimable_sorted:
            session = scan.session
            owner = session.project.owner or "-"
            size = format_size(scan)
            note = f" [workspace status: {scan.workspace_status}]" if scan.workspace_status else ""
            lines.append(
                f"  {owner}\t{session.project.project_id or session.project.directory.name}\t"
                f"{session.live_uid}\t{scan.mrc_count} mrc file(s)\t{size}\t{session.path}{note}"
            )
            if scan.unreadable_samples:
                lines.append(f"    WARNING: {scan.unreadable_samples} file(s) could not be stat'ed")
        lines.append("")
        lines.append(
            "To reclaim space, run cryosparc-live-clear-particles with the "
            "matching --project-dir and --live-uid (start with --dry-run)."
        )
    else:
        lines.append("No Live sessions with reclaimable particle .mrc files were found.")

    other = [s for s in scans if s.status != "particles_found"]
    if other:
        lines.append("")
        lines.append("Other session directories checked:")
        counts: dict[str, int] = {}
        for scan in other:
            counts[scan.status] = counts.get(scan.status, 0) + 1
        status_labels = {
            "no_extract": "no extract/ directory (not Live-processed, or a different job type)",
            "no_blob": "extract/ present but no blob/ directory",
            "empty": "extract/blob/ present but no .mrc files remain (already cleared)",
            "error": "could not be scanned (see stderr)",
        }
        for status, count in sorted(counts.items()):
            lines.append(f"  {count} {status_labels.get(status, status)}")

    return lines


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only scan for CryoSPARC Live sessions with reclaimable extracted-particle "
            ".mrc stacks. Accepts the same projectData CSV/HTML input as cryosparc_storage_scan.py."
        )
    )
    parser.add_argument(
        "input", type=Path,
        help="CryoSPARC projectData CSV, minimal owner/directory CSV, or saved HTML page",
    )
    parser.add_argument(
        "--owner", action="append", default=[], metavar="USER",
        help="Only scan projects whose Owner exactly matches USER. Repeat for multiple owners.",
    )
    parser.add_argument(
        "--scan-mode", choices=("full", "estimate", "skip"), default="estimate",
        help=(
            "Size scan mode, matching cryosparc_live_clear_particles.py: "
            "'full'=stat every .mrc file exactly; "
            "'estimate'=sample up to 10 files per blob subdirectory and extrapolate (default); "
            "'skip'=only report which sessions have .mrc files, without sizing them (fastest)."
        ),
    )
    parser.add_argument(
        "-p", "--parallel", type=int, default=4,
        help="Number of Live sessions to inspect concurrently (default: 4).",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Optional text file to write the findings to, in addition to stdout.",
    )
    parser.add_argument(
        "--list-owners", action="store_true",
        help="List owners and project counts in the input, then exit.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show selected projects and discovered Live session counts without scanning blob directories.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.parallel < 1:
        print("ERROR: --parallel must be at least 1", file=sys.stderr)
        return 2
    if not args.input.is_file():
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        return 2

    try:
        all_projects = load_projects(args.input)
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.list_owners:
        counts: dict[str, int] = {}
        for project in all_projects:
            counts[project.owner or "<blank>"] = counts.get(project.owner or "<blank>", 0) + 1
        for owner, count in sorted(counts.items(), key=lambda x: x[0].lower()):
            print(f"{owner}\t{count}")
        return 0

    owners = set(args.owner)
    projects = filter_projects(all_projects, owners)

    print(f"Loaded {len(all_projects)} project(s) from {args.input}")
    if owners:
        print(f"Owner filter: {', '.join(sorted(owners))}")
        print(f"Selected {len(projects)} project(s)")
    print(f"Scan mode: {args.scan_mode}")

    sessions: list[LiveSession] = []
    missing_projects = 0
    for project in projects:
        if not project.directory.is_dir():
            missing_projects += 1
            print(
                f"WARNING: project directory not found; skipping {project.project_id} "
                f"({project.owner}): {project.directory}",
                file=sys.stderr,
            )
            continue
        try:
            project_sessions = discover_live_sessions(project)
        except OSError as exc:
            print(f"WARNING: cannot scan {project.directory}: {exc}", file=sys.stderr)
            continue
        sessions.extend(project_sessions)
        if args.dry_run:
            print(
                f"{project.project_id or '-'}\t{project.owner or '-'}\t"
                f"{len(project_sessions)} Live session(s)\t{project.directory}"
            )

    print(f"Discovered {len(sessions)} Live session directorie(s) (S<number>)")
    if missing_projects:
        print(f"Skipped {missing_projects} missing project directorie(s)")

    if args.dry_run:
        return 0

    scans: list[LiveSessionScan] = []
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        future_to_session = {
            pool.submit(scan_session, session, args.scan_mode): session for session in sessions
        }
        for future in as_completed(future_to_session):
            session = future_to_session[future]
            try:
                scans.append(future.result())
            except Exception as exc:  # keep other sessions scanning
                print(f"ERROR: {session.path}: {exc}", file=sys.stderr)
                scans.append(LiveSessionScan(session=session, status="error", error=str(exc)))

    # Report in a stable, deterministic order (project, then live_uid).
    scans.sort(key=lambda s: (
        s.session.project.owner or "",
        str(s.session.project.directory),
        int(LIVE_UID_RE.fullmatch(s.session.live_uid).group(1)),
    ))

    lines = format_findings(scans, args.scan_mode)
    print()
    for line in lines:
        print(line)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        print()
        print(f"Findings written to: {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
