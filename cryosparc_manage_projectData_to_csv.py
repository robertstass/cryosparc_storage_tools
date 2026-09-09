#!/usr/bin/env python3
"""Convert an HTML webpage table to CSV.

Designed for CryoSPARC saved webpages, including pages that contain nested
HTML tables. The parser uses the selected table's header width to ignore rows
from nested tables.

Usage examples
--------------
# Pick the table containing "Project ID" automatically:
python cryosparc_manage_projectData_to_csv.py page.html -o projectData.csv

# Pick a table by zero-based HTML <table> index:
python cryosparc_manage_projectData_to_csv.py page.html -o output.csv --table-index 0

# Pick the first table whose header contains both terms:
python cryosparc_manage_projectData_to_csv.py page.html -o output.csv \
    --contains "Project ID" "Project Size"

Dependencies
------------
pip install beautifulsoup4
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup, Tag

from cryosparc_project_io import clean_text, direct_cells, expanded_headers, unique_headers


def table_rows(table: Tag) -> list[Tag]:
    """Return all descendant rows, including rows wrapped in tbody/divs."""
    return table.find_all("tr")


def row_values(row: Tag) -> list[str]:
    return [clean_text(cell.get_text(" ", strip=True)) for cell in direct_cells(row)]


def find_header_row(table: Tag, required_terms: Iterable[str] = ()) -> tuple[Tag, list[str]]:
    """Find a likely header row and return (row, normalized header labels)."""
    required = [clean_text(x).lower() for x in required_terms if clean_text(x)]

    candidates: list[tuple[int, Tag, list[str]]] = []
    for row in table_rows(table):
        cells = direct_cells(row)
        if not cells:
            continue
        values = row_values(row)
        header_values = expanded_headers(row)
        # Prefer rows containing TH elements, but allow TD-only headers used
        # by some client-rendered webpages.
        th_count = sum(cell.name == "th" for cell in cells)
        score = th_count * 100
        joined = " | ".join(v.lower() for v in header_values)
        if required and all(term in joined for term in required):
            score += 1000
        # Headers are usually wider than data rows. This also helps CryoSPARC,
        # where nested workspace tables have fewer cells than the project row.
        score += len(values)
        candidates.append((score, row, header_values))

    if not candidates:
        raise ValueError("Could not find a row that can be used as a table header.")

    if required:
        matches = [c for c in candidates if all(t in " | ".join(v.lower() for v in c[2]) for t in required)]
        if not matches:
            terms = ", ".join(required_terms)
            raise ValueError(f'No table header matched: {terms}')
        _, header, headers = max(matches, key=lambda x: x[0])
    else:
        _, header, headers = max(candidates, key=lambda x: x[0])

    return header, headers


def extract_table(table: Tag, required_terms: Iterable[str] = ()) -> tuple[list[str], list[list[str]]]:
    """Extract header + data rows from one HTML table.

    Rows are restricted to the header's number of immediate cells. This is
    important for pages such as CryoSPARC that contain nested workspace tables
    inside a larger project table.
    """
    header_row, raw_headers = find_header_row(table, required_terms)
    headers = unique_headers(raw_headers)
    width = len(raw_headers)
    raw_header_width = len(direct_cells(header_row))

    data: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    for row in table_rows(table):
        if row is header_row:
            continue
        cells = direct_cells(row)
        # The header may contain colspan groups. Accept rows that match the
        # expanded header width and ignore shorter nested-table rows.
        if len(cells) != width:
            # width is already the expanded logical header width in most cases;
            # keep this fallback for headers containing unusual markup.
            if len(cells) != raw_header_width:
                continue
        values = row_values(row)
        if len(values) < width:
            values.extend([""] * (width - len(values)))
        # Ignore repeated header rows and completely empty rows.
        if [v.lower() for v in values] == [h.lower() for h in raw_headers]:
            continue
        if not any(values):
            continue
        key = tuple(values)
        if key in seen:
            continue
        seen.add(key)
        data.append(values)

    return headers, data


def choose_table(soup: BeautifulSoup, table_index: int | None, contains: list[str]) -> Tag:
    tables = soup.find_all("table")
    if not tables:
        raise ValueError("No HTML <table> elements were found.")

    if table_index is not None:
        if table_index < 0 or table_index >= len(tables):
            raise ValueError(f"--table-index must be between 0 and {len(tables) - 1}.")
        return tables[table_index]

    # Prefer a table whose header contains the requested terms.
    if contains:
        wanted = [clean_text(x).lower() for x in contains]
        for table in tables:
            for row in table_rows(table):
                header_values = expanded_headers(row)
                if not header_values:
                    continue
                joined = " | ".join(v.lower() for v in header_values)
                if all(term in joined for term in wanted):
                    return table
        raise ValueError("No HTML table matched the requested --contains terms.")

    # CryoSPARC projectData pages normally expose "Project ID" in the visible
    # project table. Use that as a helpful default, then fall back to table 0.
    for table in tables:
        for row in table_rows(table):
            if "project id" in {v.lower() for v in row_values(row)}:
                return table
    return tables[0]


def write_csv(headers: list[str], rows: list[list[str]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(headers)
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract an HTML table and write it as CSV.")
    parser.add_argument("html", type=Path, help="Input HTML webpage saved to disk")
    parser.add_argument("-o", "--output", type=Path, help="Output CSV path (default: <input>.csv)")
    parser.add_argument("--table-index", type=int, default=None, help="Zero-based HTML <table> index")
    parser.add_argument(
        "--contains",
        nargs="+",
        default=[],
        metavar="TEXT",
        help="Select the first table whose header contains all supplied terms",
    )
    parser.add_argument("--list-tables", action="store_true", help="List table indexes and short previews, then exit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.html.exists():
        print(f"ERROR: input file not found: {args.html}", file=sys.stderr)
        return 2

    try:
        html = args.html.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")

        if args.list_tables:
            if not tables:
                print("No HTML tables found.")
                return 0
            for i, table in enumerate(tables):
                rows = table_rows(table)
                preview = next((row_values(r) for r in rows if row_values(r)), [])
                print(f"{i}: rows={len(rows):<4} preview={preview[:8]}")
            return 0

        table = choose_table(soup, args.table_index, args.contains)
        headers, rows = extract_table(table, args.contains)
        output = args.output or args.html.with_suffix(".csv")
        write_csv(headers, rows, output)

        print(f"Wrote {len(rows)} rows x {len(headers)} columns to {output}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
