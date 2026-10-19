#!/usr/bin/env python3
"""Scan CryoSPARC job directory sizes using a CryoSPARC project export.

The input is either:
  * a CSV produced by cryosparc_manage_projectData_to_csv.py, or
  * a saved CryoSPARC "Manage: projectData" HTML page.

Unlike the older shell scanner, this does not search top-level directories for
projects.  It uses the CryoSPARC table's Directory column as the authoritative
project path, optionally filters projects by Owner, discovers immediate J<number>
job directories and S<number> Live session directories, and runs native `du`
for size measurement. Live session directories are reported with
job_type "live" (see detect_job_type()).

The output columns extend the old cryosparc_storage_scan.sh schema with
'workspace_uids' (between job_type and path) and a trailing 'date' column:
    tld,project,owner,job,bytes,job_type,workspace_uids,path,date
The owner comes directly from the CryoSPARC project table. 'workspace_uids' is
the comma-separated workspace UID(s) from a regular job's job.json (blank for
Live sessions). 'date' is each job's creation date, in priority order:
job.json's created_at; for Live sessions (which have no created_at) the most
recent per-exposure discovered_at in exposures.bson, i.e. when the session last
found a new micrograph; and finally the directory's own mtime if neither is
available. Written as a plain ISO8601 string so cryosparc-storage-display's
--older-than can parse it directly.

CSV input uses only the Python standard library. HTML input additionally
needs BeautifulSoup 4 (`pip install beautifulsoup4`). The exposures.bson date
for Live sessions additionally needs pymongo (`pip install pymongo`, provides
the 'bson' module); without it, Live sessions silently fall back to mtime.

Examples
--------
# Scan every project in the CSV
python cryosparc_storage_scan.py projectData.csv -o jobs.csv

# Or use a minimal owner,directory CSV:
python cryosparc_project_sources.py RobertS:/path/to/cryosparc -o projects.csv
python cryosparc_storage_scan.py projects.csv -o jobs.csv

# Only projects owned by RobertS (repeat --owner for more owners)
python cryosparc_storage_scan.py projectData.csv -o jobs.csv

# Or use a minimal owner,directory CSV:
python cryosparc_project_sources.py RobertS:/path/to/cryosparc -o projects.csv
python cryosparc_storage_scan.py projects.csv -o jobs.csv --owner RobertS

# Resume an interrupted scan; already-written job paths are skipped
python cryosparc_storage_scan.py projectData.csv -o jobs.csv

# Or use a minimal owner,directory CSV:
python cryosparc_project_sources.py RobertS:/path/to/cryosparc -o projects.csv
python cryosparc_storage_scan.py projects.csv -o jobs.csv --resume

# Read the saved CryoSPARC HTML directly
python cryosparc_storage_scan.py 'Manage_ projectData _ CryoSPARC.html' -o jobs.csv

# Show which projects would be scanned, without running du
python cryosparc_storage_scan.py projectData.csv --owner RobertS --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

from cryosparc_project_io import Project, filter_projects, load_projects

try:
    import bson  # from pymongo; optional, only needed for Live exposures.bson
    from bson.codec_options import CodecOptions
    from bson.raw_bson import RawBSONDocument
except ImportError:
    bson = None

OUTPUT_FIELDS = ["tld", "project", "owner", "job", "bytes", "job_type", "workspace_uids", "path", "date"]
# Matches both regular job directories (J<number>) and CryoSPARC Live session
# directories (S<number>). Live sessions used to be included in this scan as
# a "job"; detect_job_type() below already classifies them as "live" via the
# exposures.bson marker file every Live session directory contains directly.
JOB_RE = re.compile(r"^(?:J|S)(\d+)$")


@dataclass(frozen=True)
class Job:
    project: Project
    job_name: str
    path: Path


@dataclass(frozen=True)
class JobResult:
    job: Job
    bytes_used: int
    job_type: str
    workspace_uids: str
    date: str


def job_sort_key(job_name: str) -> tuple[int, str]:
    match = JOB_RE.fullmatch(job_name)
    return (int(match.group(1)), job_name) if match else (sys.maxsize, job_name)


def discover_jobs(project: Project) -> list[Job]:
    """Discover immediate CryoSPARC J<number> and Live S<number> directories using scandir()."""
    jobs: list[Job] = []
    with os.scandir(project.directory) as entries:
        for entry in entries:
            if not JOB_RE.fullmatch(entry.name):
                continue
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            jobs.append(Job(project=project, job_name=entry.name, path=Path(entry.path)))
    jobs.sort(key=lambda j: job_sort_key(j.job_name))
    return jobs


def detect_job_type(job_dir: Path) -> str:
    if (job_dir / "exposures.bson").is_file():
        return "live"

    json_path = job_dir / "job.json"
    if not json_path.is_file():
        return "unknown"

    try:
        with json_path.open("r", encoding="utf-8", errors="replace") as handle:
            data = json.load(handle)
        value = data.get("job_type") if isinstance(data, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    except (OSError, json.JSONDecodeError, UnicodeError):
        # Preserve the old script's forgiving behaviour for malformed JSON.
        try:
            text = json_path.read_text(encoding="utf-8", errors="replace")
            match = re.search(r'"job_type"\s*:\s*"([^"]*)"', text)
            if match and match.group(1).strip():
                return match.group(1).strip()
        except OSError:
            pass

    return "unknown"


def extract_workspace_uids(job_dir: Path, job_type: str) -> str:
    """Return workspace UIDs as a comma-separated CSV field.

    Live jobs deliberately leave this blank. csv.DictWriter handles quoting when
    multiple workspace UIDs introduce commas into the field.
    """
    if job_type == "live":
        return ""

    json_path = job_dir / "job.json"
    if not json_path.is_file():
        return ""
    try:
        with json_path.open("r", encoding="utf-8", errors="replace") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return ""

    values = data.get("workspace_uids") if isinstance(data, dict) else None
    if not isinstance(values, list):
        return ""
    return ",".join(str(value).strip() for value in values if str(value).strip())


def parse_job_json_date(value: object) -> datetime | None:
    """Parse a job.json-style date value into a naive UTC datetime.

    CryoSPARC stores dates in job.json either as a plain ISO8601 string or as
    MongoDB extended JSON: {"$date": "<ISO8601>"}. Returns None if value is
    missing or cannot be parsed. Any timezone info present in the source is
    converted to UTC and then dropped, so the result can be compared and
    sorted directly against other dates produced by this function.
    """
    if isinstance(value, dict):
        value = value.get("$date")
    if not isinstance(value, str) or not value.strip():
        return None

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    dt = None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue

    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def get_live_session_date(session_dir: Path) -> str:
    """Best-effort ISO8601 date for a Live session, derived from exposures.bson.

    Verified against a real exposures.bson: a single top-level BSON document
    wraps an 'exposures' list, and each exposure record has a top-level
    'discovered_at' field -- a Unix epoch float recording when CryoSPARC Live
    found that micrograph. This returns the MOST RECENT discovered_at across
    all exposures, i.e. when the session last found new data, which is a far
    more accurate staleness signal than the exposures.bson file's own mtime
    (which can be touched by unrelated housekeeping).

    Decodes with pymongo's RawBSONDocument, which decodes lazily: every other
    per-exposure field (embedded preview-image id lists, per-movie path and
    group metadata, etc.) is left undecoded. On a real ~100 MB/15,000-exposure
    exposures.bson this is roughly 3x faster than a full bson.decode_all, and
    avoids allocating Python objects for data this function never looks at.

    Returns "" (caller falls back to directory mtime) if pymongo isn't
    installed, exposures.bson is missing, or the file can't be parsed as
    expected -- this is unverified external data that may be absent,
    truncated, or from a future CryoSPARC format, so failures here must
    never abort the scan.
    """
    if bson is None:
        return ""
    bson_path = session_dir / "exposures.bson"
    if not bson_path.is_file():
        return ""
    try:
        data = bson_path.read_bytes()
        opts = CodecOptions(document_class=RawBSONDocument)
        doc = bson.BSON(data).decode(codec_options=opts)
        exposures = doc.get("exposures")
        if not exposures:
            return ""
        latest = None
        for exposure in exposures:
            value = exposure.get("discovered_at")
            if isinstance(value, (int, float)) and (latest is None or value > latest):
                latest = value
        if latest is None:
            return ""
        return datetime.fromtimestamp(latest, tz=timezone.utc).replace(tzinfo=None).isoformat()
    except Exception:
        return ""


def get_job_date(job_dir: Path) -> str:
    """Best-effort ISO8601 date for a job or Live session.

    Written to the CSV's 'date' column and consumed by the --older-than
    filter in cryosparc-storage-display. Tries, in order: job.json's
    created_at (CryoSPARC's own record of when a J<number> job was created);
    for Live sessions (S<number>, which have no job.json) the most recent
    discovered_at in exposures.bson (see get_live_session_date()); and
    finally the directory's own mtime, so every row gets a usable date
    rather than an empty one.
    """
    json_path = job_dir / "job.json"
    if json_path.is_file():
        try:
            with json_path.open("r", encoding="utf-8", errors="replace") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                parsed = parse_job_json_date(data.get("created_at"))
                if parsed is not None:
                    return parsed.isoformat()
        except (OSError, json.JSONDecodeError, UnicodeError):
            pass

    live_date = get_live_session_date(job_dir)
    if live_date:
        return live_date

    try:
        mtime = job_dir.stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(mtime, tz=timezone.utc).replace(tzinfo=None).isoformat()


def gnu_du_available(du_bin: str) -> bool:
    try:
        proc = subprocess.run(
            [du_bin, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and "GNU coreutils" in proc.stdout


def measure_bytes(job_dir: Path, du_bin: str, size_mode: str, gnu_du: bool) -> int:
    """Measure one job directory with exactly one successful du traversal.

    The old shell script ran `du -sb` once as a capability test and then ran it
    a second time to capture the value.  On a large tree that doubles the work.
    Here we capture the first invocation directly.
    """
    if size_mode == "apparent":
        if gnu_du:
            command = [du_bin, "-sb", "--", str(job_dir)]
            multiplier = 1
        else:
            # Portable fallback; this is allocated size, not exact apparent size.
            command = [du_bin, "-sk", str(job_dir)]
            multiplier = 1024
    elif size_mode == "disk":
        if gnu_du:
            command = [du_bin, "-s", "-B1", "--", str(job_dir)]
            multiplier = 1
        else:
            command = [du_bin, "-sk", str(job_dir)]
            multiplier = 1024
    else:
        raise ValueError(f"Unknown size mode: {size_mode}")

    proc = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        message = proc.stderr.strip() or f"du exited with status {proc.returncode}"
        raise RuntimeError(message)

    line = proc.stdout.splitlines()[0] if proc.stdout else ""
    token = line.split(maxsplit=1)[0] if line else ""
    try:
        return int(token) * multiplier
    except ValueError as exc:
        raise RuntimeError(f"Could not parse du output: {line!r}") from exc


def scan_one(job: Job, du_bin: str, size_mode: str, gnu_du: bool) -> JobResult:
    bytes_used = measure_bytes(job.path, du_bin, size_mode, gnu_du)
    job_type = detect_job_type(job.path)
    return JobResult(
        job=job,
        bytes_used=bytes_used,
        job_type=job_type,
        workspace_uids=extract_workspace_uids(job.path, job_type),
        date=get_job_date(job.path),
    )


def existing_completed_paths(output: Path) -> set[str]:
    """Read successfully completed paths from an existing output CSV."""
    completed: set[str] = set()
    if not output.exists() or output.stat().st_size == 0:
        return completed

    with output.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "path" not in reader.fieldnames:
            raise RuntimeError(f"Cannot resume: {output} does not contain a 'path' column.")
        for row in reader:
            path = row.get("path", "").strip()
            bytes_text = row.get("bytes", "").strip()
            if path and bytes_text.isdigit():
                completed.add(os.path.normpath(path))
    return completed


def open_output(output: Path, resume: bool, overwrite: bool):
    output.parent.mkdir(parents=True, exist_ok=True)

    if output.exists() and output.stat().st_size > 0:
        if overwrite:
            mode = "w"
            write_header = True
        elif resume:
            # Do not append new rows to a CSV created by an older scanner schema.
            # In particular, the pre-owner format was:
            #   tld,project,job,bytes,job_type,path
            # Appending seven-field rows to that header silently corrupts the CSV.
            with output.open("r", encoding="utf-8-sig", newline="") as existing:
                reader = csv.reader(existing)
                header = next(reader, [])
            if header != OUTPUT_FIELDS:
                raise RuntimeError(
                    "Cannot resume because the existing CSV schema does not match this scanner. "
                    f"Expected: {','.join(OUTPUT_FIELDS)}; found: {','.join(header) or '<empty>'}. "
                    "Use --overwrite to start a new owner-aware scan."
                )
            mode = "a"
            write_header = False
        else:
            raise RuntimeError(
                f"Output already exists: {output}. Use --resume to continue it or --overwrite to replace it."
            )
    else:
        mode = "w"
        write_header = True

    handle = output.open(mode, encoding="utf-8", newline="", buffering=1)
    writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, quoting=csv.QUOTE_MINIMAL)
    if write_header:
        writer.writeheader()
        handle.flush()
    return handle, writer


def result_to_row(result: JobResult) -> dict[str, object]:
    project_dir = result.job.project.directory
    return {
        "tld": str(project_dir.parent),
        "project": project_dir.name,
        "owner": result.job.project.owner,
        "job": result.job.job_name,
        "bytes": result.bytes_used,
        "job_type": result.job_type,
        "workspace_uids": result.workspace_uids,
        "path": str(result.job.path),
        "date": result.date,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure CryoSPARC job directory sizes from a projectData CSV or HTML export."
    )
    parser.add_argument("input", type=Path, help="CryoSPARC projectData CSV, minimal owner/directory CSV, or saved HTML page")
    parser.add_argument("-o", "--output", type=Path, default=Path("jobs.csv"), help="Output CSV (default: jobs.csv)")
    parser.add_argument(
        "-p", "--parallel", type=int, default=2,
        help="Number of concurrent du processes (default: 2; conservative for Lustre)",
    )
    parser.add_argument(
        "--owner", action="append", default=[], metavar="USER",
        help="Only scan projects whose Owner exactly matches USER. Repeat for multiple owners.",
    )
    parser.add_argument(
        "--size-mode", choices=("apparent", "disk"), default="apparent",
        help=(
            "Size reported by du: 'apparent' preserves the old script's du -sb behaviour; "
            "'disk' reports allocated filesystem usage (default: apparent)."
        ),
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Append to an existing output and skip job paths that already have a numeric bytes value.",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Replace an existing output file instead of refusing to run.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show selected projects and discovered job counts without running du or writing output.",
    )
    parser.add_argument(
        "--list-owners", action="store_true",
        help="List owners and project counts in the input, then exit.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if args.parallel < 1:
        print("ERROR: --parallel must be at least 1", file=sys.stderr)
        return 2
    if args.resume and args.overwrite:
        print("ERROR: --resume and --overwrite are mutually exclusive", file=sys.stderr)
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
    print(f"Size mode: {args.size_mode}")
    print(f"Parallel du workers: {args.parallel}")

    jobs: list[Job] = []
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
            project_jobs = discover_jobs(project)
        except OSError as exc:
            print(f"WARNING: cannot scan {project.directory}: {exc}", file=sys.stderr)
            continue
        jobs.extend(project_jobs)
        if args.dry_run:
            print(
                f"{project.project_id or '-'}\t{project.owner or '-'}\t"
                f"{len(project_jobs)} job(s)\t{project.directory}"
            )

    print(f"Discovered {len(jobs)} J<number>/S<number> job/session directorie(s)")
    if missing_projects:
        print(f"Skipped {missing_projects} missing project directorie(s)")

    if args.dry_run:
        return 0

    du_bin = shutil.which("du")
    if du_bin is None:
        print("ERROR: du not found in PATH", file=sys.stderr)
        return 1
    gnu_du = gnu_du_available(du_bin)
    if args.size_mode == "apparent" and not gnu_du:
        print(
            "WARNING: GNU du was not detected; portable fallback uses du -sk, "
            "which reports allocated rather than exact apparent size.",
            file=sys.stderr,
        )

    try:
        completed = existing_completed_paths(args.output) if args.resume else set()
        pending = [job for job in jobs if os.path.normpath(str(job.path)) not in completed]
        if args.resume:
            print(f"Resume: {len(completed)} existing completed path(s); {len(pending)} job(s) pending")

        handle, writer = open_output(args.output, args.resume, args.overwrite)
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    successes = 0
    failures = 0
    try:
        with handle:
            with ThreadPoolExecutor(max_workers=args.parallel) as pool:
                future_to_job = {
                    pool.submit(scan_one, job, du_bin, args.size_mode, gnu_du): job for job in pending
                }
                for future in as_completed(future_to_job):
                    job = future_to_job[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # keep other jobs running; failed jobs remain resumable
                        failures += 1
                        print(f"ERROR: {job.path}: {exc}", file=sys.stderr)
                        continue

                    writer.writerow(result_to_row(result))
                    handle.flush()  # make interruption recovery useful
                    successes += 1
                    date_str = result.date[:10] if result.date else "unknown-date"
                    print(
                        f"[{successes}/{len(pending)}] {job.project.project_id or job.project.directory.name}/"
                        f"{job.job_name}: {result.bytes_used} bytes ({result.job_type}, {date_str}) "
                        f"owner={job.project.owner or '-'}",
                        flush=True,
                    )
    except KeyboardInterrupt:
        print("\nInterrupted. Completed rows already written can be reused with --resume.", file=sys.stderr)
        return 130

    print(f"Done. Wrote {successes} new row(s) to: {args.output}")
    if failures:
        print(f"{failures} job(s) failed and were not written; --resume will retry them.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
