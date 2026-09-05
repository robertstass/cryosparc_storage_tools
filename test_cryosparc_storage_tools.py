#!/usr/bin/env python3
"""Combined smoke/integration tests for cryosparc_storage_tools.

All tests use temporary or synthetic CryoSPARC-like directories. The Live
particle cleanup test always uses --dry-run, so it never intentionally deletes
particle stacks. No real CryoSPARC installation is required.

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
    assert len(jobs) == 1, jobs
    assert jobs[0]["owner"] == "TestOwner"
    assert jobs[0]["job"] == "J1"
    assert jobs[0]["job_type"] == "import_movies"
    assert int(jobs[0]["bytes"]) > 0
    print("OK: project-source discovery and storage scan")
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


def main() -> int:
    required = [
        "cryosparc_archive.py",
        "cryosparc_live_clear_particles.py",
        "cryosparc_storage_scan.py",
        "cryosparc_storage_display.py",
        "cryosparc_manage_projectData_to_csv.py",
        "cryosparc_project_sources_owner_directory.py",
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
    if not KEEP_TEST_OUTPUT:
        print("Cleaning up test directory...")
        tempdir.cleanup()

    print("ALL CRYOSPARC STORAGE TOOLS TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
