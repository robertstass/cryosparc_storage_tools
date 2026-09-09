#!/usr/bin/env python3
"""Combined smoke/integration tests for cryosparc_storage_tools.

All tests use temporary or synthetic CryoSPARC-like directories. The Live
particle cleanup test always uses --dry-run, so it never intentionally deletes
particle stacks. The Live particle scan test only exercises the strictly
read-only cryosparc_live_scan_particles.py. No real CryoSPARC installation is
required.

Usage:
    python test_cryosparc_storage_tools.py

The archive test writes a temporary overview HTML. Automated checks cover the
filesystem/output structure; interactive browser behaviour is not exercised.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KEEP_TEST_OUTPUT = True

# Windows commonly defaults redirected/console text to cp1252. The tools emit
# Unicode punctuation/symbols, so make the test harness UTF-8-safe end-to-end.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

HERE = Path(__file__).resolve().parent


def run_script(name: str, *args: object, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run one repository script with deterministic UTF-8 text handling."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, str(HERE / name), *map(str, args)],
        cwd=cwd or HERE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"{name} exited {proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc


def make_project(path: Path, uid: str = "P1", title: str = "Synthetic project") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "project.json").write_text(
        json.dumps({"uid": uid, "title": title}), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Archive test
# ---------------------------------------------------------------------------

def make_archive_job(
    project_dir: Path,
    uid: str,
    job_type: str,
    parents: list[str] | None = None,
    created: str = "2026-05-15T10:00:00.000Z",
    workspace_uids: list[str] | None = None,
) -> None:
    d = project_dir / uid
    d.mkdir(parents=True, exist_ok=True)
    doc = {
        "uid": uid,
        "job_type": job_type,
        "title": job_type,
        "status": "completed",
        "created_at": {"$date": created},
        "parents": parents or [],
        "children": [],
        "workspace_uids": workspace_uids or ["W1"],
    }
    (d / "job.json").write_text(json.dumps(doc), encoding="utf-8")


def link_archive_children(project_dir: Path) -> None:
    all_json: dict[str, dict] = {}
    for d in project_dir.iterdir():
        path = d / "job.json"
        if d.is_dir() and path.exists():
            all_json[d.name] = json.loads(path.read_text(encoding="utf-8"))
    for uid, doc in all_json.items():
        for parent in doc.get("parents", []):
            if parent in all_json:
                all_json[parent]["children"].append(uid)
    for uid, doc in all_json.items():
        (project_dir / uid / "job.json").write_text(json.dumps(doc), encoding="utf-8")


def build_archive_project(project_dir: Path) -> None:
    make_project(project_dir, uid="P1", title="My Test Project 2026")
    (project_dir / "workspaces.json").write_text(
        json.dumps(
            [
                {"uid": "W1", "title": "Main Processing"},
                {"uid": "W2", "title": "Refinement Round 2"},
            ]
        ),
        encoding="utf-8",
    )
    make_archive_job(project_dir, "J1", "import_movies", created="2026-05-15T09:00:00.000Z")
    make_archive_job(project_dir, "J2", "patch_motion_correction", ["J1"], "2026-05-15T09:10:00.000Z")
    make_archive_job(project_dir, "J3", "patch_ctf_estimation", ["J2"], "2026-05-15T09:20:00.000Z")
    make_archive_job(project_dir, "J4", "blob_picker", ["J3"], "2026-05-15T09:30:00.000Z")
    make_archive_job(project_dir, "J5", "extract_micrographs_multi", ["J4", "J3"], "2026-05-15T09:40:00.000Z")
    make_archive_job(project_dir, "J6", "class_2D_new", ["J5"], "2026-05-15T09:50:00.000Z")
    make_archive_job(project_dir, "J7", "new_nonuniform_refine", ["J6"], "2026-05-15T10:00:00.000Z", ["W2"])
    make_archive_job(project_dir, "J8", "new_local_refine", ["J7"], "2026-05-15T10:10:00.000Z", ["W2"])
    make_archive_job(project_dir, "J9", "sharpen", ["J8"], "2026-05-15T10:20:00.000Z", ["W2"])
    make_archive_job(project_dir, "J10", "deepemhancer", ["J8"], "2026-05-15T10:21:00.000Z", ["W2"])
    make_archive_job(project_dir, "J11", "orientation_diagnostics", ["J8"], "2026-05-15T10:22:00.000Z", ["W2"])
    make_archive_job(project_dir, "J12", "local_resolution", ["J8"], "2026-05-15T10:23:00.000Z", ["W2"])
    make_archive_job(project_dir, "J13", "local_filter", ["J8"], "2026-05-15T10:24:00.000Z", ["W2"])

    # Exercise Live-session parameter recovery when pymongo/bson is available,
    # but keep the test runnable without that optional dependency.
    try:
        import bson  # type: ignore
    except ImportError:
        bson = None

    if bson is not None:
        make_archive_job(project_dir, "J20", "live_2", [], "2026-05-15T08:00:00.000Z", ["W1"])
        job_path = project_dir / "J20" / "job.json"
        doc = json.loads(job_path.read_text(encoding="utf-8"))
        doc["params_base"] = {"session_uid": {"title": "Session UID", "value": "S1"}}
        job_path.write_text(json.dumps(doc), encoding="utf-8")
        session_dir = project_dir / "S1"
        session_dir.mkdir(exist_ok=True)
        exposures = {
            "exposures": [
                {
                    "abs_file_path": "/data/bowmore2/atlas1/GridSquare_01/FoilHole_001_Data.tiff",
                    "manual_reject": False,
                    "threshold_reject": False,
                    "failed": False,
                    "deleted": False,
                    "groups": {
                        "exposure": {
                            "mscope_params": {
                                "exp_group_id": [1],
                                "accel_kv": [300.0],
                                "cs_mm": [2.7],
                                "total_dose_e_per_A2": [50.0],
                                "phase_plate": [0],
                                "neg_stain": [0],
                            },
                            "movie_blob": {
                                "path": ["S1/import_movies/FoilHole_001_Data.tiff"],
                                "psize_A": [0.83],
                            },
                            "micrograph_blob": {"psize_A": [0.83]},
                        }
                    },
                }
            ]
        }
        (session_dir / "exposures.bson").write_bytes(bson.encode(exposures))

    link_archive_children(project_dir)


def test_archive(tmp: Path) -> None:
    workdir = tmp / "archive_test"
    project_dir = workdir / "CS-test-project"
    project_dir.mkdir(parents=True)
    build_archive_project(project_dir)

    proc = run_script(
        "cryosparc_archive.py",
        "--project-dir",
        project_dir,
        "--deep",
        "J8",
        "J9",
        "J10",
        "J11",
        "J12",
        "J13",
        cwd=workdir,
    )

    expected_dir = workdir / "My_Test_Project_2026_archive"
    assert expected_dir.is_dir(), proc.stdout
    html_path = expected_dir / "My_Test_Project_2026_archive.html"
    summary_path = expected_dir / "My_Test_Project_2026_archive_summary.json"
    assert html_path.is_file()
    assert summary_path.is_file()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    modes = {
        uid: summary["deep_archive_results"].get(uid, {}).get("mode")
        for uid in ("J9", "J10", "J11", "J12", "J13")
    }
    assert modes == {uid: "all" for uid in ("J9", "J10", "J11", "J12", "J13")}, modes
    assert (expected_dir / "other_jobs" / "J1").is_dir()
    assert not (expected_dir / "J1").exists()

    # The current archive HTML contains Unicode symbols. Confirm the generated
    # document survives an explicit UTF-8 read; this guards the Windows cp1252
    # failure that prompted consolidation of these tests.
    html_text = html_path.read_text(encoding="utf-8")
    assert "CryoSPARC archive overview" in html_text

    j20 = summary.get("jobs", {}).get("J20")
    if j20 and j20.get("live_session_params"):
        live_params = list(j20["live_session_params"].values())[0]
        abs_path = live_params.get("example_movie_path_absolute", {}).get("value")
        assert abs_path and abs_path.startswith("/data/bowmore2/"), abs_path

    print("OK: cryosparc_archive synthetic-project integration test")


# ---------------------------------------------------------------------------
# Storage utilities
# ---------------------------------------------------------------------------

def test_project_sources_and_storage_scan(tmp: Path) -> tuple[Path, Path]:
    root = tmp / "cryosparc_root"
    project = root / "CS-test"
    make_project(project)

    job = project / "J1"
    job.mkdir()
    (job / "job.json").write_text(
        json.dumps({"uid": "J1", "job_type": "import_movies"}), encoding="utf-8"
    )
    (job / "payload.bin").write_bytes(b"x" * 4096)

    # Regression coverage: Live session (S<number>) directories must be
    # discovered and sized alongside regular J<number> jobs, and reported
    # with job_type "live" (detected via the exposures.bson marker file).
    session = project / "S1"
    session.mkdir()
    (session / "exposures.bson").write_bytes(b"y" * 2048)

    projects_csv = tmp / "projects.csv"
    run_script(
        "cryosparc_project_sources_owner_directory.py",
        f"TestOwner:{root}",
        "-o",
        projects_csv,
    )
    with projects_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [{"owner": "TestOwner", "directory": str(project)}], rows

    jobs_csv = tmp / "jobs.csv"
    run_script(
        "cryosparc_storage_scan.py",
        projects_csv,
        "-o",
        jobs_csv,
        "--parallel",
        "1",
    )
    with jobs_csv.open(newline="", encoding="utf-8") as handle:
        jobs = list(csv.DictReader(handle))
    assert len(jobs) == 2, jobs
    by_job = {row["job"]: row for row in jobs}
    assert by_job["J1"]["owner"] == "TestOwner"
    assert by_job["J1"]["job_type"] == "import_movies"
    assert int(by_job["J1"]["bytes"]) > 0
    assert by_job["S1"]["owner"] == "TestOwner"
    assert by_job["S1"]["job_type"] == "live"
    assert int(by_job["S1"]["bytes"]) > 0
    print("OK: project-source discovery and storage scan (jobs + Live sessions)")
    return project, jobs_csv


def test_html_converter(tmp: Path, project_dir: Path) -> None:
    html_path = tmp / "projectData.html"
    html_path.write_text(
        """<!doctype html><html><body>
<table><tr><th>Project ID</th><th>Title</th><th>Owner</th><th>Directory</th></tr>
<tr><td>P1</td><td>Synthetic project</td><td>TestOwner</td><td>{}</td></tr></table>
</body></html>""".format(project_dir),
        encoding="utf-8",
    )
    output = tmp / "projectData.csv"
    run_script("cryosparc_manage_projectData_to_csv.py", html_path, "-o", output)
    with output.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["Project ID"] == "P1"
    assert rows[0]["Owner"] == "TestOwner"
    print("OK: Manage ProjectData HTML-to-CSV conversion")


def test_storage_display(tmp: Path, jobs_csv: Path) -> None:
    report = tmp / "report.html"
    run_script("cryosparc_storage_display.py", jobs_csv, "--out", report, "--no-open")
    text = report.read_text(encoding="utf-8")
    assert "Storage report" in text
    assert "plotly" in text.lower()
    print("OK: storage display HTML generation")


def test_live_clear_dry_run(tmp: Path) -> None:
    project = tmp / "CS-live"
    make_project(project, uid="P2", title="Synthetic live project")
    blob = project / "S1" / "extract" / "blob" / "group1"
    blob.mkdir(parents=True)
    particle = blob / "particles.mrc"
    particle.write_bytes(b"not-a-real-mrc-but-safe-for-filesystem-test")

    proc = run_script(
        "cryosparc_live_clear_particles.py",
        "--project-dir",
        project,
        "--live-uid",
        "S1",
        "--scan-mode",
        "full",
        "--dry-run",
    )
    assert "DRY RUN: no files were deleted." in proc.stdout
    assert particle.exists(), "dry run unexpectedly deleted the synthetic particle file"
    print("OK: Live particle cleanup dry-run leaves files untouched")


def test_find_mrc_files_avoids_per_file_stat_calls(tmp: Path) -> None:
    """Regression test for a hang seen on networked CryoSPARC storage.

    find_mrc_files()/directory_size() must classify entries using the type
    info os.scandir() already returns (DirEntry.is_dir()/is_file()), not by
    stat()-ing or lstat()-ing every matched file again. Each such extra call
    is a full network round trip on Lustre/NFS, so with tens of thousands of
    particle files a per-file stat/lstat pair alone can turn an
    --scan-mode skip listing into a multi-minute hang. This imports the
    functions directly (in-process) so it can count real stat/lstat calls.
    """
    root = tmp / "stat_call_regression"
    blob = root / "blob" / "group1"
    blob.mkdir(parents=True)
    file_count = 200
    for i in range(file_count):
        (blob / f"p_{i:04d}.mrc").write_bytes(b"x")

    sys.path.insert(0, str(HERE))
    import importlib

    import cryosparc_live_clear_particles as live_clear

    importlib.reload(live_clear)  # ensure a clean module (no leftover patches)

    calls = {"stat": 0, "lstat": 0}
    orig_stat, orig_lstat = os.stat, os.lstat

    def counting_stat(*a, **k):
        calls["stat"] += 1
        return orig_stat(*a, **k)

    def counting_lstat(*a, **k):
        calls["lstat"] += 1
        return orig_lstat(*a, **k)

    os.stat, os.lstat = counting_stat, counting_lstat
    try:
        targets = live_clear.find_mrc_files(blob.parent)
    finally:
        os.stat, os.lstat = orig_stat, orig_lstat

    assert len(targets) == file_count, targets
    total_calls = calls["stat"] + calls["lstat"]
    # A handful of calls (directory-level checks) are fine; anywhere near
    # one-per-file (the old isfile()+islink() pattern) is the regression.
    assert total_calls < file_count, (
        f"find_mrc_files() issued {total_calls} stat/lstat calls for {file_count} files "
        "-- it should reuse os.scandir()'s cached entry type instead of a stat call per file"
    )
    print(
        f"OK: find_mrc_files avoids per-file stat calls "
        f"({total_calls} stat/lstat calls for {file_count} files)"
    )


def test_live_scan_particles(tmp: Path) -> None:
    root = tmp / "live_scan_root"
    project = root / "CS-live-scan"
    make_project(project, uid="P3", title="Synthetic live scan project")

    # S1: has particles still on disk -> should be reported as reclaimable.
    blob = project / "S1" / "extract" / "blob" / "group1"
    blob.mkdir(parents=True)
    for i in range(3):
        (blob / f"particles_{i}.mrc").write_bytes(b"x" * (100 + i))

    # S2: extract/blob exists but the .mrc files are already gone.
    (project / "S2" / "extract" / "blob").mkdir(parents=True)

    # S3: no extract directory at all (never Live-processed / different job type).
    (project / "S3").mkdir(parents=True)

    projects_csv = tmp / "live_scan_projects.csv"
    with projects_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["owner", "directory"])
        writer.writeheader()
        writer.writerow({"owner": "TestOwner", "directory": str(project)})

    findings = tmp / "live_scan_findings.txt"
    proc = run_script(
        "cryosparc_live_scan_particles.py",
        projects_csv,
        "--scan-mode",
        "estimate",
        "-o",
        findings,
    )
    assert "Sessions with reclaimable particle .mrc files: 1" in proc.stdout, proc.stdout
    assert "S1" in proc.stdout and "3 mrc file(s)" in proc.stdout, proc.stdout
    assert "already cleared" in proc.stdout, proc.stdout
    assert "not Live-processed" in proc.stdout, proc.stdout
    assert findings.is_file()

    # Strictly read-only: every synthetic particle file must still be present.
    remaining = sorted((blob).glob("*.mrc"))
    assert len(remaining) == 3, remaining

    # --scan-mode skip must still detect the session without reporting a size.
    proc_skip = run_script(
        "cryosparc_live_scan_particles.py",
        projects_csv,
        "--scan-mode",
        "skip",
    )
    assert "Sessions with reclaimable particle .mrc files: 1" in proc_skip.stdout, proc_skip.stdout

    # --owner filtering excludes the project entirely.
    proc_owner = run_script(
        "cryosparc_live_scan_particles.py",
        projects_csv,
        "--owner",
        "NoSuchOwner",
    )
    assert "Discovered 0 Live session directorie(s)" in proc_owner.stdout, proc_owner.stdout

    print("OK: Live particle scan (read-only) identifies reclaimable sessions")


def main() -> int:
    required = [
        "cryosparc_archive.py",
        "cryosparc_live_clear_particles.py",
        "cryosparc_live_scan_particles.py",
        "cryosparc_storage_scan.py",
        "cryosparc_storage_display.py",
        "cryosparc_manage_projectData_to_csv.py",
        "cryosparc_project_sources_owner_directory.py",
        "cryosparc_project_io.py",
    ]
    missing = [name for name in required if not (HERE / name).is_file()]
    if missing:
        print(f"ERROR: missing script(s): {', '.join(missing)}", file=sys.stderr)
        return 2

    # Keep every generated test artifact under the directory the user invoked
    # the test from. This avoids OS temp/AppData locations and avoids writing
    # into the repository when the test is launched from elsewhere.
    # TemporaryDirectory removes the whole test tree automatically on exit.
    if KEEP_TEST_OUTPUT:
        raw = tempfile.mkdtemp(
            dir=Path.cwd(),
            prefix="cryosparc_storage_tools_test_",
        )
        tmp = Path(raw)
    else:
        tempdir = tempfile.TemporaryDirectory(
            dir=Path.cwd(),
            prefix="cryosparc_storage_tools_test_",
        )
        tmp = Path(tempdir.name)
    test_archive(tmp)
    project_dir, jobs_csv = test_project_sources_and_storage_scan(tmp)
    test_html_converter(tmp, project_dir)
    test_storage_display(tmp, jobs_csv)
    test_live_clear_dry_run(tmp)
    test_find_mrc_files_avoids_per_file_stat_calls(tmp)
    test_live_scan_particles(tmp)
    if not KEEP_TEST_OUTPUT:
        print("Cleaning up test directory...")
        tempdir.cleanup()

    print("ALL CRYOSPARC STORAGE TOOLS TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
