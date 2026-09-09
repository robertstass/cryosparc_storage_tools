#!/usr/bin/env python3
"""Shared loading of CryoSPARC project lists from CSV or saved HTML.

This module is the single place that understands the two input shapes
accepted across the storage tools:

  * a CryoSPARC "Manage: projectData" HTML page saved from the browser, or
    a CSV produced from it (columns include Project ID, Owner, Directory), or
  * a minimal ``owner,directory`` CSV such as the one produced by
    cryosparc_project_sources_owner_directory.py.

It is used by cryosparc_storage_scan.py and cryosparc_live_scan_particles.py
so the two scanners always agree on how projects are discovered, filtered,
and reported. The generic HTML-table helpers below (``direct_cells``,
``expanded_headers``, ``unique_headers``, ``clean_text``) are also reused by
cryosparc_manage_projectData_to_csv.py, and ``MINIMAL_CSV_FIELDS`` is reused
by cryosparc_project_sources_owner_directory.py, so all four scripts agree on
the same table-parsing and minimal-CSV conventions.

HTML input needs BeautifulSoup 4 (`pip install beautifulsoup4`). CSV input
uses only the Python standard library.
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

if TYPE_CHECKING:
    from bs4 import Tag


# Column names for the minimal CSV format produced by
# cryosparc_project_sources_owner_directory.py and accepted here as a
# shortcut input alongside the full projectData CSV/HTML export.
MINIMAL_CSV_FIELDS = ["owner", "directory"]


@dataclass(frozen=True)
class Project:
    project_id: str
    title: str
    owner: str
    directory: Path


def clean_text(value: str) -> str:
    """Normalize HTML/CSV text: collapse whitespace, drop non-breaking spaces."""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def direct_cells(row: "Tag") -> list["Tag"]:
    """Return only immediate th/td children, ignoring nested-table cells."""
    return row.find_all(["th", "td"], recursive=False)


def expanded_headers(row: "Tag") -> list[str]:
    """Expand colspan cells so the header width matches data-cell width."""
    headers: list[str] = []
    for cell in direct_cells(row):
        text = clean_text(cell.get_text(" ", strip=True))
        try:
            colspan = max(1, int(cell.get("colspan", 1)))
        except (TypeError, ValueError):
            colspan = 1
        for i in range(1, colspan + 1):
            headers.append(text if i == 1 else f"{text}_{i}")
    return headers


def unique_headers(headers: list[str]) -> list[str]:
    """Make headers non-empty and unique while preserving order."""
    result: list[str] = []
    counts: dict[str, int] = {}
    for i, header in enumerate(headers, start=1):
        base = clean_text(header) or f"column_{i}"
        counts[base] = counts.get(base, 0) + 1
        result.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return result


def _load_html_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    """Extract the CryoSPARC project table from saved HTML.

    This is deliberately self-contained so callers can accept HTML directly
    without importing the separate cryosparc_manage_projectData_to_csv.py
    conversion script.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "HTML input requires BeautifulSoup 4. Install with: pip install beautifulsoup4\n"
            "Alternatively, convert the HTML to CSV first and use CSV input."
        ) from exc

    html = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    def row_values(row: "Tag") -> list[str]:
        return [clean_text(cell.get_text(" ", strip=True)) for cell in direct_cells(row)]

    required = {"project id", "owner", "directory"}
    selected_table = None
    selected_header = None
    selected_headers: list[str] = []

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            headers = expanded_headers(row)
            lowered = {h.lower() for h in headers}
            if required.issubset(lowered):
                selected_table = table
                selected_header = row
                selected_headers = unique_headers(headers)
                break
        if selected_table is not None:
            break

    if selected_table is None or selected_header is None:
        raise RuntimeError(
            "Could not find a CryoSPARC project table containing Project ID, Owner, and Directory."
        )

    width = len(selected_headers)
    rows: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for row in selected_table.find_all("tr"):
        if row is selected_header:
            continue
        cells = direct_cells(row)
        # Ignore rows from nested tables and other layouts.
        if len(cells) != width:
            continue
        values = row_values(row)
        if not any(values):
            continue
        key = tuple(values)
        if key in seen:
            continue
        seen.add(key)
        rows.append(values)

    return selected_headers, rows


def _projects_from_records(fieldnames: Sequence[str], records: Iterable[dict[str, str]]) -> list[Project]:
    lookup = {name.strip().lower(): name for name in fieldnames if name is not None}

    # Minimal source CSVs produced by cryosparc_project_sources_owner_directory.py
    # contain only the MINIMAL_CSV_FIELDS columns: owner,directory
    # Accept those directly and derive project_id/title from the directory name.
    minimal = all(field in lookup for field in MINIMAL_CSV_FIELDS) and "project id" not in lookup

    if minimal:
        owner_col = lookup["owner"]
        dir_col = lookup["directory"]
        id_col = title_col = None
    else:
        missing = [name for name in ("project id", "owner", "directory") if name not in lookup]
        if missing:
            available = ", ".join(fieldnames)
            raise RuntimeError(
                f"Input is missing required column(s): {', '.join(missing)}. Available columns: {available}"
            )
        id_col = lookup["project id"]
        owner_col = lookup["owner"]
        dir_col = lookup["directory"]
        title_col = lookup.get("title")

    projects: list[Project] = []
    seen_dirs: set[str] = set()
    for rec in records:
        directory_text = clean_text(rec.get(dir_col, ""))
        if not directory_text:
            continue
        directory = Path(directory_text).expanduser()
        key = os.path.normpath(str(directory))
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        project_name = directory.name
        projects.append(
            Project(
                project_id=clean_text(rec.get(id_col, "")) if id_col else project_name,
                title=clean_text(rec.get(title_col, "")) if title_col else project_name,
                owner=clean_text(rec.get(owner_col, "")),
                directory=directory,
            )
        )
    return projects


def load_projects(path: Path) -> list[Project]:
    """Load projects from a projectData CSV/HTML export or a minimal owner/directory CSV."""
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        headers, rows = _load_html_rows(path)
        records = [dict(zip(headers, row)) for row in rows]
        return _projects_from_records(headers, records)

    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise RuntimeError("CSV has no header row.")
            return _projects_from_records(reader.fieldnames, reader)

    raise RuntimeError("Input must be a .csv, .html, or .htm file.")


def filter_projects(projects: Sequence[Project], owners: set[str]) -> list[Project]:
    if not owners:
        return list(projects)
    return [project for project in projects if project.owner in owners]


def owner_counts(projects: Sequence[Project]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for project in projects:
        counts[project.owner or "<blank>"] = counts.get(project.owner or "<blank>", 0) + 1
    return counts
