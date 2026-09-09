# cryosparc_storage_tools

A small collection of command-line utilities for understanding and managing the disk usage of CryoSPARC projects. The tools cover offline archival, selective cleanup of CryoSPARC Live particle stacks, project/job storage scans, and HTML reports of scan results.

Largely vibe coded by Robert Stass, Bowden group, Strubi, University of Oxford. 

> [!IMPORTANT]
> These tools have different safety properties. `cryosparc-archive` and `cryosparc-live-scan-particles` are read-only with respect to the CryoSPARC project directory. `cryosparc-live-clear-particles` deliberately deletes particle stack files. Read the tool-specific notes below before using either one.

## Included tools

| Script | Installed command | Purpose |
|---|---|---|
| `cryosparc_archive.py` | `cryosparc-archive` | Create a compact, offline archive of project metadata and selected job outputs. |
| `cryosparc_live_clear_particles.py` | `cryosparc-live-clear-particles` | Delete extracted particle `.mrc` stacks from a CryoSPARC Live session while retaining micrographs and extraction metadata. |
| `cryosparc_live_scan_particles.py` | `cryosparc-live-scan-particles` | Read-only scan for CryoSPARC Live sessions with reclaimable extracted-particle `.mrc` stacks, to identify sessions worth sending to `cryosparc-live-clear-particles`. |
| `cryosparc_storage_scan.py` | `cryosparc-storage-scan` | Measure storage used by CryoSPARC job directories from a projectData HTML/CSV source. |
| `cryosparc_storage_display.py` | `cryosparc-storage-display` | Turn storage-scan CSV output into an interactive Plotly HTML report. |
| `cryosparc_manage_projectData_to_csv.py` | `cryosparc-manage-projectdata-to-csv` | Convert a saved CryoSPARC Manage → ProjectData HTML page to CSV. |
| `cryosparc_project_sources_owner_directory.py` | `cryosparc-project-sources-owner-directory` | Build a minimal `owner,directory` CSV by scanning one or more owner-associated project roots. |
| `cryosparc_project_io.py` | *(not a CLI tool)* | Shared library for CryoSPARC project-table/CSV parsing, used by `cryosparc-storage-scan`, `cryosparc-live-scan-particles`, `cryosparc-manage-projectdata-to-csv`, and `cryosparc-project-sources-owner-directory`. |

## Installation

Installing the repository is recommended because `pyproject.toml` installs all Python dependencies and creates the shorter commands shown above.

```bash
git clone <your-repository-url>/cryosparc_storage_tools.git
cd cryosparc_storage_tools
python -m pip install .
```

For development, use an editable install:

```bash
python -m pip install -e .
```

Alternatively, create the supplied Conda environment and then install the repository to register the command-line entry points:

```bash
conda env create -f environment.yml
conda activate cryosparc-storage-tools
python -m pip install --no-deps .
```

The full dependency set is `numpy`, `mrcfile`, `pymongo` (provides `bson`), `cryosparc-tools`, `beautifulsoup4`, `plotly`, and `matplotlib`. Some individual scripts need only the Python standard library, but installing the repository as above makes all tools usable from the same environment.

---

## `cryosparc-archive`

`cryosparc_archive.py` creates an offline archive containing project/job metadata for the whole project, selected deeper job outputs, FSC data, recovered event-log plots, and a self-contained HTML overview of the project graph.

### Important: this is not CryoSPARC's built-in archiving

This utility is **distinct from CryoSPARC's built-in archive/restore functionality**. Its output is intended as a compact offline record of a project and selected results. A project archived with this script **cannot be reattached to CryoSPARC for further processing**.

The script itself is read-only with respect to the CryoSPARC project directory: it reads from `--project-dir` and writes to the archive output directory. It does **not** delete or shrink the original CryoSPARC project. Therefore, after making and validating an archive, you must delete the corresponding project/data through your normal CryoSPARC/storage-management process if your goal is actually to free space.

### Quick start

```bash
cryosparc-archive --project-dir /path/to/CS-myproject
```

To archive selected jobs more deeply:

```bash
cryosparc-archive \
    --project-dir /path/to/CS-myproject \
    --deep J50 J23 J8:last_iteration
```

With no `--outdir`, the output directory is named from the project's title with `_archive` appended. Open the generated HTML file in a browser to inspect the offline overview.

Every job gets its metadata mirrored. Jobs named with `--deep` can use `bare_minimum`, `minimalist`, `last_iteration`, or `all`, with sensible defaults selected according to job type. The script also preserves project/workspace metadata and produces machine- and human-readable summaries.

Useful options include:

```text
--project-dir PATH        CryoSPARC project directory (required)
--outdir PATH             Archive destination
--deep JOB_UID[:mode] ... Jobs to archive beyond bare metadata
--max-jobs N              Safety cap on jobs scanned
--skip-live-bson-copy     Skip copying large Live exposures.bson files
--dry-run                 Report planned actions without writing
```

---

## `cryosparc-live-clear-particles`

`cryosparc_live_clear_particles.py` removes extracted particle `.mrc` stacks under a selected CryoSPARC Live session's `extract/blob` directory while leaving micrographs and extraction metadata intact.

> [!CAUTION]
> This tool is **destructive**. Without `--dry-run`, it deletes files. It asks for an explicit `yes` confirmation before deletion and refuses obviously unsafe inputs, but you should still check the target carefully.

The deleted particle stacks can be re-extracted if needed, **provided the relevant Live session particle-export and micrograph-export jobs remain available**. This makes the tool useful when the large extracted stacks can be regenerated but the acquisition/micrograph data and Live processing history should be retained.

Always start with a dry run:

```bash
cryosparc-live-clear-particles \
    --project-dir /path/to/CS-project \
    --live-uid S1 \
    --dry-run
```

The default `--scan-mode full` measures the exact size of candidate files. `--scan-mode estimate` samples files to estimate reclaimable space more quickly, and `--scan-mode skip` avoids size calculation.

```bash
cryosparc-live-clear-particles \
    --project-dir /path/to/CS-project \
    --live-uid S1 \
    --scan-mode estimate
```

---

## `cryosparc-live-scan-particles`

`cryosparc_live_scan_particles.py` is a fast, **strictly read-only** companion to `cryosparc-storage-scan` and `cryosparc-live-clear-particles`. It accepts the same projectData CSV/HTML input as `cryosparc-storage-scan`, and for every selected project looks for CryoSPARC Live session directories (`S1`, `S2`, ...) and checks their `extract/blob` particle directories for `.mrc` files. It reuses the same particle-finding and `estimate`-mode sampling logic as `cryosparc-live-clear-particles`, so the numbers line up between the two tools.

Unlike `cryosparc-storage-scan`, this never runs `du` and never measures whole job directories — it only looks inside each session's `extract/blob` directory — so it is normally much quicker to run, especially with `--scan-mode skip`. It never deletes or modifies anything; use the reported `project directory` / `live_uid` pairs with `cryosparc-live-clear-particles` to actually reclaim space.

Some `S<n>` directories will have no `extract`/`blob` directory at all (never Live-processed, or a different job type), and others will have an `extract/blob` directory whose `.mrc` files were already deleted. Both are reported without error, separately from sessions that still have reclaimable particles.

```bash
# Estimate reclaimable particle-stack space for every project (default scan mode)
cryosparc-live-scan-particles projectData.csv

# Fastest pass: just report which sessions still have particle files, no sizing
cryosparc-live-scan-particles projectData.csv --scan-mode skip

# Exact sizes (slower: stats every .mrc file instead of sampling)
cryosparc-live-scan-particles projectData.csv --scan-mode full

# Restrict to one owner, and also save the findings to a text file
cryosparc-live-scan-particles projectData.csv --owner RobertS -o findings.txt
```

`--scan-mode estimate` (the default) matches `cryosparc-live-clear-particles`'s own estimate mode: it samples up to 10 `.mrc` files per blob subdirectory and extrapolates. `--scan-mode skip` skips sizing entirely and is the quickest option for a first pass across many projects. `-o/--output` writes the same findings to a text file in addition to stdout. `--parallel` controls how many Live sessions are inspected concurrently (default 4).

---

## `cryosparc-storage-scan`

`cryosparc_storage_scan.py` measures storage used by immediate `J<number>` job directories for projects listed in a CryoSPARC ProjectData export. It writes a CSV with:

```text
tld,project,owner,job,bytes,job_type,path
```

The input can be either a CSV or a saved CryoSPARC ProjectData HTML page.

### Obtaining the source HTML

**In cryosparc, navigate to Manage -> ProjectData -> Right click the page and click "Save as..."**

Then pass the saved `.html` file directly to the scanner:

```bash
cryosparc-storage-scan 'Manage_ projectData _ CryoSPARC.html' -o jobs.csv
```

HTML input requires BeautifulSoup, which is installed automatically with this repository. You can also first convert the page to CSV with `cryosparc-manage-projectdata-to-csv`.

### Long-running scans

A full storage scan is likely to take a long time on a large CryoSPARC installation — potentially several hours — because `du` must traverse many job directories. It is sensible to run it as a queued batch job where available, or under `nohup` so an SSH/network disconnection does not kill the scan. For example:

```bash
nohup cryosparc-storage-scan projectData.csv -o jobs.csv > out.log 2>&1 &
```

The scanner writes completed rows incrementally, so interrupted scans can be resumed:

```bash
cryosparc-storage-scan projectData.csv -o jobs.csv --resume
```

Other useful examples:

```bash
# Preview selected projects/jobs without running du
cryosparc-storage-scan projectData.csv --dry-run

# Restrict to one owner
cryosparc-storage-scan projectData.csv -o jobs.csv --owner RobertS

# Increase/decrease concurrent du processes (default 2)
cryosparc-storage-scan projectData.csv -o jobs.csv --parallel 2

# Show owners available in the input
cryosparc-storage-scan projectData.csv --list-owners
```

`--size-mode apparent` is the default and uses GNU `du -sb` where available. `--size-mode disk` reports allocated disk usage instead.

---

## `cryosparc-storage-display`

`cryosparc_storage_display.py` reads the CSV produced by `cryosparc-storage-scan` and creates an interactive Plotly HTML storage report. The report includes views of the largest jobs and projects, storage by owner, and storage by job type. Clicking plotted jobs/projects can copy associated paths to the clipboard in the generated page.

```bash
cryosparc-storage-display jobs.csv --out storage_report.html --no-open
```

Useful options include `--top`, `--pie-top`, `--type-top`, repeatable `--owner`, and `--colormap`. Named Matplotlib colormaps can be supplied through `--colormap`; comma-separated CSS colors are also accepted.

The generated report loads Plotly JavaScript from the Plotly CDN when opened, so viewing it requires network access unless you modify the generated HTML separately.

---

## `cryosparc-manage-projectdata-to-csv`

This utility extracts the CryoSPARC project table from a saved HTML page and writes it to CSV, taking care to ignore nested table rows that can otherwise confuse simple HTML-table parsers.

```bash
cryosparc-manage-projectdata-to-csv \
    'Manage_ projectData _ CryoSPARC.html' \
    -o projectData.csv
```

By default it looks for a table containing `Project ID`. You can select a particular table with `--table-index`, require terms with `--contains`, or inspect candidate tables with `--list-tables`.

The resulting CSV can be passed directly to `cryosparc-storage-scan`.

---

## `cryosparc-project-sources-owner-directory`

This is an alternative way to create scanner input when you already know which filesystem roots belong to which CryoSPARC owners. Each argument is `OWNER:DIRECTORY`. The script scans exactly one directory level below each root and includes subdirectories containing `project.json`.

```bash
cryosparc-project-sources-owner-directory \
    RobertS:/path/to/cryosparc \
    Alice:/another/cryosparc/root \
    -o projects.csv
```

The output is a minimal CSV:

```text
owner,directory
```

That file can then be scanned directly:

```bash
cryosparc-storage-scan projects.csv -o jobs.csv
```

Use `--append` to add newly discovered projects to an existing CSV.

---

## Typical storage-audit workflow

1. Obtain project locations either by saving **Manage → ProjectData** from CryoSPARC or by using `cryosparc-project-sources-owner-directory`.
2. If desired, convert the saved HTML to CSV with `cryosparc-manage-projectdata-to-csv`.
3. Run `cryosparc-live-scan-particles` first — it's quick and read-only, and highlights which Live sessions still have reclaimable particle `.mrc` stacks worth clearing.
4. Run `cryosparc-storage-scan` for the fuller picture (preferably in a queue or under `nohup` for a large installation).
5. Run `cryosparc-storage-display` on the resulting `jobs.csv` to inspect the largest projects/jobs and storage by owner/job type.
6. Decide what should be archived or removed. Use `cryosparc-archive` for an offline record of selected project results; use `cryosparc-live-clear-particles` to delete the Live particle stacks identified in step 3 (only when intentionally deleting regenerable data).

## Tests

A single self-contained test harness exercises the archive tool and the other storage utilities using temporary synthetic CryoSPARC-like projects:

```bash
python test_cryosparc_storage_tools.py
```

The archive integration test checks output naming, metadata layout and deep-archive modes. The storage tests cover project-source discovery, `du`-based scanning, ProjectData HTML conversion, Plotly report generation, the strictly read-only Live particle scan, and Live particle cleanup in `--dry-run` mode only. No test intentionally deletes particle data, and no real CryoSPARC installation is required.

The test harness explicitly uses UTF-8 for subprocess output and its own console streams so Unicode characters emitted by the tools do not fail on Windows systems whose default Python text encoding is `cp1252`. The display/conversion tests require the repository dependencies to be installed.
