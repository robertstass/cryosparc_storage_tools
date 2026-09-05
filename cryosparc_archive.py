#!/usr/bin/env python3
"""
cryosparc_archive.py

Archive a whole CryoSPARC project's metadata (cheaply, for every job) plus
full data for specific jobs you name — final maps, FSC curves, event-log
plot images — and produce a single offline-viewable HTML overview of how
every job in the project connects, split by workspace.

THIS SCRIPT ONLY EVER READS FROM --project-dir AND WRITES TO --outdir.
There is no delete/remove/rename operation anywhere in it, and no file
under --project-dir is ever opened for writing — every write/copy target
traces back to --outdir. This is archival only; it cannot modify or
remove your CryoSPARC project.

------------------------------------------------------------------------
HOW THIS WORKS
------------------------------------------------------------------------
1. METADATA MIRROR (always, for every job/session — cheap):
   Every J<N> directory's job.json and every S<N> (Live session)
   directory's exposures.bson gets copied into outdir/J<N>/ and
   outdir/S<N>/ respectively, preserving the project's own directory
   names. job.json is small; exposures.bson can be large if it embeds
   thumbnail previews — its size is printed so this isn't a silent
   surprise (see --skip-live-bson-copy to skip the raw copy and keep
   just the derived curation counts instead). project.json,
   workspaces.json, and job_manifest.json (confirmed present at the
   project root) are copied to outdir's top level verbatim. Jobs not
   selected for deep archiving are mirrored under outdir/other_jobs/<uid>/
   rather than cluttering the top level — deep-archived jobs stay
   directly under outdir/<uid>/ so they're easy to find.

2. LIGHTWEIGHT STATS (always, for every job — cheap):
   For every job, this tries to determine (using memory-mapped reads —
   no large file is ever fully loaded just to get a count):
     - job type / category, title, status, creation time
     - workspace membership (job.json's own 'workspace_uids'; titles for
       these come from workspaces.json at the project root)
     - direct parents/children (job.json's own 'parents'/'children' —
       CryoSPARC records these directly, no connection-graph guessing
       needed)
     - particle / micrograph counts on its own output, if it has one
     - accepted/rejected counts, if it looks like a curation-style job
     - the full effective parameter set the job was configured with
       ('params_base' defaults merged with 'params_spec' overrides,
       generically for any job type — this is literally what CryoSPARC's
       own "Parameters" panel shows)
     - for Live-processing jobs specifically: their own params are
       minimal, so the 'Session UID' parameter is used to pull the real
       acquisition parameters from that Live session's own
       exposures.bson instead (per-exposure groups.exposure.mscope_params
       — verified constant across every exposure in a real session).
   This is what populates the HTML overview and lets you trace particle
   counts through the whole project without deep-archiving everything.

3. DEEP ARCHIVE (only for jobs you name with --deep JOB_UID[:mode]):
   Modes, each a superset of the previous:
     bare_minimum    nothing beyond the metadata mirror above.
     minimalist      + events.bson/job.log + whatever plot images can be
                     located from them (see part 4 below) — no
                     underlying data, just visual snapshots.
     last_iteration  + final-iteration output files, detected generically
                     by filename pattern ('<job_uid>_<NNN>_<rest>', only
                     the highest NNN kept) — works for refinement
                     volumes/masks, 2D class-average stacks, or anything
                     else that follows this convention, without hardcoded
                     per-job-type logic — plus FSC curves (flat files if
                     present, else reconstructed numerically from
                     job.json; validated byte-for-byte against a real
                     CryoSPARC export).
     all             the entire job directory, wholesale (per-file size
                     cap still applies). Refused for job categories whose
                     directories are typically huge — motion correction,
                     particle extraction, Live sessions — since that
                     defeats the point of a minimal archive; falls back
                     to that category's default mode instead.
   Every category has a sensible default mode (2D classification:
   minimalist; refinement/classification-type jobs: last_iteration;
   uncategorized/unknown job types: last_iteration; most everything else:
   minimalist) so you only need the job ID normally — override per-job
   with e.g. --deep J13:last_iteration to also grab a 2D job's class-
   average stack file, not just its snapshot image.
   Naming a local refinement job automatically also deep-archives its
   preceding global refinement job at that job's own default mode
   (skipped if you already named it explicitly).

4. EVENT LOG IMAGES (part of minimalist mode and above):
   CryoSPARC's event log (events.bson) records every plot it ever
   rendered for a job (2D class views, FSC plots, volume slices, viewing-
   direction plots, noise-model plots, ...) as a 'fileid' pointing into a
   'gridfs_data' directory holding raw MongoDB storage-engine files, not
   one file per blob. Checking a real MATCHED events.bson + gridfs_data
   pair (rather than mismatched samples from different jobs) revealed
   that each fileid appears as a literal ASCII string a short, bounded
   distance before its actual image data — consistent with being a BSON
   document field preceding the binary payload in the same record. This
   is now used for exact, content-addressed lookup — not a guess —
   verified against two full matched pairs (65/65 and 6/6 images located,
   every one a byte-valid PNG whose content genuinely matched its claimed
   name, zero duplicates). Results are restricted to the job's FINAL
   iteration (plus one-off setup/diagnostic events with no iteration
   number) — CryoSPARC re-renders most of these every iteration, and
   gridfs_data holds the job's entire history. Any fileid genuinely not
   found in the available gridfs_data file(s) (e.g. rotated out of the
   store) is reported as not located rather than guessed at.

5. OVERVIEW.HTML:
   A self-contained, zero-network-required HTML file — a "card view"
   (deliberately simpler than trying to recreate CryoSPARC's own tree
   view): every job as a card, sorted by creation time, filterable by
   workspace (shown by their real title from workspaces.json, not just
   "W1"). Clicking a card highlights its DIRECT parents (red) and DIRECT
   children (green) only — matching CryoSPARC's own convention — while
   the full ancestor/descendant counts are still shown as text in the
   detail panel. Deep-archived jobs are visually marked with their mode.

   Multiple independent import/Live chains in one project, or jobs that
   later merge two separate chains together (e.g. combining particles
   from two Live sessions), need no special handling here — the graph is
   built purely from each job's own recorded parents/children, so
   whatever shape that data actually has is what gets shown.

------------------------------------------------------------------------
LIMITATIONS
------------------------------------------------------------------------
- "Particles in" for a job with multiple particle-producing parents is
  shown as each parent's own count, not a merged/deduplicated total —
  CryoSPARC itself doesn't cleanly attribute this either.
- Resolution/FSC reconstruction depends on job.json containing the
  fsc_info fields CryoSPARC currently uses; a very different version
  might not have them (this will just say so rather than guessing).
- Event images depend on gridfs_data actually containing the referenced
  blob — a fileid genuinely rotated out of the store (or a project where
  gridfs_data isn't organized per-job the way it was in the two verified
  examples) will just be reported as not located.

------------------------------------------------------------------------
USAGE
------------------------------------------------------------------------
    python cryosparc_archive.py \
        --project-dir /path/to/CS-myproject \
        --outdir /path/to/archive \
        --deep J50 J23:last_iteration \
        [--dry-run]

    pip install numpy mrcfile --user
    pip install pymongo --user       # only needed to read events.bson / Live exposures.bson
    pip install cryosparc-tools --user   # only needed as a .cs fallback, see load_cs
"""

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("ERROR: numpy is required (pip install numpy). Exiting.", file=sys.stderr)
    sys.exit(1)

try:
    import mrcfile
except ImportError:
    mrcfile = None

try:
    from cryosparc.dataset import Dataset as CSDataset  # cryosparc-tools, optional
except ImportError:
    CSDataset = None

try:
    import bson  # from pymongo; optional, only needed for Live exposures.bson / events.bson
except ImportError:
    bson = None


def eprint(*a, **k):
    print(*a, file=sys.stderr, **k)


# ==========================================================================
# Constants
# ==========================================================================

FIELD_CANDIDATES = {
    "particle_pixel_size":  ["blob/psize_A"],
    "raw_pixel_size":       ["movie_blob/psize_A", "micrograph_blob/psize_A", "rawdata_blob/psize_A"],
    "accel_kv":             ["mscope_params/accel_kv", "ctf/accel_kv"],
    "cs_mm":                ["mscope_params/cs_mm", "ctf/cs_mm"],
    "amp_contrast":         ["mscope_params/amp_contrast", "ctf/amp_contrast"],
    "total_dose_e_per_A2":  ["mscope_params/total_dose_e_per_A2", "mscope_params/total_dose"],
    "class2d_assignment":   ["alignments2D/class"],
}

JOB_TYPE_KEYWORDS = {
    "local_refine":  ["local_refine"],
    "global_refine": ["nonuniform_refine", "new_nonuniform_refine", "homo_refine", "hetero_refine"],
    "class2d":       ["class_2d", "class2d"],
    "class3d":       ["class_3d", "class3d", "heterogeneous_refine", "abinit"],
    "motion":        ["motion_correction", "patch_motion", "rigid_motion", "full_frame_motion"],
    "ctf":           ["ctf_estimation", "patch_ctf", "gctf"],
    "extract":       ["extract_micrographs", "downsample_micrographs", "extract"],
    "picking":       ["blob_picker", "template_picker", "manual_picker", "topaz", "picker"],
    "curation":      ["curate_exposures", "curate"],
    "inspect_picks": ["inspect_picks", "inspect_particle_picks"],
    "sharpen":       ["sharpen", "sharpening"],
    "deepemhancer":  ["deepemhancer", "deep_emhancer"],
    "orientation_diagnostics": ["orientation_diagnostics"],
    "local_resolution": ["local_resolution"],
    "local_filter":  ["local_filter"],
    "live":          ["live"],
    "import":        ["import_movies", "import_micrographs", "import_particles", "import_volumes"],
}

XML_MASK_LABELS = {
    "fsc_sphericalmask": ("spherical", "Spherical Mask"),
    "fsc_loosemask":     ("loose", "Loose Mask"),
    "fsc_tightmask":     ("tight", "Tight Mask"),
    "fsc_noisesub":      ("corrected", "Corrected Mask"),
}
PRE_TIGHTEN_COLUMNS = ["fsc_nomask", "fsc_sphericalmask", "fsc_loosemask", "fsc_tightmask"]
POST_TIGHTEN_COLUMNS = ["fsc_nomask", "fsc_loosemask", "fsc_tightmask",
                         "fsc_noisesub_raw", "fsc_noisesub_true", "fsc_noisesub"]

REJECT_FLAG_FIELDS = ["manual_reject", "threshold_reject", "failed", "deleted"]

GENERIC_COPY_MAX_BYTES = 500 * 1024 * 1024  # per-file cap for the "all" mode's wholesale directory copy

# --------------------------------------------------------------------------
# Deep-archive modes.
#
# CryoSPARC has a lot of job types, and they change across versions — hence
# modes rather than trying to maintain bespoke handling per job type:
#   bare_minimum   just job.json (this happens for EVERY job regardless of
#                  mode, as part of the metadata mirror — this mode name
#                  exists so a job can be explicitly pinned to "nothing
#                  extra", e.g. to override an auto-added dependency)
#   minimalist     + events.bson/job.log + whatever images can be pulled
#                  from them. No underlying data, just the visual/plot
#                  snapshots CryoSPARC itself already rendered.
#   last_iteration + final-iteration output files (volumes/masks/2D class
#                  stacks/whatever else follows the per-iteration naming
#                  convention) + FSC curves.
#   all            the entire job directory, wholesale (per-file size cap
#                  still applies — see GENERIC_COPY_MAX_BYTES).
#
# "all" is refused for categories whose job directories are typically huge
# and not what anyone archiving "just the important bits" wants (raw
# motion-corrected micrographs, full extracted particle stacks, Live
# session data) — ALL_MODE_DISALLOWED_CATEGORIES below.
# --------------------------------------------------------------------------
VALID_MODES = {"bare_minimum", "minimalist", "last_iteration", "all"}

CATEGORY_DEFAULT_MODE = {
    "class2d":       "minimalist",
    "local_refine":  "last_iteration",
    "global_refine": "last_iteration",
    "class3d":       "last_iteration",
    "sharpen":       "all",
    "deepemhancer":  "all",
    "orientation_diagnostics": "all",
    "local_resolution": "all",
    "local_filter":  "all",
    "motion":        "minimalist",
    "ctf":           "minimalist",
    "extract":       "minimalist",
    "picking":       "minimalist",
    "curation":      "minimalist",
    "inspect_picks": "minimalist",
    "live":          "minimalist",
    "import":        "minimalist",
    None:            "last_iteration",  # uncategorized/unknown job type ("other")
}

ALL_MODE_DISALLOWED_CATEGORIES = {"motion", "extract", "live"}


# ==========================================================================
# Basic job.json / .cs reading
# ==========================================================================

def load_job_json(job_dir: Path):
    for candidate in (job_dir / "job.json", job_dir / f"{job_dir.name}.json"):
        if candidate.exists():
            try:
                with open(candidate) as f:
                    return json.load(f), candidate
            except Exception as e:
                eprint(f"  [!] Could not parse {candidate}: {e}")
    return None, None


def get_parents(job_json: dict, this_uid: str):
    """CryoSPARC records direct parent job UIDs on the job itself — no
    need to guess from connection structures. Falls back to a generic
    'anything shaped like a J-number' scan of the whole document for
    versions where the field might be named differently or absent."""
    p = job_json.get("parents")
    if isinstance(p, list) and all(isinstance(x, str) for x in p):
        return list(p)
    return sorted(_referenced_job_uids_fallback(job_json, this_uid))


def get_children(job_json: dict):
    c = job_json.get("children")
    if isinstance(c, list) and all(isinstance(x, str) for x in c):
        return list(c)
    return []


def _referenced_job_uids_fallback(obj, this_uid):
    found = set()

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for item in o:
                walk(item)
        elif isinstance(o, str):
            if re.fullmatch(r"J\d+", o) and o != this_uid:
                found.add(o)

    walk(obj)
    return found


def parse_created_at(job_json: dict):
    """CryoSPARC stores dates as MongoDB extended JSON: {'$date': 'ISO8601'}."""
    v = job_json.get("created_at")
    if isinstance(v, dict) and "$date" in v:
        return v["$date"]
    if isinstance(v, str):
        return v
    return None


def classify_job_type(job_type: str):
    job_type = (job_type or "").lower()
    for category, keywords in JOB_TYPE_KEYWORDS.items():
        if any(k in job_type for k in keywords):
            return category
    return None


def load_cs(path: Path):
    try:
        arr = np.load(str(path), allow_pickle=True)
        if arr.dtype == object and arr.shape == ():
            arr = arr.item()
        return arr
    except Exception as e_numpy:
        if CSDataset is not None:
            try:
                return CSDataset.load(str(path))
            except Exception as e_cs:
                raise RuntimeError(f"Could not load {path} with numpy ({e_numpy}) or cryosparc-tools ({e_cs})")
        raise RuntimeError(
            f"Could not load {path} with numpy ({e_numpy}), and cryosparc-tools isn't "
            f"installed to try the newer format. Try: pip install cryosparc-tools"
        )


def load_cs_mmap(path: Path):
    """Cheap variant for the project-wide scan: memory-maps rather than
    loading data, so getting a row count from a huge .cs file is fast and
    doesn't need the data to fit in RAM. Falls back to a full load only if
    mmap isn't possible (e.g. an object-dtype/pickled array)."""
    try:
        arr = np.load(str(path), mmap_mode="r", allow_pickle=False)
        return arr
    except Exception:
        try:
            return load_cs(path)
        except Exception:
            return None


def dataset_len(ds):
    try:
        return len(ds)
    except TypeError:
        return ds.shape[0]


def dataset_field_names(ds):
    if hasattr(ds, "fields"):
        try:
            return list(ds.fields())
        except Exception:
            pass
    if hasattr(ds, "descr"):
        try:
            return [f[0] for f in ds.descr()]
        except Exception:
            pass
    names = getattr(ds, "dtype", None)
    if names is not None and names.names:
        return list(names.names)
    return []


def get_field(ds, stat_name, index=0):
    candidates = FIELD_CANDIDATES.get(stat_name, [stat_name])
    for name in candidates:
        try:
            col = ds[name]
            val = col[index]
            return val.item() if hasattr(val, "item") else val
        except Exception:
            continue
    return None


def get_column(ds, stat_name):
    candidates = FIELD_CANDIDATES.get(stat_name, [stat_name])
    for name in candidates:
        try:
            return ds[name]
        except Exception:
            continue
    available = dataset_field_names(ds)
    eprint(f"  [!] Could not find any of {candidates}.")
    if available:
        eprint(f"      Available fields: {available}")
    return None


def find_particle_cs(job_dir: Path):
    """Only matches files with 'particle' actually in the name — no blind
    fallback to 'the only .cs file in the dir', since this is now called
    generically across every job type in the project and a wrong guess
    (e.g. picking up a picks or mask .cs as if it were particles) would
    silently misreport counts."""
    cs_files = [f for f in job_dir.glob("*.cs")
                if "particle" in f.name.lower()
                and "accepted" not in f.name.lower() and "rejected" not in f.name.lower()]
    if not cs_files:
        return None
    return max(cs_files, key=lambda f: f.stat().st_size)


def find_exposure_cs(job_dir: Path):
    """Same reasoning as find_particle_cs: require an actual 'micrograph'
    or 'exposure' in the filename, no blind fallback."""
    cs_files = [f for f in job_dir.glob("*.cs")
                if any(k in f.name.lower() for k in ("micrograph", "exposure"))
                and "accepted" not in f.name.lower() and "rejected" not in f.name.lower()]
    if not cs_files:
        return None
    return max(cs_files, key=lambda f: f.stat().st_size)


def find_accept_reject_counts(job_dir: Path):
    cs_files = list(job_dir.glob("*.cs"))
    accepted = [f for f in cs_files if "accept" in f.name.lower()]
    rejected = [f for f in cs_files if "reject" in f.name.lower()]
    result = {"accepted": None, "rejected": None}
    if accepted:
        f = max(accepted, key=lambda x: x.stat().st_size)
        arr = load_cs_mmap(f)
        if arr is not None:
            result["accepted"] = dataset_len(arr)
    if rejected:
        f = max(rejected, key=lambda x: x.stat().st_size)
        arr = load_cs_mmap(f)
        if arr is not None:
            result["rejected"] = dataset_len(arr)
    return result


# ==========================================================================
# Refinement output files (iteration-aware) + FSC reconstruction
#
# Verified against a real CryoSPARC-exported job: reproduces the .txt
# export byte-for-byte and the .xml exports to ~19 significant figures
# (see module history / prior validation — job.json's fsc_info /
# fsc_info_autotight / fsc_info_best hold the full curves; the frequency
# axis is (i+0.5)/(box_size_px * pixel_size_A) using the PARTICLE pixel
# size, not the MRC header's slightly-differently-rounded one).
# ==========================================================================

# ==========================================================================
# "last_iteration" mode: generic final-iteration file detection + FSC
# reconstruction
#
# Deliberately NOT bucketed by job type (no hardcoded "volume"/"mask"/
# "half map" keyword matching) — CryoSPARC has too many job types, and
# they change across versions, to maintain a mapping per type. Instead:
# any file named '<job_uid>_<NNN>_<rest>' is treated as a per-iteration
# output, grouped by NNN, and only the highest NNN group is kept. This
# works for refinement volumes/masks, 2D class-average stacks, or
# whatever any other job type writes per-iteration, uniformly.
#
# FSC curve files are a separate special case: CryoSPARC names them
# '<job_uid>_fsc_iteration_<N>...' rather than the '<job_uid>_<NNN>_...'
# pattern above, so they're found by a dedicated glob rather than the
# generic iteration-number grouping.
#
# Verified against a real CryoSPARC-exported job: FSC reconstruction
# reproduces the .txt export byte-for-byte and the .xml exports to ~19
# significant figures (job.json's fsc_info / fsc_info_autotight /
# fsc_info_best hold the full curves; the frequency axis is
# (i+0.5)/(box_size_px * pixel_size_A) using the PARTICLE pixel size,
# not the MRC header's slightly-differently-rounded one).
# ==========================================================================

def find_final_iteration_files(job_dir: Path, job_uid: str):
    """Generic: group files named '<job_uid>_<NNN>_<rest>' by NNN, return
    only the files from the highest NNN found (plus that number itself),
    and how many earlier-iteration files were skipped. No job-type-specific
    logic — this is deliberately naive about what 'rest' actually is."""
    iter_re = re.compile(rf"^{re.escape(job_uid)}_(\d+)_(.+)$")
    numbered = []
    for f in job_dir.iterdir():
        if not f.is_file():
            continue
        m = iter_re.match(f.stem)
        if m:
            numbered.append((int(m.group(1)), f))
    if not numbered:
        return None, [], 0
    final_iter = max(n for n, _ in numbered)
    final_files = [f for n, f in numbered if n == final_iter]
    skipped = len(numbered) - len(final_files)
    return final_iter, final_files, skipped


def find_flat_fsc_files(job_dir: Path, job_uid: str):
    """FSC curve/plot files use their own naming convention
    ('<job_uid>_fsc_iteration_<N>...'), not the generic '<job_uid>_<NNN>_'
    pattern, so they need a dedicated glob rather than the generic
    iteration grouping above. Bug fix: the glob below also matches
    numbered per-iteration files that happen to have 'fsc' in the name
    (e.g. 'J48_004_volume_mask_fsc.cs') — those are ALREADY correctly
    filtered to the final iteration by find_final_iteration_files, so
    they're explicitly excluded here to avoid re-adding every earlier
    iteration's copy right back in."""
    iter_re = re.compile(rf"^{re.escape(job_uid)}_(\d+)_(.+)$")
    files = []
    seen = set()
    for pattern in ("*fsc*.png", "*fsc*.xml", "*fsc*.cs", "*fsc*.txt"):
        for f in job_dir.glob(pattern):
            if iter_re.match(f.stem):
                continue  # handled (and iteration-filtered) by find_final_iteration_files instead
            if f not in seen:
                seen.add(f)
                files.append(f)
    return files


def has_flat_fsc_files(job_dir: Path, job_uid: str):
    return len(find_flat_fsc_files(job_dir, job_uid)) > 0


def copy_last_iteration_files(job_dir: Path, dest_dir: Path, job_uid: str, dry_run: bool):
    final_iter, final_files, skipped = find_final_iteration_files(job_dir, job_uid)
    fsc_files = find_flat_fsc_files(job_dir, job_uid)
    if final_iter is not None:
        print(f"  final iteration detected: {final_iter} (skipped {skipped} file(s) from earlier iterations)")
    if fsc_files:
        print(f"  found {len(fsc_files)} flat FSC-related file(s)")
    if not dry_run and (final_files or fsc_files):
        dest_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for f in final_files + fsc_files:
        print(f"    copying {f.name}")
        if not dry_run:
            shutil.copy2(f, dest_dir / f.name)
        copied.append(f.name)
    return {"final_iteration": final_iter, "copied_files": copied, "had_flat_fsc": bool(fsc_files)}


def find_fsc_stats_groups(job_json: dict):
    groups = []
    for idx, g in enumerate(job_json.get("output_result_groups", []) or []):
        stats = g.get("latest_summary_stats") or {}
        if any(k.startswith("fsc_info") for k in stats.keys()):
            groups.append((g.get("name", f"group_{idx}"), stats))
    return groups


def fsc_resolution_A(freqs, curve, threshold=0.143):
    for i in range(1, len(curve)):
        if curve[i - 1] >= threshold and curve[i] < threshold:
            f0, f1 = freqs[i - 1], freqs[i]
            y0, y1 = curve[i - 1], curve[i]
            frac = (threshold - y0) / (y1 - y0)
            fc = f0 + frac * (f1 - f0)
            return (1.0 / fc) if fc > 0 else None
    return None


def render_fsc_txt(columns, stats):
    cols = [c for c in columns if c in stats]
    if not cols:
        return None
    n = len(stats[cols[0]])
    lines = ["\t".join(["wave_number"] + cols)]
    for i in range(n):
        row = [f"{i + 0.5:.6f}"] + [f"{stats[c][i]:.6f}" for c in cols]
        lines.append("\t".join(row))
    return "\n".join(lines) + "\n"


def render_fsc_xml(title, freqs, values):
    parts = [f'<fsc title="cryoSPARC GSFSC {title}" xaxis="Resolution (A-1)" '
             f'yaxis="Correlation Coefficient">']
    for x, y in zip(freqs, values):
        yv = str(np.float32(y))
        parts.append(f'<coordinate><x>{x!r}</x><y>{yv}</y></coordinate>')
    parts.append('</fsc>')
    return "".join(parts)


def find_log_resolution_lines(job_dir: Path):
    lines = []
    for pattern in ("*.log", "*.txt"):
        for f in job_dir.glob(pattern):
            try:
                with open(f, errors="ignore") as fh:
                    for line in fh:
                        if re.search(r"resolution", line, re.I):
                            lines.append((f.name, line.strip()))
            except Exception:
                pass
    return lines


def reconstruct_fsc_exports(job_uid, job_json, dest_dir, final_iter, pixel_size_A, box_size_px, dry_run):
    """Two schemas have been seen in the wild for this data:
      - OLDER: separate 'fsc_info' (pre-tightening) and 'fsc_info_autotight'
        /'fsc_info_best' (post-tightening) dicts; keys fsc_nomask/
        fsc_sphericalmask/fsc_loosemask/fsc_tightmask/fsc_noisesub(_raw/_true).
        This is the schema validated byte-for-byte against a real
        CryoSPARC GUI export — the code path for it is unchanged from
        that validation.
      - NEWER (seen 2026): everything in a single 'fsc_info' dict (no
        separate post-tightening dict), the tight-mask curve renamed to
        'fsc_resmask', post-tightening curves named 'fsc_autotight'/
        'fsc_autotight_noisesub'/'fsc_autotight_noisesub_raw', plus extra
        scalar metadata (N, psize, radwn_*) mixed into the same dict
        (which is what crashed n_shells detection — that's fixed by only
        ever measuring list-valued entries). This path is a best-effort
        adaptation using the SAME output format as the validated path,
        but has NOT been verified against a real export from this newer
        version — flagged as such in the summary output.
    """
    result = {"groups": {}}
    groups = find_fsc_stats_groups(job_json)
    if not groups or pixel_size_A is None or box_size_px is None or final_iter is None:
        return None
    multi = len(groups) > 1
    for gname, stats in groups:
        n_shells = None
        source_key = None
        for key in ("fsc_info", "fsc_info_autotight", "fsc_info_best"):
            if key in stats and isinstance(stats[key], dict):
                for v in stats[key].values():
                    if isinstance(v, list) and v:
                        n_shells = len(v)
                        source_key = key
                        break
            if n_shells is not None:
                break
        if n_shells is None:
            eprint(f"  [!] [fsc] No usable FSC curve data found in job.json for group '{gname}' — skipping.")
            continue
        freqs = [(i + 0.5) / (box_size_px * pixel_size_A) for i in range(n_shells)]
        suffix = f"_{gname}" if multi else ""
        base = f"{job_uid}_fsc_iteration_{final_iter:03d}{suffix}"
        group_result = {"resolutions_A": {}}

        has_legacy_tight = "fsc_tightmask" in stats.get("fsc_info", {})
        # NOTE: a sibling 'fsc_info_best'/'fsc_info_autotight' key existing is NOT sufficient
        # evidence of the legacy schema on its own — the newer schema was found to also carry a
        # (redundant) 'fsc_info_best' sibling, just using the SAME new key names inside it as
        # 'fsc_info' rather than legacy ones. Only the presence of 'fsc_tightmask' in the primary
        # fsc_info dict reliably distinguishes the two, so that alone decides the whole branch
        # (mixing legacy/new per sub-section previously caused a silent partial-output bug).

        if has_legacy_tight:
            # --- validated path, byte-for-byte verified against a real GUI export ---
            if "fsc_info" in stats:
                txt = render_fsc_txt(PRE_TIGHTEN_COLUMNS, stats["fsc_info"])
                if txt:
                    path = dest_dir / f"{base}.txt"
                    print(f"  [fsc] reconstructing {path.name}")
                    if not dry_run:
                        path.write_text(txt, encoding="utf-8")
                    for key in PRE_TIGHTEN_COLUMNS:
                        if key in stats["fsc_info"]:
                            res = fsc_resolution_A(freqs, stats["fsc_info"][key])
                            group_result["resolutions_A"][key] = res
                for key, (token, title) in XML_MASK_LABELS.items():
                    if key in stats["fsc_info"] and key != "fsc_noisesub":
                        xml = render_fsc_xml(title, freqs, stats["fsc_info"][key])
                        path = dest_dir / f"{base}_{token}_mask.xml"
                        print(f"  [fsc] reconstructing {path.name}")
                        if not dry_run:
                            path.write_text(xml, encoding="utf-8")

            post_key = "fsc_info_autotight" if "fsc_info_autotight" in stats else \
                       ("fsc_info_best" if "fsc_info_best" in stats else None)
            if post_key:
                post_stats = stats[post_key]
                txt = render_fsc_txt(POST_TIGHTEN_COLUMNS, post_stats)
                if txt:
                    path = dest_dir / f"{base}_after_fsc_mask_auto_tightening.txt"
                    print(f"  [fsc] reconstructing {path.name}")
                    if not dry_run:
                        path.write_text(txt, encoding="utf-8")
                    for key in POST_TIGHTEN_COLUMNS:
                        if key in post_stats:
                            res = fsc_resolution_A(freqs, post_stats[key])
                            group_result["resolutions_A"][f"{key}_post_tighten"] = res
                for key, (token, title) in XML_MASK_LABELS.items():
                    if key in post_stats:
                        xml = render_fsc_xml(title, freqs, post_stats[key])
                        path = dest_dir / f"{base}_after_fsc_mask_auto_tightening_{token}_mask.xml"
                        print(f"  [fsc] reconstructing {path.name}")
                        if not dry_run:
                            path.write_text(xml, encoding="utf-8")
                if "fsc_noisesub" in post_stats:
                    group_result["headline_resolution_A"] = fsc_resolution_A(freqs, post_stats["fsc_noisesub"])
                elif "fsc_tightmask" in stats.get("fsc_info", {}):
                    group_result["headline_resolution_A"] = fsc_resolution_A(freqs, stats["fsc_info"]["fsc_tightmask"])

        else:
            # --- newer schema, best-effort, NOT byte-verified against a real export ---
            print(f"  [!] [fsc] '{gname}': newer job.json schema detected (fsc_resmask/fsc_autotight* "
                  f"keys) — reconstructing best-effort; this format hasn't been verified against a "
                  f"real GUI export the way the older schema has.")
            s = stats[source_key]
            pre_cols = {"fsc_nomask": s.get("fsc_nomask"), "fsc_sphericalmask": s.get("fsc_sphericalmask"),
                        "fsc_loosemask": s.get("fsc_loosemask"), "fsc_tightmask": s.get("fsc_resmask")}
            pre_cols = {k: v for k, v in pre_cols.items() if isinstance(v, list)}
            txt = render_fsc_txt(list(pre_cols.keys()), pre_cols)
            if txt:
                path = dest_dir / f"{base}.txt"
                print(f"  [fsc] reconstructing {path.name}")
                if not dry_run:
                    path.write_text(txt, encoding="utf-8")
                for key, curve in pre_cols.items():
                    group_result["resolutions_A"][key] = fsc_resolution_A(freqs, curve)
            xml_map = {"fsc_sphericalmask": ("spherical", "Spherical Mask"),
                       "fsc_loosemask": ("loose", "Loose Mask"),
                       "fsc_resmask": ("tight", "Tight Mask")}
            for key, (token, title) in xml_map.items():
                if isinstance(s.get(key), list):
                    xml = render_fsc_xml(title, freqs, s[key])
                    path = dest_dir / f"{base}_{token}_mask.xml"
                    print(f"  [fsc] reconstructing {path.name}")
                    if not dry_run:
                        path.write_text(xml, encoding="utf-8")

            post_cols = {"fsc_nomask": s.get("fsc_nomask"), "fsc_loosemask": s.get("fsc_loosemask"),
                         "fsc_tightmask": s.get("fsc_resmask"),
                         "fsc_noisesub_raw": s.get("fsc_autotight_noisesub_raw"),
                         "fsc_noisesub": s.get("fsc_autotight_noisesub")}
            post_cols = {k: v for k, v in post_cols.items() if isinstance(v, list)}
            if "fsc_noisesub" in post_cols:
                txt = render_fsc_txt(list(post_cols.keys()), post_cols)
                if txt:
                    path = dest_dir / f"{base}_after_fsc_mask_auto_tightening.txt"
                    print(f"  [fsc] reconstructing {path.name}")
                    if not dry_run:
                        path.write_text(txt, encoding="utf-8")
                    for key, curve in post_cols.items():
                        group_result["resolutions_A"][f"{key}_post_tighten"] = fsc_resolution_A(freqs, curve)
                xml = render_fsc_xml("Corrected Mask", freqs, post_cols["fsc_noisesub"])
                path = dest_dir / f"{base}_after_fsc_mask_auto_tightening_corrected_mask.xml"
                print(f"  [fsc] reconstructing {path.name}")
                if not dry_run:
                    path.write_text(xml, encoding="utf-8")
                group_result["headline_resolution_A"] = fsc_resolution_A(freqs, post_cols["fsc_noisesub"])
            elif "fsc_tightmask" in post_cols:
                group_result["headline_resolution_A"] = fsc_resolution_A(freqs, post_cols["fsc_tightmask"])

            # the newer schema also carries CryoSPARC's OWN precomputed resolution values
            # (radwn_*_A) directly — surfaced alongside our own interpolated ones as a cross-check,
            # since they may use a slightly different convention than fsc_resolution_A's 0.143 threshold
            precomputed = {k: v for k, v in s.items() if k.startswith("radwn_") and k.endswith("_A")}
            if precomputed:
                group_result["precomputed_resolutions_A_from_job_json"] = precomputed

        result["groups"][gname] = group_result
    return result


def mrc_box_and_pixel_size(mrc_path: Path):
    if mrcfile is None:
        return None, None
    with mrcfile.open(str(mrc_path), permissive=True, header_only=False) as mrc:
        box = int(mrc.header.nx)
        vs = mrc.voxel_size
        pixel_size = float(vs.x) if vs.x else None
    return box, pixel_size


def deep_archive_last_iteration(job_dir: Path, job_uid: str, job_json: dict, dest_dir: Path,
                                 event_result, dry_run: bool):
    """'last_iteration' mode: whatever the job wrote for its final
    iteration (volumes, masks, 2D class stacks, or anything else that
    follows the naming convention) + FSC curves + basic stats.

    FSC curve source priority (most trustworthy first):
      1. flat .txt/.xml files already sitting in the job directory
      2. the actual .txt/.xml CryoSPARC itself wrote, recovered from
         events.bson + gridfs_data (see extract_event_images/
         extract_blob_by_fileid) — these are the literal files, not a
         reconstruction, and are already saved under event_images/ by
         the time this runs
      3. only if neither of the above produced real FSC text/xml:
         numerically reconstructed from job.json (reconstruct_fsc_exports)
         — flagged with an explicit warning, since this is a fallback,
         not the authoritative source, and known to differ in at least
         one respect (job.json sanitizes NaN/Inf before serializing)."""
    copy_result = copy_last_iteration_files(job_dir, dest_dir, job_uid, dry_run)

    stats = {}
    particle_cs = find_particle_cs(job_dir)
    if particle_cs:
        ds = load_cs_mmap(particle_cs)
        if ds is not None:
            stats["particle_count"] = dataset_len(ds)
            stats["pixel_size_A"] = get_field(ds, "particle_pixel_size")
            print(f"  particles: {stats['particle_count']}, pixel size: {stats['pixel_size_A']} A/px")

    mrc_files = [f for f in copy_result["copied_files"] if f.lower().endswith(".mrc")]
    if mrc_files and mrcfile is not None:
        box, pixel_size_hdr = mrc_box_and_pixel_size(job_dir / mrc_files[0])
        stats["box_size_px"] = box
        stats["pixel_size_A_from_mrc_header"] = pixel_size_hdr

    have_real_fsc_events = bool(event_result) and event_result.get("located_by_type", {}).get("xml", 0) > 0

    if copy_result["had_flat_fsc"]:
        print("  flat FSC file(s) already exist in the job directory — copied above, not reconstructing.")
    elif have_real_fsc_events:
        n_xml = event_result["located_by_type"].get("xml", 0)
        n_txt = event_result["located_by_type"].get("txt", 0)
        print(f"  the actual FSC .xml/.txt file(s) CryoSPARC wrote were recovered from events.bson "
              f"(see event_images/: {n_xml} xml, {n_txt} txt) — not reconstructing from job.json.")
    else:
        eprint(f"  [!] No flat FSC files on disk and none recoverable from events.bson/gridfs_data — "
               f"FALLING BACK to reconstructing FSC data numerically from job.json. This is a "
               f"best-effort reconstruction, not the original file: it's been validated against a "
               f"real CryoSPARC export for one job.json schema, but job.json is also known to "
               f"sanitize NaN/Inf values before serializing (affects one diagnostic column, not "
               f"the plotted curves), and a newer schema variant is supported best-effort only "
               f"(see reconstruct_fsc_exports). Treat these particular files with more caution "
               f"than ones recovered directly.")
        px = stats.get("pixel_size_A") or stats.get("pixel_size_A_from_mrc_header")
        box = stats.get("box_size_px")
        fsc = reconstruct_fsc_exports(job_uid, job_json, dest_dir, copy_result["final_iteration"], px, box, dry_run)
        if fsc:
            stats["fsc_reconstructed"] = fsc
            stats["fsc_reconstructed_fallback_warning"] = (
                "Reconstructed numerically from job.json — the real GUI-exported files could not "
                "be located. See deep_archive_last_iteration's docstring for known caveats."
            )
            for gname, gres in fsc["groups"].items():
                headline = gres.get("headline_resolution_A")
                if headline:
                    print(f"  headline GSFSC resolution ({gname}): {headline:.2f} A")
        else:
            log_lines = find_log_resolution_lines(job_dir)
            if log_lines:
                stats["resolution_lines_from_logs"] = log_lines

    return {"copied_files": copy_result, "stats": stats}


# ==========================================================================
# 2D classification: per-class particle counts
#
# The class-average IMAGE itself now comes from the generic event-image
# extraction (deep_archive_events_and_log) — CryoSPARC's own rendering,
# not a recreation, wherever the underlying gridfs storage can be read.
# The custom montage-rendering this module used to do has been dropped:
# it's redundant now, and the real thing is more faithful anyway. This
# is only for the class_averages.mrc stack in 'last_iteration'/'all'
# mode (find_final_iteration_files already picks it up generically,
# since it follows the same '<job_uid>_<NNN>_...' naming convention as
# refinement volumes) — this function just adds the numeric per-class
# particle breakdown, which no image (real or recreated) gives you back.
# ==========================================================================

def compute_class2d_counts(job_dir: Path, job_uid: str, dest_dir: Path, dry_run: bool):
    particle_cs = find_particle_cs(job_dir)
    class_counts = {}
    if particle_cs:
        ds = load_cs(particle_cs)
        col = get_column(ds, "class2d_assignment")
        if col is not None:
            class_counts = dict(Counter(int(v) for v in col))
            print(f"  particles: {sum(class_counts.values())} across {len(class_counts)} classes")

    if not dry_run and class_counts:
        dest_dir.mkdir(parents=True, exist_ok=True)
        txt_path = dest_dir / f"{job_uid}_class_counts.txt"
        total = sum(class_counts.values())
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("class_idx\tparticle_count\tpercent\n")
            for idx, count in sorted(class_counts.items(), key=lambda kv: -kv[1]):
                f.write(f"{idx}\t{count}\t{100.0*count/total:.2f}\n")
    return {"class_counts": class_counts}


# ==========================================================================
# CryoSPARC Live session (exposures.bson)
# ==========================================================================

def _is_rejecting(value):
    if isinstance(value, dict):
        return any(bool(v) for v in value.values())
    return bool(value)


def load_bson_docs(path: Path):
    if bson is None:
        raise RuntimeError("pip install pymongo (provides the 'bson' module)")
    with open(path, "rb") as f:
        data = f.read()
    return bson.decode_all(data)


def summarize_live_curation(session_dir: Path):
    """Verified against a real exposures.bson: a single BSON document
    wraps an 'exposures' list; each record has independent boolean flags
    manual_reject/threshold_reject/failed/deleted."""
    bson_path = session_dir / "exposures.bson"
    if not bson_path.exists():
        return None
    try:
        docs = load_bson_docs(bson_path)
    except Exception as e:
        eprint(f"  [!] Could not read {bson_path}: {e}")
        return None
    if not docs:
        return {"num_exposures_total": 0}
    if len(docs) == 1 and "exposures" in docs[0] and isinstance(docs[0]["exposures"], list):
        exps = docs[0]["exposures"]
    else:
        exps = docs

    total = len(exps)
    sample_fields = sorted(exps[0].keys()) if exps else []
    present_flags = [k for k in REJECT_FLAG_FIELDS if k in sample_fields]
    result = {"num_exposures_total": total, "sample_fields": sample_fields, "flag_fields_used": present_flags}
    if present_flags:
        rejected = sum(1 for e in exps if any(_is_rejecting(e.get(k)) for k in present_flags))
        result["accepted"] = total - rejected
        result["rejected"] = rejected
    return result


# ==========================================================================
# Event log images (events.bson + GridFS-style blob storage)
#
# Every event with images (2D class views, FSC plots, volume slices,
# viewing-direction plots, Guinier plots, noise model plots, etc.) records
# an 'imgfiles' list: [{filetype, filename, fileid}, ...]. The first
# working theory — that 'gridfs_data' is raw MongoDB storage located by
# searching for a PNG's own binary signature near the fileid — turned out
# to be based on a wrong model of the underlying format, discovered by
# testing the length the technique implied against an independently
# verified true length: the bytes immediately preceding a payload aren't
# a raw BSON length field, they're PYTHON PICKLE opcodes. 'gridfs_data'
# is a stream of pickled Python objects, not a MongoDB data file at all.
# Once recognized, extraction is exact rather than a heuristic: after the
# fileid's own literal ASCII string, a pickle BINBYTES opcode (b'B')
# appears, immediately followed by a 4-byte little-endian length and then
# exactly that many raw bytes — no signature-sniffing or end-of-file
# marker needed, so this works identically for PNG, TXT, and XML alike.
# Verified against three separate matched events.bson + gridfs_data
# pairs: every single PNG (81/81, 65/65, 6/6 — a handful of others were
# genuinely absent from those particular samples) opened as a valid
# image, and every single TXT (7/7) and XML (7/7) decoded as valid,
# well-formed text (each XML's payload ran exactly from its opening tag
# to its closing tag, matching the declared pickle length precisely).
#
# PDFs are still skipped (redundant with the PNG of the same plot).
#
# Preference order per the person's request: an XML/PNG recovered this
# way is the actual file CryoSPARC itself produced, so it's used ahead of
# reconstruct_fsc_exports()'s numeric reconstruction from job.json, which
# is now only a fallback (with a clear warning) for whichever curves
# couldn't be found this way — e.g. no gridfs_data available, or a
# fileid genuinely rotated out of the store.
# ==========================================================================

PICKLE_BINBYTES_OP = b"B"
# ^ FUTURE-DEBUGGING NOTE: this whole extraction mechanism rests on the
# empirical observation (verified against 3 real matched events.bson +
# gridfs_data pairs, see the long comment above) that gridfs_data is a
# stream of Python pickle objects, and that a pickle BINBYTES opcode
# (single byte b'B', pickle protocol 3+) reliably appears a short
# distance after each fileid string, followed by a 4-byte little-endian
# length and then exactly that many payload bytes. This is NOT a
# documented/stable CryoSPARC file format — it's what pickle produces for
# a bytes object under the default/common protocol. If a future
# CryoSPARC version changes its internal serialization (e.g. a newer
# pickle protocol using BINBYTES8 (opcode b'\x8e', 8-byte length) for
# large payloads, or drops pickle for something else entirely), this
# function will start silently returning None for everything (fileid
# found, but no 'B' opcode nearby, or a garbage length that fails the
# bounds check) rather than crashing — that silent "0 located" failure
# mode is the first thing to suspect if event-file extraction stops
# working, and the fix would start with re-running the byte-level
# investigation this comment summarizes (grep raw bytes around a known
# fileid, as done originally) against a fresh matched sample.
FILEID_SEARCH_WINDOW = 4096  # bytes to look ahead of a fileid string for its pickle BINBYTES
                             # opcode; observed offsets in real data were under 150 bytes, this
                             # is a generous margin without risking finding an unrelated 'B' byte
                             # that happens to occur in some other field's value first


def load_gridfs_blobs(gridfs_dirs):
    """Read every file under the given gridfs_data directories into memory
    once, so each fileid lookup doesn't re-read from disk."""
    blobs = []
    for d in gridfs_dirs:
        for f in sorted(p for p in d.rglob("*") if p.is_file()):
            try:
                blobs.append(f.read_bytes())
            except Exception:
                continue
    return blobs


def extract_blob_by_fileid(fileid: str, blobs):
    """Find `fileid` as a literal ASCII string in one of the raw gridfs
    blobs, then read the pickle BINBYTES-encoded payload that follows
    shortly after it (see module note above). Returns the raw bytes, or
    None if not found in any blob. Works identically for any filetype —
    the extraction doesn't need to know or care what's inside."""
    needle = fileid.encode("ascii")
    for data in blobs:
        idx = data.find(needle)
        if idx == -1:
            continue
        b_pos = data.find(PICKLE_BINBYTES_OP, idx, idx + len(needle) + FILEID_SEARCH_WINDOW)
        if b_pos == -1 or b_pos + 5 > len(data):
            continue
        length = int.from_bytes(data[b_pos + 1:b_pos + 5], "little")
        if length <= 0 or b_pos + 5 + length > len(data):
            continue
        return data[b_pos + 5: b_pos + 5 + length]
    return None


def parse_event_iteration(text: str):
    """Extract an iteration number from an event's own descriptive text —
    the event's 'iteration' field itself was checked against real data and
    found to be unpopulated (None) even on events that clearly are
    per-iteration, so the number embedded in the text ('...iteration 6',
    or a bare trailing number like 'Per particle scale factors 006') is
    the only reliable source. Events with neither pattern (e.g. one-off
    setup/diagnostic plots like 'Initial Real Space Slices') return None
    and are treated as always-relevant rather than iteration-specific."""
    if not text:
        return None
    m = re.search(r"iteration[:\s]+(\d+)", text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*$", text)
    if m:
        return int(m.group(1))
    return None


EVENT_FILETYPES_TO_EXTRACT = ("png", "txt", "xml")  # pdf skipped — redundant with the png


def extract_event_images(job_dir: Path, project_dir: Path, dest_dir: Path, dry_run: bool):
    """Copy PNG/TXT/XML files attached to this job's event log (including
    the actual FSC curve files CryoSPARC itself wrote, not just plots),
    restricted to the FINAL iteration (plus one-off events with no
    iteration number, e.g. initial setup diagnostics) — CryoSPARC
    re-renders most of these every iteration, and gridfs_data holds the
    job's entire history, not just its current state. Each one is located
    by its own fileid (see module note above) and saved under its real,
    correct name."""
    events_path = job_dir / "events.bson"
    if not events_path.exists():
        return None
    try:
        docs = load_bson_docs(events_path)
    except Exception as e:
        eprint(f"  [!] Could not read {events_path}: {e}")
        return None
    events = docs[0]["events"] if (len(docs) == 1 and isinstance(docs[0], dict) and "events" in docs[0]) else docs

    all_expected = []
    for e in events:
        text = e.get("text", "")
        for im in (e.get("imgfiles") or []):
            if im.get("filetype") not in EVENT_FILETYPES_TO_EXTRACT:
                continue  # pdf intentionally skipped — redundant with the png of the same plot
            all_expected.append({"filename": im.get("filename"), "fileid": im.get("fileid"),
                                  "filetype": im.get("filetype"), "event_text": text,
                                  "iteration": parse_event_iteration(text)})

    known_iters = [x["iteration"] for x in all_expected if x["iteration"] is not None]
    max_iter = max(known_iters) if known_iters else None

    # Some plots (e.g. "Alignment map A") recur every iteration under the exact same
    # filename, but their event text has no parseable iteration number (parse_event_iteration
    # returns None), so they'd otherwise all look like distinct "always relevant" one-off
    # files and pile up as J50_alignment_map_a.png, _1.png, _2.png, ... one per iteration.
    # Keep only the chronologically LAST occurrence of each such filename — events are in
    # chronological order, so a later dict write simply overwrites the earlier one.
    last_unknown_by_filename = {}
    for x in all_expected:
        if x["iteration"] is None:
            last_unknown_by_filename[x["filename"]] = x

    expected = []
    for x in all_expected:
        if x["iteration"] is not None:
            if max_iter is None or x["iteration"] == max_iter:
                expected.append(x)
        elif last_unknown_by_filename.get(x["filename"]) is x:
            expected.append(x)

    n_dropped = len(all_expected) - len(expected)
    if n_dropped:
        print(f"  {len(all_expected)} event file(s) in total across all iterations; restricting "
              f"to the final iteration ({max_iter}) and only the most recent occurrence of any "
              f"recurring same-named plot: {len(expected)} kept, {n_dropped} earlier-iteration "
              f"duplicate(s) dropped")

    img_dir = dest_dir / "event_images"
    gridfs_dirs = [d for d in (job_dir / "gridfs_data", project_dir / "gridfs_data") if d.is_dir()]
    if not gridfs_dirs:
        eprint(f"  [!] No gridfs_data directory found at {job_dir}/gridfs_data or "
               f"{project_dir}/gridfs_data — can't extract event files. "
               f"{len(expected)} file(s) expected for the final iteration but their bytes "
               f"couldn't be located.")
        if not dry_run and expected:
            img_dir.mkdir(parents=True, exist_ok=True)
            with open(img_dir / "index.json", "w") as f:
                json.dump({"located": 0, "expected": len(expected),
                           "final_iteration_number": max_iter, "files": []}, f, indent=2)
        return {"located": 0, "expected": len(expected), "located_by_type": {}}

    blobs = load_gridfs_blobs(gridfs_dirs)
    manifest = []
    located = 0
    located_by_type = {}
    if not dry_run and expected:
        img_dir.mkdir(parents=True, exist_ok=True)
    for exp in expected:
        blob = extract_blob_by_fileid(exp["fileid"], blobs) if exp["fileid"] else None
        entry = {"file": exp["filename"], "event_text": exp["event_text"],
                  "filetype": exp["filetype"], "located": blob is not None}
        if blob is not None:
            located += 1
            located_by_type[exp["filetype"]] = located_by_type.get(exp["filetype"], 0) + 1
            dest_path = img_dir / (exp["filename"] or f"{job_dir.name}_{exp['fileid']}.{exp['filetype']}")
            counter = 1
            while dest_path.exists():
                dest_path = img_dir / f"{Path(exp['filename']).stem}_{counter}{Path(exp['filename']).suffix}"
                counter += 1
            entry["file"] = dest_path.name
            if not dry_run:
                dest_path.write_bytes(blob)
        manifest.append(entry)

    by_type_str = ", ".join(f"{n} {t}" for t, n in sorted(located_by_type.items()))
    print(f"  {located}/{len(expected)} final-iteration event file(s) located by fileid and saved "
          f"under their real names ({by_type_str})" + (f"; {len(expected) - located} not found in "
          f"the available gridfs_data file(s) (possibly rotated out of the store)"
          if located < len(expected) else ""))

    if not dry_run and (manifest or expected):
        img_dir.mkdir(parents=True, exist_ok=True)
        with open(img_dir / "index.json", "w") as f:
            json.dump({"located": located, "expected": len(expected), "located_by_type": located_by_type,
                       "final_iteration_number": max_iter, "files": manifest}, f, indent=2)

    return {"located": located, "expected": len(expected), "located_by_type": located_by_type}


def deep_archive_events_and_log(job_dir: Path, project_dir: Path, dest_dir: Path, dry_run: bool):
    """Always run for a deep-archived job, regardless of type/mode (except
    bare_minimum): copy events.bson and any job log file(s), then extract
    whatever images they reference."""
    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
    events_path = job_dir / "events.bson"
    if events_path.exists():
        print(f"  copying events.bson")
        if not dry_run:
            shutil.copy2(events_path, dest_dir / "events.bson")
    log_files = list(job_dir.glob("*.log"))
    for f in log_files:
        print(f"  copying {f.name}")
        if not dry_run:
            shutil.copy2(f, dest_dir / f.name)
    return extract_event_images(job_dir, project_dir, dest_dir, dry_run)


# ==========================================================================
# Job parameters ("what was typed in the inputs section to run this job")
# ==========================================================================

def extract_job_params(job_json: dict):
    """CryoSPARC records the full parameter set with defaults and titles
    in 'params_base', and any user-overridden values in 'params_spec'.
    Merging them gives the actual effective configuration — pixel size,
    dose, Cs, file-path wildcards for import jobs, etc. — generically for
    any job type, using CryoSPARC's own human-readable titles rather than
    guessing which raw field names matter for which job type."""
    base = job_json.get("params_base") or {}
    spec = job_json.get("params_spec") or {}
    effective = {}
    for key, meta in base.items():
        if not isinstance(meta, dict):
            continue
        val = meta.get("value")
        if key in spec and isinstance(spec[key], dict) and "value" in spec[key]:
            val = spec[key]["value"]
        effective[key] = {
            "value": val,
            "title": meta.get("title", key),
            "section": meta.get("section"),
            "hidden": meta.get("hidden", False),
            "advanced": meta.get("advanced", False),
        }
    for key, meta in spec.items():
        if key not in effective and isinstance(meta, dict):
            effective[key] = {"value": meta.get("value"), "title": key, "section": None,
                               "hidden": False, "advanced": False}
    return effective


def extract_output_groups(job_json: dict):
    """CryoSPARC's own GUI shows, for each of a job's output groups (the
    things you connect as inputs to a downstream job — particles, a
    volume, a mask, ...), how many items are in it. That number is
    already sitting right on the group as 'num_items', alongside a
    human-readable 'title' — no need to load and count a .cs file
    ourselves. This also surfaces groups our particle/micrograph-count
    heuristic wouldn't otherwise catch, e.g. a distinct 'particles_rejected'
    output group some jobs have alongside their main 'particles' group."""
    groups = []
    for g in job_json.get("output_result_groups", []) or []:
        if not isinstance(g, dict):
            continue
        groups.append({
            "name": g.get("name"),
            "title": g.get("title") or g.get("name"),
            "type": g.get("type"),
            "num_items": g.get("num_items"),
        })
    return groups


def find_session_uid_param(params: dict):
    """Live-processing jobs carry a 'Session UID' parameter pointing at
    the actual Live session directory (e.g. 'S1') where the real
    acquisition/processing parameters live — the job's own params_base is
    otherwise fairly minimal. Matches on title (the reliable, human-facing
    label) rather than an assumed key name, since the underlying key
    naming isn't confirmed."""
    for key, meta in params.items():
        title = (meta.get("title") or "").lower()
        if "session" in title and ("uid" in title or "id" in title):
            return meta.get("value")
        if "session" in key.lower() and "uid" in key.lower():
            return meta.get("value")
    return None


def infer_wildcard_pattern(paths):
    """Best-effort reconstruction of a glob-like pattern from a sample of
    resolved file paths — NOT the literal wildcard string originally typed
    into the import job (that isn't recoverable from the exposure records),
    just an illustrative pattern showing what varies between filenames."""
    paths = [p for p in paths if p]
    if not paths:
        return None
    if len(paths) == 1:
        return paths[0]
    prefix_len = 0
    shortest = min(len(p) for p in paths)
    while prefix_len < shortest and all(p[prefix_len] == paths[0][prefix_len] for p in paths):
        prefix_len += 1
    suffix_len = 0
    while suffix_len < shortest - prefix_len and all(p[-1 - suffix_len] == paths[0][-1 - suffix_len] for p in paths):
        suffix_len += 1
    prefix, suffix = paths[0][:prefix_len], (paths[0][-suffix_len:] if suffix_len else "")
    if prefix_len + suffix_len >= shortest:
        return paths[0]
    return prefix + "*" + suffix


def find_live_session_params(project_dir: Path, session_uid: str):
    """The Live session directory itself (S<N>) has no separate
    parameter-bearing json/bson files — confirmed against a real session's
    listing (ctfestimated/exposures.bson/extract/gridfs_data/
    import_movies/motioncorrected/pick, nothing else). The actual
    acquisition parameters instead live PER-EXPOSURE inside exposures.bson,
    under groups.exposure.mscope_params (accel_kv, cs_mm,
    total_dose_e_per_A2, ...) and groups.exposure.movie_blob /
    micrograph_blob (pixel sizes, resolved file paths) — verified constant
    across all exposures in a real session (single exp_group_id, identical
    values throughout), so the first exposure's values are representative
    unless multiple exposure groups are detected, in which case that's
    flagged rather than silently averaged/overwritten."""
    exposures_path = project_dir / session_uid / "exposures.bson"
    if not exposures_path.exists():
        return None
    try:
        docs = load_bson_docs(exposures_path)
    except Exception as e:
        eprint(f"  [!] Could not read {exposures_path}: {e}")
        return None
    if not docs:
        return None
    exps = docs[0]["exposures"] if (len(docs) == 1 and isinstance(docs[0], dict) and "exposures" in docs[0]) else docs
    if not exps:
        return None

    def first(d, key):
        v = (d or {}).get(key)
        return v[0] if isinstance(v, list) and v else None

    def exposure_group(e):
        return e.get("groups", {}).get("exposure", {})

    group_ids = {first(exposure_group(e).get("mscope_params", {}), "exp_group_id") for e in exps}

    g0 = exposure_group(exps[0])
    mp, movie_blob, mic_blob = g0.get("mscope_params", {}), g0.get("movie_blob", {}), g0.get("micrograph_blob", {})

    def entry(title, value):
        return {"title": title, "value": value, "section": None, "hidden": False, "advanced": False}

    params = {
        "accel_kv": entry("Accelerating voltage (kV)", first(mp, "accel_kv")),
        "cs_mm": entry("Spherical aberration Cs (mm)", first(mp, "cs_mm")),
        "total_dose_e_per_A2": entry("Total exposure dose (e/A^2)", first(mp, "total_dose_e_per_A2")),
        "phase_plate": entry("Phase plate used", first(mp, "phase_plate")),
        "neg_stain": entry("Negative stain", first(mp, "neg_stain")),
        "raw_pixel_size_A": entry("Raw movie pixel size (A)", first(movie_blob, "psize_A")),
        "micrograph_pixel_size_A": entry("Motion-corrected pixel size (A)", first(mic_blob, "psize_A")),
        "num_exposures": entry("Number of exposures in session", len(exps)),
        # 'abs_file_path' (a top-level field on the exposure record, not nested under groups.exposure)
        # holds the real absolute path on the acquisition/storage filesystem (e.g. under /data/...),
        # which is more useful for locating the original data than movie_blob's project-relative
        # path (e.g. 'S1/import_movies/...') — both are included since they serve different purposes.
        "example_movie_path_absolute": entry("Example movie file path (absolute)", exps[0].get("abs_file_path")),
        "example_movie_path_relative": entry("Example movie file path (project-relative)", first(movie_blob, "path")),
    }

    if len(group_ids) > 1:
        eprint(f"  [!] Live session {session_uid} has {len(group_ids)} distinct exposure groups "
               f"(different optics groups) — only reporting the first group's values; others may differ.")
        params["_note"] = entry("Note", f"{len(group_ids)} exposure groups present; showing group 1 of these only")

    sample_abs_paths = [e.get("abs_file_path") for e in exps[:200]]
    abs_wildcard = infer_wildcard_pattern(sample_abs_paths)
    if abs_wildcard:
        params["inferred_movie_path_pattern_absolute"] = entry(
            "Movies path pattern, absolute (inferred from filenames — not necessarily the literal "
            "wildcard typed in)", abs_wildcard)

    sample_paths = [first(exposure_group(e).get("movie_blob", {}), "path") for e in exps[:200]]
    wildcard = infer_wildcard_pattern(sample_paths)
    if wildcard:
        params["inferred_movie_path_pattern"] = entry(
            "Movie path pattern, project-relative (inferred from filenames — not necessarily the "
            "literal wildcard typed in)", wildcard)

    return {"exposures.bson (per-exposure groups.exposure fields)": params}


# ==========================================================================

def deep_archive_generic_job(job_dir: Path, job_uid: str, dest_dir: Path, dry_run: bool):
    print(f"\n== Deep-archiving {job_uid} (generic: copying job directory contents) ==")
    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
    copied, skipped = [], []
    for f in job_dir.iterdir():
        if not f.is_file():
            continue
        size = f.stat().st_size
        if size > GENERIC_COPY_MAX_BYTES:
            skipped.append((f.name, size))
            continue
        print(f"    copying {f.name}")
        if not dry_run:
            shutil.copy2(f, dest_dir / f.name)
        copied.append(f.name)
    for name, size in skipped:
        eprint(f"  [!] Skipped {name} ({size/1e6:.0f} MB > {GENERIC_COPY_MAX_BYTES/1e6:.0f} MB cap)")
    return {"copied_files": copied, "skipped_large_files": [n for n, _ in skipped]}


# ==========================================================================
# Project-wide scan: cheap stats for every job, every session
# ==========================================================================

def scan_job(job_dir: Path, project_dir: Path):
    """Build a lightweight record for one job using only cheap operations
    (job.json parse + memory-mapped .cs shape reads — never a full load
    of a large array)."""
    uid = job_dir.name
    job_json, job_json_path = load_job_json(job_dir)
    if job_json is None:
        return {"id": uid, "job_type": "?", "category": None, "title": uid, "status": None,
                "created_at": None, "workspace_uids": [], "parents": [], "children": [],
                "particles": None, "micrographs": None, "accepted": None, "rejected": None,
                "job_json_found": False, "params": {}, "output_groups": []}

    job_type = job_json.get("job_type") or job_json.get("type") or "?"
    category = classify_job_type(job_type)
    record = {
        "id": uid,
        "job_type": job_type,
        "category": category,
        "title": job_json.get("title") or job_type,
        "status": job_json.get("status"),
        "created_at": parse_created_at(job_json),
        "workspace_uids": job_json.get("workspace_uids") or [],
        "parents": get_parents(job_json, uid),
        "children": get_children(job_json),
        "particles": None,
        "micrographs": None,
        "accepted": None,
        "rejected": None,
        "job_json_found": True,
        "params": extract_job_params(job_json),
        "output_groups": extract_output_groups(job_json),
    }

    # output_result_groups' own 'num_items' is the authoritative source when present — falls
    # back to counting rows in a .cs file (below) only for schemas/jobs that don't have it.
    for g in record["output_groups"]:
        if g["type"] == "particle" and g["name"] == "particles" and g["num_items"] is not None:
            record["particles"] = g["num_items"]
        elif g["type"] == "particle" and g["name"] == "particles_rejected" and g["num_items"] is not None:
            record["rejected"] = g["num_items"]
        elif g["type"] == "exposure" and g["num_items"] is not None:
            record["micrographs"] = g["num_items"]

    particle_cs = find_particle_cs(job_dir)
    if particle_cs and record["particles"] is None:
        arr = load_cs_mmap(particle_cs)
        if arr is not None:
            record["particles"] = dataset_len(arr)

    exposure_cs = find_exposure_cs(job_dir)
    if exposure_cs and record["micrographs"] is None:
        arr = load_cs_mmap(exposure_cs)
        if arr is not None:
            record["micrographs"] = dataset_len(arr)

    ar = find_accept_reject_counts(job_dir)
    if record["accepted"] is None:
        record["accepted"] = ar["accepted"]
    if record["rejected"] is None:
        record["rejected"] = ar["rejected"]

    # Extract basic acquisition parameters for import/motion/ctf-style jobs where cheap to do
    if category in ("import", "motion", "ctf", "live") and exposure_cs is not None:
        arr = load_cs_mmap(exposure_cs)
        if arr is not None:
            for key in ("accel_kv", "cs_mm", "amp_contrast", "total_dose_e_per_A2", "raw_pixel_size"):
                val = get_field(arr, key)
                if val is not None:
                    record[key] = val

    # Live-processing jobs: their own params are minimal — the real acquisition/processing
    # config lives in the Live session directory their 'Session UID' param points at.
    if category == "live":
        session_uid = find_session_uid_param(record["params"])
        if session_uid:
            record["live_session_uid"] = session_uid
            live_params = find_live_session_params(project_dir, str(session_uid))
            if live_params:
                record["live_session_params"] = live_params
            else:
                eprint(f"  [!] {uid}: Session UID '{session_uid}' found, but no parameter-bearing "
                       f"json found under {project_dir / str(session_uid)} — this part is a guess "
                       f"about the session's internal layout; let me know its actual structure "
                       f"if this keeps coming up empty.")

    return record


def find_sibling_session_params(records: dict, uid: str, session_uid):
    """Fallback for when a Live session's own directory has nothing
    recoverable (e.g. a cleared/emptied session, or exposures.bson
    missing/empty): look for any OTHER job in the project whose own
    'Session UID' parameter points at the same session, and surface that
    job's full parameter set too. Some CryoSPARC versions run separate
    per-stage worker jobs (seen in project.json's job_types as
    'rtp_worker') alongside the main live_session job — if one of those
    carries the acquisition settings directly in its own job.json even
    when the session folder is empty, this picks it up. Best-effort and
    not confirmed against a real example of this happening — reuses the
    already-generic extract_job_params rather than guessing new field
    names, so it costs nothing if no such sibling exists."""
    matches = {}
    for other_uid, rec in records.items():
        if other_uid == uid:
            continue
        other_session = find_session_uid_param(rec.get("params", {}))
        if other_session and str(other_session) == str(session_uid):
            visible = {k: v for k, v in rec.get("params", {}).items() if not v.get("hidden")}
            if len(visible) > 1:  # more than just its own Session UID param
                matches[other_uid] = rec["params"]
    return matches or None


def scan_project(project_dir: Path, max_jobs=5000):
    job_dirs = sorted(
        [d for d in project_dir.iterdir() if d.is_dir() and re.fullmatch(r"J\d+", d.name)],
        key=lambda d: int(d.name[1:]),
    )
    session_dirs = sorted(
        [d for d in project_dir.iterdir() if d.is_dir() and re.fullmatch(r"S\d+", d.name)],
        key=lambda d: int(d.name[1:]),
    )
    if len(job_dirs) > max_jobs:
        eprint(f"  [!] {len(job_dirs)} job dirs found, capping at {max_jobs} (--max-jobs to raise).")
        job_dirs = job_dirs[:max_jobs]

    records = {}
    for d in job_dirs:
        records[d.name] = scan_job(d, project_dir)

    # Second pass (needs every job scanned first): for live jobs where the session directory
    # itself had nothing, try sibling jobs that reference the same session.
    for uid, rec in records.items():
        if rec.get("category") == "live" and "live_session_params" not in rec and rec.get("live_session_uid"):
            sibling = find_sibling_session_params(records, uid, rec["live_session_uid"])
            if sibling:
                rec["live_session_params_from_sibling_jobs"] = sibling
                print(f"  [i] {uid}: no session data in S{rec['live_session_uid']}, but found "
                      f"matching params in sibling job(s) {list(sibling.keys())} — using those instead")

    session_records = {}
    for d in session_dirs:
        curation = summarize_live_curation(d)
        session_records[d.name] = curation or {}

    return job_dirs, session_dirs, records, session_records


# ==========================================================================
# Metadata mirror + deep-copy orchestration
# ==========================================================================

def copy_project_level_files(project_dir: Path, outdir: Path, dry_run: bool):
    """project.json, workspaces.json, job_manifest.json live at the
    project root (confirmed present alongside the J<N>/S<N> directories)
    — small, genuinely useful metadata, so copy them verbatim."""
    names = ["project.json", "workspaces.json", "job_manifest.json"]
    copied = []
    for name in names:
        src = project_dir / name
        if src.exists():
            print(f"  copying {name}")
            if not dry_run:
                shutil.copy2(src, outdir / name)
            copied.append(name)
    return copied


def parse_workspaces_json(project_dir: Path):
    """workspaces.json (confirmed present at the project root — a plain
    JSON array, not BSON) has each workspace's uid and human-readable
    title, plus for Live workspaces a session_uid linking it to its S<N>
    session directory."""
    path = project_dir / "workspaces.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            workspaces = json.load(f)
    except Exception as e:
        eprint(f"  [!] Could not parse {path}: {e}")
        return {}
    return {w["uid"]: w.get("title") or w["uid"] for w in workspaces if "uid" in w}


def mirror_metadata(job_dirs, session_dirs, outdir: Path, deep_set: dict,
                     skip_live_bson_copy: bool, dry_run: bool):
    """Every job gets its job.json mirrored. Jobs not selected for deep
    archiving go under outdir/other_jobs/<uid>/ rather than cluttering the
    top level with dozens of bare-metadata-only folders — deep-archived
    jobs (which have more to look at) stay directly under outdir/<uid>/."""
    print(f"\n== Mirroring metadata for {len(job_dirs)} job(s) and {len(session_dirs)} session(s) ==")
    for d in job_dirs:
        _, job_json_path = load_job_json(d)
        if job_json_path is None:
            eprint(f"  [!] No job.json found for {d.name}")
            continue
        dest = outdir / d.name if d.name in deep_set else outdir / "other_jobs" / d.name
        if not dry_run:
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(job_json_path, dest / "job.json")

    for d in session_dirs:
        bson_path = d / "exposures.bson"
        if not bson_path.exists():
            continue
        size_mb = bson_path.stat().st_size / 1e6
        print(f"  {d.name}/exposures.bson is {size_mb:.1f} MB", end="")
        if skip_live_bson_copy:
            print("  (skipping raw copy, --skip-live-bson-copy set; curation counts still computed)")
        else:
            print("  (copying)")
            if not dry_run:
                (outdir / d.name).mkdir(parents=True, exist_ok=True)
                shutil.copy2(bson_path, outdir / d.name / "exposures.bson")


def parse_deep_spec(spec: str):
    """Parse '--deep JOB_UID' or '--deep JOB_UID:mode'."""
    if ":" in spec:
        uid, mode = spec.split(":", 1)
        if mode not in VALID_MODES:
            raise ValueError(f"Unknown mode '{mode}' for {uid}. Valid modes: {sorted(VALID_MODES)}")
        return uid.strip(), mode
    return spec.strip(), None


def resolve_deep_set(requested, records):
    """Expand the user's requested job list into {uid: mode}. A local
    refinement job automatically pulls in its preceding global refinement
    parent (found via the job's own recorded 'parents', not guessed),
    using that job's own category default mode unless the user already
    named it explicitly."""
    deep = {}
    notes = []
    for spec in requested:
        try:
            uid, mode = parse_deep_spec(spec)
        except ValueError as e:
            eprint(f"  [!] {e}")
            continue
        if uid not in records:
            eprint(f"  [!] --deep {uid}: no such job found in this project, skipping.")
            continue
        rec = records[uid]
        deep[uid] = mode or CATEGORY_DEFAULT_MODE.get(rec["category"], "last_iteration")

        if rec["category"] == "local_refine":
            global_parents = [p for p in rec["parents"] if p in records and records[p]["category"] == "global_refine"]
            for gp in global_parents:
                if gp not in deep:
                    gp_mode = CATEGORY_DEFAULT_MODE.get(records[gp]["category"], "last_iteration")
                    notes.append(f"{uid} is a local refinement — also deep-archiving its preceding "
                                 f"global refinement {gp} (mode: {gp_mode})")
                    deep[gp] = gp_mode
            if not global_parents:
                notes.append(f"{uid} is a local refinement, but no preceding global refinement job "
                             f"was found among its parents ({rec['parents']}) — add it manually with "
                             f"--deep if there is one.")
    for n in notes:
        print(f"  {n}")
    return deep


def run_deep_archive(uid, mode, project_dir, outdir, records, dry_run):
    job_dir = project_dir / uid
    dest_dir = outdir / uid
    category = records[uid]["category"]

    if mode == "all" and category in ALL_MODE_DISALLOWED_CATEGORIES:
        fallback = CATEGORY_DEFAULT_MODE.get(category, "last_iteration")
        eprint(f"  [!] --deep {uid}:all refused — '{category}' jobs typically hold huge raw data "
               f"(motion-corrected micrographs / extracted particle stacks / Live session data), "
               f"and copying all of it defeats the point of a minimal archive. Falling back to "
               f"'{fallback}'. Use last_iteration or minimalist, or copy that job's directory "
               f"yourself directly if you really do want everything.")
        mode = fallback

    result = {"mode": mode}
    if mode == "bare_minimum":
        print(f"\n== {uid}: bare_minimum (job.json only, already in the metadata mirror) ==")
        return result

    print(f"\n== Deep-archiving {uid} (mode: {mode}) ==")
    result["event_images"] = deep_archive_events_and_log(job_dir, project_dir, dest_dir, dry_run)

    if mode == "minimalist":
        return result

    if mode == "all":
        result["generic_copy"] = deep_archive_generic_job(job_dir, uid, dest_dir, dry_run)
        return result

    # mode == "last_iteration"
    job_json, _ = load_job_json(job_dir)
    result["last_iteration"] = deep_archive_last_iteration(job_dir, uid, job_json, dest_dir,
                                                            result["event_images"], dry_run)
    if category == "class2d":
        result["class2d_counts"] = compute_class2d_counts(job_dir, uid, dest_dir, dry_run)

    return result



# ==========================================================================
# HTML "card view" overview — no external libraries, no network required
# ==========================================================================

CATEGORY_COLORS = {
    "local_refine":  "#e07a5f", "global_refine": "#e07a5f", "sharpen": "#e07a5f",
    "class2d":       "#81b29a", "class3d":       "#81b29a",
    "motion":        "#3d5a80", "ctf":           "#3d5a80",
    "extract":       "#98c1d9",
    "picking":       "#f2cc8f",
    "curation":      "#bc9cb0", "inspect_picks": "#bc9cb0",
    "deepemhancer":  "#c65b7c", "orientation_diagnostics": "#c65b7c",
    "local_resolution": "#c65b7c", "local_filter": "#c65b7c",
    "live":          "#6d6875",
    "import":        "#adb5bd",
    None:            "#5c6270",
}

STATUS_COLORS = {
    "completed": "#6fae7a", "failed": "#c96a5a", "killed": "#c96a5a",
    "running": "#e0b25f", "queued": "#8991a1", "building": "#8991a1",
}

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>CryoSPARC archive overview — {project_name}</title>
<style>
  :root {{
    --bg: #14161a; --panel: #1c1f26; --card: #21242c; --border: #2c313c;
    --text: #dfe3ea; --muted: #8991a1; --accent: #e07a5f;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ height: 100%; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
    display: flex; overflow: hidden;
  }}
  #main {{
    flex: 1 1 auto; min-width: 0; height: 100vh; overflow-y: auto; overflow-x: hidden;
  }}
  header {{
    position: sticky; top: 0; z-index: 10; background: var(--bg);
    padding: 14px 20px 10px; border-bottom: 1px solid var(--border);
  }}
  header h1 {{ font-size: 16px; margin: 0 0 2px; }}
  header .sub {{ font-size: 12px; color: var(--muted); }}
  .controls {{ display: flex; gap: 10px; align-items: flex-start; margin-top: 10px; flex-wrap: wrap; }}
  input#filter {{
    background: var(--panel); border: 1px solid var(--border); color: var(--text);
    padding: 7px 10px; border-radius: 6px; font-size: 13px; width: 240px;
  }}
  #tabs {{ display: flex; flex-wrap: wrap; gap: 8px; align-content: flex-start; }}
  .tab {{
    display: inline-flex; align-items: center;
    background: var(--panel); border: 1px solid var(--border); color: var(--muted);
    padding: 6px 12px; border-radius: 999px; font-size: 12.5px; cursor: pointer; user-select: none;
    white-space: nowrap;
  }}
  .tab.active {{ background: var(--accent); color: #14161a; border-color: var(--accent); font-weight: 600; }}
  .view-toggle {{ display: flex; gap: 4px; margin-left: auto; }}
  .view-btn {{
    background: var(--panel); border: 1px solid var(--border); color: var(--muted);
    padding: 6px 12px; border-radius: 6px; font-size: 12.5px; cursor: pointer;
  }}
  .view-btn.active {{ background: var(--accent); color: #14161a; border-color: var(--accent); font-weight: 600; }}
  #tree-wrapper {{ display: none; }}
  #tree-toolbar {{
    display: flex; gap: 8px; align-items: center; padding: 10px 20px;
    border-bottom: 1px solid var(--border); font-size: 12px; color: var(--muted);
  }}
  #tree-toolbar button {{
    background: var(--panel); border: 1px solid var(--border); color: var(--text);
    width: 26px; height: 26px; border-radius: 5px; cursor: pointer; font-size: 14px;
  }}
  #tree-viewport {{
    position: relative; overflow: hidden; height: calc(100vh - 200px);
    cursor: grab; background: var(--bg);
  }}
  #tree-viewport.grabbing {{ cursor: grabbing; }}
  #tree-canvas {{ position: absolute; top: 0; left: 0; transform-origin: 0 0; }}
  #tree-edges {{ position: absolute; top: 0; left: 0; overflow: visible; pointer-events: none; }}
  .tree-node {{
    position: absolute; width: 160px; background: var(--card); border: 1px solid var(--border);
    border-radius: 6px; padding: 6px 8px; cursor: pointer; transition: opacity .15s, border-color .15s;
  }}
  .tree-node .cat-bar {{ height: 3px; margin: -6px -8px 5px; border-radius: 3px 3px 0 0; }}
  .tree-node .uid {{ font-weight: 700; font-size: 12px; }}
  .tree-node .type {{ font-size: 9.5px; color: var(--muted); margin-top: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .tree-node .counts {{ font-size: 10px; margin-top: 3px; }}
  .tree-node.dimmed {{ opacity: 0.32; }}
  .tree-node.sel {{ border-color: #ffffff; border-width: 2px; }}
  .tree-node.ancestor {{ border-color: #d9534f; box-shadow: 0 0 0 1px #d9534f inset; }}
  .tree-node.descendant {{ border-color: #5cb85c; box-shadow: 0 0 0 1px #5cb85c inset; }}
  .tree-node .deep-badge {{
    position: absolute; top: -6px; right: -6px; background: var(--accent); color: #14161a;
    font-size: 7px; font-weight: 700; border-radius: 999px; padding: 1px 5px; white-space: nowrap;
  }}
  #grid-wrapper {{ position: relative; }}
  #connector-svg {{
    position: absolute; top: 0; left: 0; width: 100%; height: 100%;
    pointer-events: none; overflow: visible; z-index: 1;
  }}
  #grid {{
    display: flex; flex-wrap: wrap; gap: 10px; padding: 16px 20px 24px;
    position: relative; z-index: 2;
  }}
  .card {{
    width: 190px; background: var(--card); border: 1px solid var(--border); border-radius: 8px;
    padding: 10px; cursor: pointer; transition: opacity .15s, border-color .15s, transform .1s;
    position: relative;
  }}
  .card:hover {{ border-color: #4a5060; }}
  .card.dimmed {{ opacity: 0.32; }}
  .card.sel {{ border-color: #ffffff; border-width: 2px; }}
  .card.ancestor {{ border-color: #d9534f; box-shadow: 0 0 0 1px #d9534f inset; }}
  .card.descendant {{ border-color: #5cb85c; box-shadow: 0 0 0 1px #5cb85c inset; }}
  .card .cat-bar {{ height: 4px; border-radius: 3px; margin: -10px -10px 8px; }}
  .card .uid {{ font-weight: 700; font-size: 13px; }}
  .card .status-dot {{ display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-left: 6px; }}
  .card .type {{ font-size: 10.5px; color: var(--muted); margin: 2px 0 6px; word-break: break-word; }}
  .card .counts {{ font-size: 11px; line-height: 1.5; }}
  .card .counts .k {{ color: var(--muted); }}
  .card .deep-badge {{
    position: absolute; top: -7px; right: -7px; background: var(--accent); color: #14161a;
    font-size: 8px; font-weight: 700; border-radius: 999px; padding: 2px 6px; white-space: nowrap;
  }}
  #detail {{
    position: relative; flex: 0 0 340px; min-width: 220px; max-width: 80vw; height: 100vh;
    overflow-y: auto; overflow-x: hidden; box-sizing: border-box; z-index: 50;
    background: var(--panel); border-left: 1px solid var(--border); padding: 14px 18px;
    font-size: 12.5px;
  }}
  #drag-handle {{
    position: absolute; top: 0; left: -5px; bottom: 0; width: 10px; cursor: ew-resize;
  }}
  #drag-handle::after {{
    content: ''; position: absolute; top: 50%; left: 0; width: 4px; height: 40px;
    border-radius: 2px; background: var(--border); transform: translateY(-50%);
  }}
  #drag-handle:hover::after {{ background: var(--accent); }}
  #detail .placeholder {{ color: var(--muted); margin-top: 10px; }}
  #detail h2 {{ font-size: 13px; margin: 0 0 8px; }}
  #detail {{ overflow-wrap: anywhere; }}
  #detail .row {{ margin: 3px 0; }}
  #detail .k {{ color: var(--muted); display: inline-block; width: 90px; vertical-align: top; }}
  .chip {{
    display: inline-block; background: var(--card); border: 1px solid var(--border);
    border-radius: 999px; padding: 2px 9px; margin: 2px 3px 2px 0; cursor: pointer; font-size: 11.5px;
  }}
  .chip:hover {{ border-color: var(--accent); }}
  .pk {{ color: var(--muted); padding: 2px 8px 2px 0; border-bottom: 1px solid var(--border); vertical-align: top; width: 42%; word-break: break-word; }}
  .pv {{ padding: 2px 0; border-bottom: 1px solid var(--border); word-break: break-word; overflow-wrap: anywhere; }}
  #detail .close {{ float: right; cursor: pointer; color: var(--muted); }}
  .legend-inline {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 6px; font-size: 11px; color: var(--muted); }}
  .legend-inline span.sw {{ display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 4px; }}
</style>
</head>
<body>
<div id="main">
<header>
  <h1>CryoSPARC archive overview</h1>
  <div class="sub">{project_name} &middot; {n_jobs} jobs &middot; {n_sessions} Live session(s) &middot; {n_deep} deep-archived</div>
  <div class="controls">
    <input id="filter" type="text" placeholder="Filter by UID, title, or type...">
    <span id="tabs"></span>
    <span class="view-toggle">
      <button class="view-btn active" id="btn-card-view" onclick="setViewMode('card')">Card view</button>
      <button class="view-btn" id="btn-tree-view" onclick="setViewMode('tree')">Tree view</button>
    </span>
  </div>
  <div class="legend-inline" id="legend"></div>
</header>
<div id="grid-wrapper">
  <svg id="connector-svg"></svg>
  <div id="grid"></div>
</div>
<div id="tree-wrapper">
  <div id="tree-toolbar">
    <button onclick="treeZoomBy(0.8)" title="Zoom out">&minus;</button>
    <span id="tree-zoom-label">100%</span>
    <button onclick="treeZoomBy(1.25)" title="Zoom in">+</button>
    <button onclick="treeFitView()" title="Fit to view" style="width:auto;padding:0 10px;">Fit</button>
    <span>Drag to pan, scroll to zoom, click a job for details</span>
  </div>
  <div id="tree-viewport">
    <div id="tree-canvas">
      <svg id="tree-edges"></svg>
    </div>
  </div>
</div>
</div>
<div id="detail">
  <div id="drag-handle"></div>
  <span class="close" id="detail-close" onclick="closeDetail()" style="display:none;">close &times;</span>
  <div id="detail-body"><div class="placeholder">Click a card for more details.</div></div>
</div>
<script>
const JOBS = {jobs_json};
const SESSIONS = {sessions_json};
const CAT_COLORS = {colors_json};
const STATUS_COLORS = {status_colors_json};
const DEEP = {deep_json};
const WORKSPACE_TITLES = {workspace_titles_json};

const byId = Object.fromEntries(JOBS.map(j => [j.id, j]));

function fmt(n) {{ return (n === null || n === undefined) ? '\u2014' : n.toLocaleString(); }}
function catColor(cat) {{ return CAT_COLORS[cat] || CAT_COLORS['null']; }}

function ancestorsOf(id, seen) {{
  seen = seen || new Set();
  const j = byId[id];
  if (!j) return seen;
  for (const p of j.parents) {{
    if (!seen.has(p) && byId[p]) {{ seen.add(p); ancestorsOf(p, seen); }}
  }}
  return seen;
}}
function descendantsOf(id, seen) {{
  seen = seen || new Set();
  const j = byId[id];
  if (!j) return seen;
  for (const c of j.children) {{
    if (!seen.has(c) && byId[c]) {{ seen.add(c); descendantsOf(c, seen); }}
  }}
  return seen;
}}

let currentWorkspace = 'all';
let currentQuery = '';
let selectedId = null;

function jobVisible(j) {{
  if (currentWorkspace !== 'all') {{
    if (currentWorkspace === '__none__') {{ if (j.workspace_uids.length) return false; }}
    else if (!j.workspace_uids.includes(currentWorkspace)) return false;
  }}
  if (currentQuery) {{
    const q = currentQuery.toLowerCase();
    if (!j.id.toLowerCase().includes(q) && !(j.title||'').toLowerCase().includes(q)
        && !(j.job_type||'').toLowerCase().includes(q)) return false;
  }}
  return true;
}}

function countsHtml(j) {{
  const lines = [];
  if (j.particles !== null) lines.push(`<span class="k">particles</span> ${{fmt(j.particles)}}`);
  if (j.micrographs !== null) lines.push(`<span class="k">micrographs</span> ${{fmt(j.micrographs)}}`);
  if (j.accepted !== null || j.rejected !== null)
    lines.push(`<span class="k">accept/rej</span> ${{fmt(j.accepted)}} / ${{fmt(j.rejected)}}`);
  return lines.join('<br>');
}}

function render() {{
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  let directParents = new Set(), directChildren = new Set();
  if (selectedId) {{
    const sel = byId[selectedId];
    // Immediate neighbors only (output of one is input to the other) — matches
    // CryoSPARC's own red/green convention, not the full ancestor/descendant chain.
    directParents = new Set((sel.parents || []).filter(p => byId[p]));
    directChildren = new Set((sel.children || []).filter(c => byId[c]));
  }}
  for (const j of JOBS) {{
    const visible = jobVisible(j);
    const card = document.createElement('div');
    card.className = 'card';
    if (!visible) {{ card.style.display = 'none'; }}
    if (selectedId) {{
      if (j.id === selectedId) card.classList.add('sel');
      else if (directParents.has(j.id)) card.classList.add('ancestor');
      else if (directChildren.has(j.id)) card.classList.add('descendant');
      else card.classList.add('dimmed');
    }}
    card.onclick = () => selectJob(j.id);
    card.dataset.uid = j.id;
    const statusColor = STATUS_COLORS[j.status] || '#8991a1';
    card.innerHTML = `
      <div class="cat-bar" style="background:${{catColor(j.category)}}"></div>
      ${{DEEP[j.id] ? `<div class="deep-badge">${{DEEP[j.id].toUpperCase()}}</div>` : ''}}
      <div class="uid">${{j.id}}<span class="status-dot" style="background:${{statusColor}}" title="${{j.status||''}}"></span></div>
      <div class="type">${{j.job_type}}</div>
      <div class="counts">${{countsHtml(j)}}</div>
    `;
    grid.appendChild(card);
  }}
  drawConnectors();
}}

function drawConnectors() {{
  // Simple "tree view" lite: draws straight lines from the selected card to its
  // DIRECT parent/child cards only (matching the red/green highlight scheme) —
  // deliberately not a full graph layout. Cards already have a stable position
  // from the normal flowing grid, so this just connects the dots between
  // wherever they already are, which stays fast and simple regardless of
  // project size (always O(number of direct neighbors), never O(all jobs)).
  const svg = document.getElementById('connector-svg');
  svg.innerHTML = '';
  if (!selectedId) return;
  const selCard = document.querySelector('.card.sel');
  if (!selCard) return;
  const wrapperRect = document.getElementById('grid-wrapper').getBoundingClientRect();
  const center = (rect) => ({{
    x: rect.left + rect.width / 2 - wrapperRect.left,
    y: rect.top + rect.height / 2 - wrapperRect.top,
  }});
  const selCenter = center(selCard.getBoundingClientRect());
  const ns = 'http://www.w3.org/2000/svg';
  document.querySelectorAll('.card.ancestor, .card.descendant').forEach(card => {{
    if (card.style.display === 'none') return;  // filtered out of the current view
    const c = center(card.getBoundingClientRect());
    const line = document.createElementNS(ns, 'line');
    line.setAttribute('x1', selCenter.x);
    line.setAttribute('y1', selCenter.y);
    line.setAttribute('x2', c.x);
    line.setAttribute('y2', c.y);
    line.setAttribute('stroke', card.classList.contains('ancestor') ? '#d9534f' : '#5cb85c');
    line.setAttribute('stroke-width', '2');
    line.setAttribute('stroke-opacity', '0.55');
    svg.appendChild(line);
  }});
}}

window.addEventListener('resize', () => {{ drawConnectors(); if (viewMode === 'tree') renderTree(); }});

// ==========================================================================
// Tree view: a real layered graph layout (Sugiyama-style: assign each job
// to a layer by longest path from its roots, order within each layer via
// a few passes of barycenter averaging to reduce edge crossings, then
// position and draw). Deliberately not pixel-perfect vs CryoSPARC's own
// tree view (no saved node positions to reuse, see module docstring) —
// this is a from-scratch layout, but the actual parent/child data behind
// it is exact, not approximated.
// ==========================================================================

let viewMode = 'card';
let treeInitialized = false;
let treePan = {{ x: 40, y: 40 }};
let treeZoom = 1;
const TREE_LAYER_H = 130;  // vertical spacing between layers (generations), top to bottom
const TREE_NODE_W = 190;   // horizontal spacing between nodes within a layer

function setViewMode(mode) {{
  viewMode = mode;
  document.getElementById('btn-card-view').classList.toggle('active', mode === 'card');
  document.getElementById('btn-tree-view').classList.toggle('active', mode === 'tree');
  document.getElementById('grid-wrapper').style.display = mode === 'card' ? 'block' : 'none';
  document.getElementById('tree-wrapper').style.display = mode === 'tree' ? 'block' : 'none';
  if (mode === 'tree') {{
    renderTree();
    if (!treeInitialized) {{ treeFitView(); treeInitialized = true; }}
  }}
}}

function computeTreeLayout() {{
  const layer = {{}};
  function getLayer(uid, visiting) {{
    if (layer[uid] !== undefined) return layer[uid];
    if (visiting.has(uid)) return 0;  // defensive cycle guard; shouldn't occur in practice
    visiting.add(uid);
    const job = byId[uid];
    const parents = (job.parents || []).filter(p => byId[p]);
    const l = parents.length ? Math.max(...parents.map(p => getLayer(p, visiting))) + 1 : 0;
    layer[uid] = l;
    visiting.delete(uid);
    return l;
  }}
  JOBS.forEach(j => getLayer(j.id, new Set()));

  const numLayers = JOBS.length ? Math.max(...Object.values(layer)) + 1 : 0;
  const layers = Array.from({{ length: numLayers }}, () => []);
  JOBS.forEach(j => layers[layer[j.id]].push(j.id));
  // stable initial order within each layer (creation time, same as card view)
  layers.forEach(l => l.sort((a, b) => (byId[a].created_at || '').localeCompare(byId[b].created_at || '')));

  const order = {{}};
  layers.forEach(l => l.forEach((uid, i) => order[uid] = i));

  function barycenterPass(fromField, layerRange) {{
    for (const li of layerRange) {{
      const layerJobs = layers[li];
      if (!layerJobs.length) continue;
      const scored = layerJobs.map(uid => {{
        const neighbors = (byId[uid][fromField] || []).filter(n => byId[n] && order[n] !== undefined);
        const score = neighbors.length ? neighbors.reduce((s, n) => s + order[n], 0) / neighbors.length : order[uid];
        return {{ uid, score }};
      }});
      scored.sort((a, b) => a.score - b.score);
      scored.forEach((s, i) => {{ order[s.uid] = i; }});
      layers[li] = scored.map(s => s.uid);
    }}
  }}
  const downRange = [...Array(numLayers).keys()].slice(1);
  const upRange = [...Array(numLayers).keys()].slice(0, -1).reverse();
  for (let iter = 0; iter < 3; iter++) {{
    barycenterPass('parents', downRange);
    barycenterPass('children', upRange);
  }}

  const pos = {{}};
  layers.forEach((layerJobs, li) => {{
    layerJobs.forEach((uid, oi) => {{ pos[uid] = {{ x: oi * TREE_NODE_W, y: li * TREE_LAYER_H }}; }});
  }});
  return pos;
}}

function applyTreeTransform() {{
  document.getElementById('tree-canvas').style.transform =
    `translate(${{treePan.x}}px, ${{treePan.y}}px) scale(${{treeZoom}})`;
  document.getElementById('tree-zoom-label').textContent = Math.round(treeZoom * 100) + '%';
}}

function treeFitView() {{
  const canvas = document.getElementById('tree-canvas');
  const nodes = canvas.querySelectorAll('.tree-node');
  if (!nodes.length) return;
  let maxX = 0, maxY = 0;
  nodes.forEach(n => {{
    maxX = Math.max(maxX, parseFloat(n.style.left) + n.offsetWidth);
    maxY = Math.max(maxY, parseFloat(n.style.top) + n.offsetHeight);
  }});
  const viewport = document.getElementById('tree-viewport');
  const vw = viewport.clientWidth - 60, vh = viewport.clientHeight - 60;
  treeZoom = Math.max(0.15, Math.min(1, Math.min(vw / maxX, vh / maxY)));
  treePan = {{ x: 30, y: 30 }};
  applyTreeTransform();
}}

function renderTree() {{
  const canvas = document.getElementById('tree-canvas');
  const positions = computeTreeLayout();
  canvas.querySelectorAll('.tree-node').forEach(n => n.remove());

  let directParents = new Set(), directChildren = new Set();
  if (selectedId) {{
    const sel = byId[selectedId];
    directParents = new Set((sel.parents || []).filter(p => byId[p]));
    directChildren = new Set((sel.children || []).filter(c => byId[c]));
  }}

  for (const j of JOBS) {{
    const p = positions[j.id];
    const node = document.createElement('div');
    node.className = 'tree-node';
    node.style.left = p.x + 'px';
    node.style.top = p.y + 'px';
    node.dataset.uid = j.id;
    if (!jobVisible(j)) node.classList.add('dimmed');
    if (selectedId) {{
      if (j.id === selectedId) node.classList.add('sel');
      else if (directParents.has(j.id)) node.classList.add('ancestor');
      else if (directChildren.has(j.id)) node.classList.add('descendant');
      else node.classList.add('dimmed');
    }}
    node.onclick = () => selectJob(j.id);
    const statusColor = STATUS_COLORS[j.status] || '#8991a1';
    node.innerHTML = `
      <div class="cat-bar" style="background:${{catColor(j.category)}}"></div>
      ${{DEEP[j.id] ? `<div class="deep-badge">${{DEEP[j.id].toUpperCase()}}</div>` : ''}}
      <div class="uid">${{j.id}}<span class="status-dot" style="background:${{statusColor}}"></span></div>
      <div class="type">${{j.job_type}}</div>
      <div class="counts">${{countsHtml(j)}}</div>
    `;
    canvas.appendChild(node);
  }}

  // edges: colored by the parent (source) job's category — a defensible, easy-to-compute
  // convention since job.json's parent/child links don't distinguish which specific output
  // group/slot each connection uses, unlike CryoSPARC's own richer internal graph model
  const svg = document.getElementById('tree-edges');
  svg.innerHTML = '';
  const ns = 'http://www.w3.org/2000/svg';
  const NODE_W = 160, NODE_H = 50;
  let maxX = 0, maxY = 0;
  for (const j of JOBS) {{
    for (const parentId of (j.parents || [])) {{
      if (!byId[parentId] || !positions[parentId]) continue;
      const pp = positions[parentId], cp = positions[j.id];
      const x1 = pp.x + NODE_W / 2, y1 = pp.y + NODE_H;
      const x2 = cp.x + NODE_W / 2, y2 = cp.y;
      maxX = Math.max(maxX, x1, x2); maxY = Math.max(maxY, y1, y2);
      const midY = (y1 + y2) / 2;
      const path = document.createElementNS(ns, 'path');
      path.setAttribute('d', `M ${{x1}} ${{y1}} C ${{x1}} ${{midY}}, ${{x2}} ${{midY}}, ${{x2}} ${{y2}}`);
      path.setAttribute('fill', 'none');
      const highlighted = selectedId && (parentId === selectedId || j.id === selectedId);
      path.setAttribute('stroke', catColor(byId[parentId].category));
      path.setAttribute('stroke-width', highlighted ? '2.5' : '1.5');
      path.setAttribute('stroke-opacity', selectedId ? (highlighted ? '0.9' : '0.12') : '0.45');
      svg.appendChild(path);
    }}
  }}
  svg.setAttribute('width', maxX + 200);
  svg.setAttribute('height', maxY + 100);
  canvas.style.width = (maxX + 200) + 'px';
  canvas.style.height = (maxY + 100) + 'px';
}}

function treeZoomAt(factor, clientX, clientY) {{
  const rect = document.getElementById('tree-viewport').getBoundingClientRect();
  const mx = clientX - rect.left, my = clientY - rect.top;
  const canvasX = (mx - treePan.x) / treeZoom, canvasY = (my - treePan.y) / treeZoom;
  treeZoom = Math.max(0.05, Math.min(3, treeZoom * factor));
  treePan.x = mx - canvasX * treeZoom;
  treePan.y = my - canvasY * treeZoom;
  applyTreeTransform();
}}

function treeZoomBy(factor) {{
  // toolbar +/- buttons have no cursor position to anchor to — zoom on the viewport's own center
  const rect = document.getElementById('tree-viewport').getBoundingClientRect();
  treeZoomAt(factor, rect.left + rect.width / 2, rect.top + rect.height / 2);
}}

(function setupTreePanZoom() {{
  const viewport = document.getElementById('tree-viewport');
  let dragging = false, lastX = 0, lastY = 0, moved = 0;
  viewport.addEventListener('mousedown', (e) => {{
    if (e.target.closest('.tree-node')) return;  // let node clicks through
    dragging = true; lastX = e.clientX; lastY = e.clientY; moved = 0;
    viewport.classList.add('grabbing');
  }});
  window.addEventListener('mousemove', (e) => {{
    if (!dragging) return;
    const dx = e.clientX - lastX, dy = e.clientY - lastY;
    treePan.x += dx; treePan.y += dy;
    moved += Math.abs(dx) + Math.abs(dy);
    lastX = e.clientX; lastY = e.clientY;
    applyTreeTransform();
  }});
  window.addEventListener('mouseup', (e) => {{
    if (dragging && moved < 4 && !e.target.closest('.tree-node')) closeDetail();  // a real click, not a drag
    dragging = false;
    viewport.classList.remove('grabbing');
  }});
  viewport.addEventListener('wheel', (e) => {{
    e.preventDefault();
    treeZoomAt(e.deltaY < 0 ? 1.1 : 0.9, e.clientX, e.clientY);
  }}, {{ passive: false }});
}})();

function outputGroupsHtml(j) {{
  const groups = j.output_groups || [];
  if (!groups.length) return '';
  const rows = groups.map(g =>
    `<tr><td class="pk">${{g.title || g.name}}</td><td class="pv">${{fmt(g.num_items)}} (${{g.type}})</td></tr>`
  ).join('');
  return `<details style="margin-top:10px;" open>
    <summary style="cursor:pointer;color:var(--muted);">Output groups (${{groups.length}})</summary>
    <table style="width:100%;table-layout:fixed;margin-top:6px;border-collapse:collapse;font-size:11.5px;">${{rows}}</table>
  </details>`;
}}

function paramsHtml(j) {{
  const params = j.params || {{}};
  const visible = Object.entries(params).filter(([k, v]) => !v.hidden);
  if (!visible.length) return '';
  const rows = visible.map(([k, v]) =>
    `<tr><td class="pk">${{v.title || k}}</td><td class="pv">${{JSON.stringify(v.value)}}</td></tr>`
  ).join('');
  return `<details style="margin-top:10px;">
    <summary style="cursor:pointer;color:var(--muted);">Parameters (${{visible.length}}, as configured to run this job)</summary>
    <table style="width:100%;table-layout:fixed;margin-top:6px;border-collapse:collapse;font-size:11.5px;">${{rows}}</table>
  </details>`;
}}

function liveParamsHtml(j) {{
  const live = j.live_session_params;
  const sibling = j.live_session_params_from_sibling_jobs;
  if (!live && !sibling) return '';
  let out = `<div style="margin-top:10px;color:var(--muted);font-size:11.5px;">`
    + `Live session: ${{j.live_session_uid || '?'}}</div>`;
  for (const [stage, params] of Object.entries(live || {{}})) {{
    const visible = Object.entries(params).filter(([k, v]) => !v.hidden);
    if (!visible.length) continue;
    const rows = visible.map(([k, v]) =>
      `<tr><td class="pk">${{v.title || k}}</td><td class="pv">${{JSON.stringify(v.value)}}</td></tr>`
    ).join('');
    out += `<details style="margin-top:6px;">
      <summary style="cursor:pointer;color:var(--muted);">${{stage}} (${{visible.length}} params)</summary>
      <table style="width:100%;table-layout:fixed;margin-top:6px;border-collapse:collapse;font-size:11.5px;">${{rows}}</table>
    </details>`;
  }}
  if (sibling) {{
    out += `<div style="margin-top:6px;color:var(--muted);font-size:11px;">Session directory itself `
      + `had nothing usable — showing params from sibling job(s) that reference the same session:</div>`;
    for (const [sib_uid, params] of Object.entries(sibling)) {{
      const visible = Object.entries(params).filter(([k, v]) => !v.hidden);
      if (!visible.length) continue;
      const rows = visible.map(([k, v]) =>
        `<tr><td class="pk">${{v.title || k}}</td><td class="pv">${{JSON.stringify(v.value)}}</td></tr>`
      ).join('');
      out += `<details style="margin-top:6px;">
        <summary style="cursor:pointer;color:var(--muted);">from ${{sib_uid}} (${{visible.length}} params)</summary>
        <table style="width:100%;table-layout:fixed;margin-top:6px;border-collapse:collapse;font-size:11.5px;">${{rows}}</table>
      </details>`;
    }}
  }}
  return out;
}}

function selectJob(id) {{
  selectedId = id;
  const j = byId[id];
  document.getElementById('detail-close').style.display = '';
  const ancestors = [...ancestorsOf(id)];
  const descendants = [...descendantsOf(id)];
  const chip = (uid) => `<span class="chip" onclick="selectJob('${{uid}}')">${{uid}}</span>`;
  document.getElementById('detail-body').innerHTML = `
    <h2>${{j.id}} &mdash; ${{j.title}}</h2>
    <div class="row"><span class="k">Type</span>${{j.job_type}}</div>
    <div class="row"><span class="k">Status</span>${{j.status || '\u2014'}}</div>
    <div class="row"><span class="k">Created</span>${{j.created_at || '\u2014'}}</div>
    <div class="row"><span class="k">Workspaces</span>${{(j.workspace_uids||[]).map(w => WORKSPACE_TITLES[w] ? `${{WORKSPACE_TITLES[w]}} (${{w}})` : w).join(', ') || '\u2014'}}</div>
    <div class="row"><span class="k">Particles</span>${{fmt(j.particles)}}</div>
    <div class="row"><span class="k">Micrographs</span>${{fmt(j.micrographs)}}</div>
    <div class="row"><span class="k">Accepted</span>${{fmt(j.accepted)}}</div>
    <div class="row"><span class="k">Rejected</span>${{fmt(j.rejected)}}</div>
    <div class="row"><span class="k">Deep-archived</span>${{DEEP[j.id]
        ? `yes, mode: ${{DEEP[j.id]}} \u2014 <a href="./${{j.id}}/" target="_blank" style="color:var(--accent);">open ${{j.id}}/ folder \u2197</a>`
        : 'no (metadata only) \u2014 <a href="./other_jobs/' + j.id + '/" target="_blank" style="color:var(--accent);">open ' + j.id + '/ folder \u2197</a>'}}</div>
    <div class="row"><span class="k">Direct parents</span>${{j.parents.map(chip).join(' ') || '\u2014'}}</div>
    <div class="row"><span class="k">Direct children</span>${{j.children.map(chip).join(' ') || '\u2014'}}</div>
    <div class="row"><span class="k">All ancestors</span><span style="color:#d9534f;">\u25c0</span> ${{ancestors.length}} job(s) upstream</div>
    <div class="row"><span class="k">All descendants</span><span style="color:#5cb85c;">\u25b6</span> ${{descendants.length}} job(s) downstream</div>
    ${{outputGroupsHtml(j)}}
    ${{paramsHtml(j)}}
    ${{liveParamsHtml(j)}}
  `;
  render();
  if (viewMode === 'tree') renderTree();
  document.querySelector(`.card.sel`)?.scrollIntoView({{ block: 'nearest', behavior: 'smooth' }});
}}

function closeDetail() {{
  selectedId = null;
  document.getElementById('detail-close').style.display = 'none';
  document.getElementById('detail-body').innerHTML = '<div class="placeholder">Click a card for more details.</div>';
  render();
  if (viewMode === 'tree') renderTree();
}}

document.getElementById('grid').addEventListener('click', (e) => {{
  if (e.target.id === 'grid') closeDetail();  // click on empty grid background, not a card
}});

// drag the handle at the left edge of the detail panel to resize its width
(function() {{
  const handle = document.getElementById('drag-handle');
  const panel = document.getElementById('detail');
  let dragging = false;
  handle.addEventListener('mousedown', (e) => {{ dragging = true; e.preventDefault(); }});
  window.addEventListener('mousemove', (e) => {{
    if (!dragging) return;
    const newWidth = window.innerWidth - e.clientX;
    panel.style.flexBasis = Math.max(220, Math.min(window.innerWidth * 0.8, newWidth)) + 'px';
    drawConnectors();  // keeps card-view connector lines in sync as the grid area is resized
    if (viewMode === 'tree') renderTree();  // keeps tree layout/edges in sync with the new viewport width
  }});
  window.addEventListener('mouseup', () => {{ dragging = false; }});
}})();

document.getElementById('filter').addEventListener('input', (e) => {{
  currentQuery = e.target.value.trim();
  render();
  if (viewMode === 'tree') renderTree();
}});

// workspace tabs
const workspaces = [...new Set(JOBS.flatMap(j => j.workspace_uids))].sort();
const hasUnassigned = JOBS.some(j => !j.workspace_uids.length);
const tabsDiv = document.getElementById('tabs');
function makeTab(id, label) {{
  const t = document.createElement('span');
  t.className = 'tab' + (id === 'all' ? ' active' : '');
  t.textContent = label;
  t.onclick = () => {{
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    currentWorkspace = id;
    render();
    if (viewMode === 'tree') renderTree();
  }};
  tabsDiv.appendChild(t);
}}
makeTab('all', 'All workspaces');
for (const w of workspaces) makeTab(w, WORKSPACE_TITLES[w] ? `${{WORKSPACE_TITLES[w]}} (${{w}})` : w);
if (hasUnassigned) makeTab('__none__', 'No workspace');

// legend
const legendDiv = document.getElementById('legend');
const seenCats = new Set();
for (const j of JOBS) {{
  const cat = j.category || 'other';
  if (seenCats.has(cat)) continue;
  seenCats.add(cat);
  const el = document.createElement('span');
  el.innerHTML = `<span class="sw" style="background:${{catColor(j.category)}}"></span>${{cat}}`;
  legendDiv.appendChild(el);
}}

render();
</script>
</body>
</html>
"""


def build_overview_html(project_name, records, session_records, deep_modes, workspace_titles):
    jobs = list(records.values())

    def safe_json(obj):
        return json.dumps(obj).replace("</", "<\\/")

    return HTML_TEMPLATE.format(
        project_name=project_name,
        n_jobs=len(jobs),
        n_sessions=len(session_records),
        n_deep=len(deep_modes),
        jobs_json=safe_json(jobs),
        sessions_json=safe_json(session_records),
        colors_json=safe_json({(k if k else "null"): v for k, v in CATEGORY_COLORS.items()}),
        status_colors_json=safe_json(STATUS_COLORS),
        deep_json=safe_json(deep_modes),
        workspace_titles_json=safe_json(workspace_titles),
    )


# ==========================================================================
# Main
# ==========================================================================

def default_archive_name(project_dir: Path):
    """Prefer the project's own given title (project.json's 'title' field,
    confirmed present at the project root) over the bare directory name —
    sanitized to something filesystem-safe, with '_archive' appended so
    it's obviously an output, not the project itself."""
    proj_json = project_dir / "project.json"
    title = None
    if proj_json.exists():
        try:
            with open(proj_json) as f:
                title = json.load(f).get("title")
        except Exception:
            pass
    base = title or project_dir.name
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip("_") or "cryosparc_project"
    return f"{safe}_archive"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project-dir", required=True, type=Path)
    ap.add_argument("--outdir", type=Path, default=None,
                     help="Where to write the archive. Defaults to a new directory in the current "
                          "working directory, named after the project's own title in project.json "
                          "(falling back to the project directory's name) with '_archive' appended.")
    ap.add_argument("--deep", nargs="*", default=[], metavar="JOB_UID[:mode]",
                     help="Job UIDs (e.g. J50) to archive beyond bare metadata. Each job gets a "
                          "sensible default mode based on its type (2D classification: minimalist; "
                          "refinement/classification jobs: last_iteration; anything else: last_iteration "
                          "if uncategorized else minimalist) — override per-job with J50:mode. "
                          f"Valid modes: {sorted(VALID_MODES)}. A local refinement automatically "
                          "pulls in its preceding global refinement too.")
    ap.add_argument("--max-jobs", type=int, default=5000)
    ap.add_argument("--skip-live-bson-copy", action="store_true",
                     help="Don't copy raw exposures.bson (can be large with embedded thumbnails) — "
                          "still computes and saves the derived accept/reject counts.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project_dir = args.project_dir.resolve()
    if not project_dir.is_dir():
        eprint(f"ERROR: {project_dir} is not a directory")
        sys.exit(1)

    if args.outdir is None:
        outdir = (Path.cwd() / default_archive_name(project_dir)).resolve()
        print(f"--outdir not given, defaulting to {outdir}")
    else:
        outdir = args.outdir.resolve()
    archive_name = outdir.name

    if not args.dry_run:
        outdir.mkdir(parents=True, exist_ok=True)

    print(f"Scanning {project_dir} ...")
    job_dirs, session_dirs, records, session_records = scan_project(project_dir, args.max_jobs)
    print(f"Found {len(job_dirs)} job(s), {len(session_dirs)} Live session(s)")

    # Resolved before mirroring so bare-metadata jobs can go straight into other_jobs/
    # rather than needing a second pass to move them after the fact.
    deep_set = resolve_deep_set(args.deep, records)

    print(f"\n== Copying project-level metadata ==")
    copy_project_level_files(project_dir, outdir, args.dry_run)
    workspace_titles = parse_workspaces_json(project_dir)

    mirror_metadata(job_dirs, session_dirs, outdir, deep_set, args.skip_live_bson_copy, args.dry_run)

    deep_results = {}
    for uid in sorted(deep_set, key=lambda u: int(u[1:])):
        try:
            deep_results[uid] = run_deep_archive(uid, deep_set[uid], project_dir, outdir, records, args.dry_run)
        except Exception as e:
            eprint(f"  [!] ERROR deep-archiving {uid}: {e!r} — skipping this job and continuing with "
                   f"the rest (its metadata mirror is unaffected; only its deep-archive extras failed).")
            deep_results[uid] = {"error": repr(e)}

    print(f"\n== Writing overview ==")
    if not args.dry_run:
        html = build_overview_html(project_dir.name, records, session_records, deep_set, workspace_titles)
        html_path = outdir / f"{archive_name}.html"
        html_path.write_text(html, encoding="utf-8")
        print(f"  wrote {html_path}")

        summary = {
            "project_dir": str(project_dir),
            "jobs": records,
            "live_sessions": session_records,
            "workspace_titles": workspace_titles,
            "deep_archived": sorted(deep_set),
            "deep_archive_results": deep_results,
        }
        json_path = outdir / f"{archive_name}_summary.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"  wrote {json_path}")

        by_category = Counter(r["category"] or "uncategorized" for r in records.values())
        txt_path = outdir / f"{archive_name}_summary.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"CryoSPARC project archive: {project_dir.name}\n")
            f.write(f"{len(records)} jobs, {len(session_records)} Live session(s)\n\n")
            f.write("Jobs by category:\n")
            for cat, n in by_category.most_common():
                f.write(f"  {cat}: {n}\n")
            f.write(f"\nDeep-archived jobs: {', '.join(sorted(deep_set)) or '(none)'}\n")
            for uid, sname in session_records.items():
                if "accepted" in sname:
                    f.write(f"\nLive session {uid}: {sname['num_exposures_total']} exposures, "
                            f"{sname['accepted']} accepted, {sname['rejected']} rejected\n")
        print(f"  wrote {txt_path}")

    print("\nDone." + ("  (dry run — nothing was copied or written)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
