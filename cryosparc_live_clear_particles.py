#!/usr/bin/env python3
"""
Safely remove cryoSPARC Live extracted particle MRC stacks while leaving
micrographs and extraction metadata intact.

Examples:
    cryosparc_live_clear_particles.py \
        --project-dir /path/to/project \
        --live-uid S1 \
        --dry-run

    cryosparc_live_clear_particles.py \
        --project-dir /path/to/project \
        --live-uid S1 \
        --scan-mode estimate
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


SAMPLE_FILES_PER_BLOB_DIR = 10


def human_size(num_bytes):
    """Return a human-readable IEC byte size."""
    value = float(num_bytes)
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return "{} {}".format(int(value), unit)
            return "{:.2f} {}".format(value, unit)
        value /= 1024.0


def directory_size(root):
    """Sum sizes of regular files below root without following symlinks."""
    total = 0
    if not root.is_dir():
        return total

    for dirpath, dirnames, filenames in os.walk(str(root), followlinks=False):
        dirnames[:] = [
            d for d in dirnames
            if not os.path.islink(os.path.join(dirpath, d))
        ]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                if os.path.isfile(path) and not os.path.islink(path):
                    total += os.path.getsize(path)
            except OSError:
                pass
    return total


def find_mrc_files(blob_dir):
    """Return regular .mrc files below blob_dir, without following symlinks."""
    targets = []
    for dirpath, dirnames, filenames in os.walk(str(blob_dir), followlinks=False):
        dirnames[:] = [
            d for d in dirnames
            if not os.path.islink(os.path.join(dirpath, d))
        ]
        for name in filenames:
            if not name.lower().endswith(".mrc"):
                continue
            path = Path(dirpath) / name
            try:
                if path.is_file() and not path.is_symlink():
                    targets.append(path)
            except OSError:
                continue
    return sorted(targets)


def estimate_mrc_size_by_blob_dir(blob_dir, targets):
    """
    Estimate total MRC size by sampling up to the first 10 target files in each
    immediate blob/<directory> group and extrapolating using that group's count.

    Files directly inside blob/ are treated as their own group. Any unexpected
    deeper layout is grouped by the first path component below blob/.
    """
    groups = {}
    for path in targets:
        try:
            rel = path.relative_to(blob_dir)
        except ValueError:
            continue
        parts = rel.parts
        group = parts[0] if len(parts) > 1 else "."
        groups.setdefault(group, []).append(path)

    estimated_total = 0.0
    sampled_files = 0
    unreadable_samples = 0
    group_results = []

    for group in sorted(groups):
        files = groups[group]
        sample = files[:SAMPLE_FILES_PER_BLOB_DIR]
        sizes = []
        for path in sample:
            try:
                sizes.append(path.stat().st_size)
            except OSError:
                unreadable_samples += 1

        if sizes:
            average = float(sum(sizes)) / len(sizes)
            estimate = average * len(files)
            estimated_total += estimate
            sampled_files += len(sizes)
            group_results.append((group, len(files), len(sizes), estimate))
        else:
            group_results.append((group, len(files), 0, None))

    return int(round(estimated_total)), sampled_files, unreadable_samples, group_results


def get_live_workspace(project_dir, live_uid):
    """Best-effort lookup of the live session in workspaces.json."""
    workspace_file = project_dir / "workspaces.json"
    if not workspace_file.is_file():
        return None

    try:
        with workspace_file.open("r") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None

    if not isinstance(data, list):
        return None

    for workspace in data:
        if not isinstance(workspace, dict):
            continue
        if (
            workspace.get("session_uid") == live_uid
            or workspace.get("session_dir") == live_uid
        ):
            return workspace
    return None


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Delete only extracted particle .mrc stacks from a cryoSPARC Live "
            "session, preserving micrographs and metadata."
        )
    )
    parser.add_argument(
        "--project-dir",
        required=True,
        help="Path to the cryoSPARC project directory",
    )
    parser.add_argument(
        "--live-uid",
        required=True,
        help="Live session UID/directory, e.g. S1",
    )
    parser.add_argument(
        "--scan-mode",
        choices=("full", "estimate", "skip"),
        default="full",
        help=(
            "Size scan mode: full=measure every file exactly (default); "
            "estimate=sample up to 10 MRC files per blob subdirectory and "
            "extrapolate; skip=do not calculate or report sizes"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be removed without deleting anything",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    project_dir = Path(args.project_dir).expanduser().resolve()
    live_uid = args.live_uid.strip()

    if not re.match(r"^S[0-9]+$", live_uid):
        print(
            "ERROR: --live-uid must look like S1, S2, S3, etc.",
            file=sys.stderr,
        )
        return 2

    if not project_dir.is_dir():
        print(
            "ERROR: Project directory does not exist: {}".format(project_dir),
            file=sys.stderr,
        )
        return 2

    if not (project_dir / "project.json").is_file():
        print(
            "ERROR: {} does not look like a cryoSPARC project "
            "(project.json not found).".format(project_dir),
            file=sys.stderr,
        )
        return 2

    live_dir = project_dir / live_uid
    extract_dir = live_dir / "extract"
    blob_dir = extract_dir / "blob"

    if not live_dir.is_dir():
        print(
            "ERROR: Live session directory not found: {}".format(live_dir),
            file=sys.stderr,
        )
        return 2

    if not extract_dir.is_dir():
        print(
            "ERROR: Extract directory not found: {}".format(extract_dir),
            file=sys.stderr,
        )
        return 2

    if not blob_dir.is_dir():
        print(
            "ERROR: Particle blob directory not found: {}".format(blob_dir),
            file=sys.stderr,
        )
        return 2

    workspace = get_live_workspace(project_dir, live_uid)
    if workspace is not None:
        workspace_type = workspace.get("workspace_type")
        status = workspace.get("status")
        title = workspace.get("title")

        print("Live session: {}".format(live_uid))
        if title:
            print("Workspace title: {}".format(title))
        if workspace_type:
            print("Workspace type: {}".format(workspace_type))
        if status:
            print("Workspace status: {}".format(status))

        if workspace_type and workspace_type != "live":
            print(
                "ERROR: {} is present in workspaces.json but is not marked "
                "as a live workspace.".format(live_uid),
                file=sys.stderr,
            )
            return 2

        if status in ("running", "active"):
            print(
                "ERROR: Live session {} is marked '{}'. Pause or stop it "
                "before clearing extracted particles.".format(live_uid, status),
                file=sys.stderr,
            )
            return 2
    else:
        print(
            "WARNING: Could not verify {} in workspaces.json; continuing with "
            "filesystem safety checks only.".format(live_uid)
        )

    print("Extract directory: {}".format(extract_dir))
    print("Particle blob directory: {}".format(blob_dir))
    print("Scan mode: {}".format(args.scan_mode))
    print()

    print("Locating MRC particle files...")
    targets = find_mrc_files(blob_dir)
    print("MRC particle files found: {}".format(len(targets)))

    exact_target_total = None
    estimated_target_total = None

    if args.scan_mode == "full":
        print("Calculating exact sizes...")
        extract_total = directory_size(extract_dir)
        target_sizes = []
        unreadable = 0
        for path in targets:
            try:
                target_sizes.append((path, path.stat().st_size))
            except OSError:
                unreadable += 1

        exact_target_total = sum(size for _, size in target_sizes)
        print("Total size of extract directory: {}".format(human_size(extract_total)))
        print(
            "Space occupied by target MRC files: {}".format(
                human_size(exact_target_total)
            )
        )
        if unreadable:
            print(
                "WARNING: {} target file(s) could not be stat'ed.".format(unreadable)
            )

    elif args.scan_mode == "estimate":
        print(
            "Estimating size from up to {} MRC files per blob subdirectory...".format(
                SAMPLE_FILES_PER_BLOB_DIR
            )
        )
        (
            estimated_target_total,
            sampled_files,
            unreadable_samples,
            group_results,
        ) = estimate_mrc_size_by_blob_dir(blob_dir, targets)

        print(
            "Estimated space occupied by target MRC files: {}".format(
                human_size(estimated_target_total)
            )
        )
        print(
            "Estimate sampled {} file(s) across {} blob subdirector{}.".format(
                sampled_files,
                len(group_results),
                "y" if len(group_results) == 1 else "ies",
            )
        )
        if unreadable_samples:
            print(
                "WARNING: {} sampled file(s) could not be stat'ed.".format(
                    unreadable_samples
                )
            )

    if not targets:
        print("Nothing to delete.")
        return 0

    if args.dry_run:
        print()
        print("DRY RUN: no files were deleted.")
        print("Run again without --dry-run to delete these .mrc particle stacks.")
        return 0

    print()
    print("ONLY regular *.mrc files under this directory will be deleted:")
    print("  {}".format(blob_dir))
    print()
    print("Files to delete: {}".format(len(targets)))
    if args.scan_mode == "full":
        print("Expected space to free: {}".format(human_size(exact_target_total)))
    elif args.scan_mode == "estimate":
        print(
            "Estimated space to free: {}".format(
                human_size(estimated_target_total)
            )
        )
    print()

    answer = input("Are you sure you want to continue? Type 'yes' to delete: ")
    if answer.strip().lower() != "yes":
        print("Cancelled. No files were deleted.")
        return 1

    deleted_count = 0
    deleted_bytes = 0
    failed = []

    if args.scan_mode == "full":
        size_by_path = dict(target_sizes)
    else:
        size_by_path = {}

    for path in targets:
        try:
            path.unlink()
            deleted_count += 1
            if args.scan_mode == "full":
                deleted_bytes += size_by_path.get(path, 0)
        except OSError as exc:
            failed.append((path, exc))

    print()
    print("Deleted {} MRC file(s).".format(deleted_count))
    if args.scan_mode == "full":
        print("Space saved: {}".format(human_size(deleted_bytes)))
    elif args.scan_mode == "estimate":
        if deleted_count == len(targets):
            saved_estimate = estimated_target_total
        else:
            saved_estimate = int(
                round(estimated_target_total * (float(deleted_count) / len(targets)))
            )
        print("Estimated space saved: {}".format(human_size(saved_estimate)))

    if failed:
        print(
            "WARNING: {} file(s) could not be deleted:".format(len(failed)),
            file=sys.stderr,
        )
        for path, exc in failed[:20]:
            print("  {}: {}".format(path, exc), file=sys.stderr)
        if len(failed) > 20:
            print(
                "  ... and {} more".format(len(failed) - 20),
                file=sys.stderr,
            )
        return 3

    return 0


if __name__ == "__main__":
    sys.exit(main())
