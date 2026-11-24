"""Introspection of file / cluster mapping files (XLSX or CSV).

Two layouts are recognised, for files (channel e.g. file_no) and for clusters (e.g. cluster_no):

1. "simple": the file is named like the channel (file_no.csv, file_no.xlsx, ...). Column 1 holds the
   integer number, column 2 the name. An optional header row is skipped.
2. "table": any other name; must be XLSX. Files: the tab "pheno" (the only tab if there is just
   one) must contain a column named like the channel; a naming column is then proposed
   (file_name first, then sample_id). Clusters: the tab "clusters" must have the columns
   clustering_id, cluster_no and cluster_name, and the chosen clustering (the cluster channel name)
   must be listed in clustering_id.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

XLSX_EXT = {".xlsx", ".xlsm"}
TEXT_EXT = {".csv", ".tsv", ".txt"}
FILE_SHEET = "pheno"
CLUSTER_SHEET = "clusters"
NAME_COLUMN_CHOICES = ["file_name", "sample_id"]          # first choice first
CLUSTER_COLUMNS = ("clustering_id", "cluster_no", "cluster_name")


@dataclass
class MapInfo:
    ok: bool = False
    message: str = ""
    kind: str = ""                       # "simple" | "table"
    sheet: str | None = None             # tab used (table)
    id_column: str | None = None         # file table: the column matching the channel
    columns: list[str] = field(default_factory=list)      # file table: candidate name columns
    suggested: str | None = None         # file table: proposed name column
    clusterings: list[str] = field(default_factory=list)  # cluster table: values of clustering_id


# ---------------------------------------------------------------- helpers
def is_simple(path: str | Path, channel: str) -> bool:
    """File named like the channel (file_no.csv for channel file_no)."""
    return Path(path).stem.lower() == (channel or "").lower()


def _as_int(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() else None
    if isinstance(v, str):
        try:
            f = float(v.strip())
        except ValueError:
            return None
        return int(f) if f.is_integer() else None
    return None


def _text_rows(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    delim = max(",;\t", key=first.count) if any(d in first for d in ",;\t") else ","
    return list(csv.reader(io.StringIO(text), delimiter=delim))


def _open_xlsx(path: Path):
    return openpyxl.load_workbook(path, read_only=True, data_only=True)


def _pick_sheet(names: list[str], wanted: str) -> str | None:
    for n in names:
        if n.strip().lower() == wanted:
            return n
    return names[0] if len(names) == 1 else None


def _fail(msg: str, kind: str = "") -> MapInfo:
    return MapInfo(ok=False, message=msg, kind=kind)


# ---------------------------------------------------------------- simple layout
def read_simple(path: str | Path) -> list[tuple[int, str]]:
    """Read a 'number, name' file. Raises ValueError with a readable message."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in XLSX_EXT:
        wb = _open_xlsx(p)
        try:
            rows = [list(r) for r in wb[wb.sheetnames[0]].iter_rows(values_only=True)]
        finally:
            wb.close()
    elif ext in TEXT_EXT:
        rows = _text_rows(p)
    else:
        raise ValueError("Unsupported file type (use XLSX, CSV, TSV or TXT)")

    out: list[tuple[int, str]] = []
    seen: set[int] = set()
    header_allowed = True
    for i, row in enumerate(rows, start=1):
        if all(c is None or str(c).strip() == "" for c in row):
            continue
        if len(row) < 2:
            raise ValueError(f"Row {i} has fewer than two columns")
        no = _as_int(row[0])
        if no is None:
            if header_allowed:                    # first non-empty row = header
                header_allowed = False
                continue
            raise ValueError(f"Row {i}: '{row[0]}' is not an integer")
        header_allowed = False
        name = "" if row[1] is None else str(row[1]).strip()
        if not name:
            raise ValueError(f"Row {i}: no name for {no}")
        if no in seen:
            raise ValueError(f"Row {i}: duplicate number {no}")
        seen.add(no)
        out.append((no, name))
    if not out:
        raise ValueError("No entries found")
    return out


def _inspect_simple(p: Path, what: str) -> MapInfo:
    try:
        entries = read_simple(p)
    except Exception as e:  # noqa: BLE001
        return _fail(str(e), "simple")
    no, name = entries[0]
    return MapInfo(ok=True, kind="simple",
                   message=f"{what} mapping (number, name): {len(entries)} entries, e.g. {no} → {name}")


# ---------------------------------------------------------------- files
def inspect_file_map(path: str | Path, channel: str) -> MapInfo:
    p = Path(path)
    if is_simple(p, channel):
        return _inspect_simple(p, "File")
    if p.suffix.lower() not in XLSX_EXT:
        return _fail(f"Expected an XLSX file (or a CSV/XLSX file named '{channel}')")
    try:
        wb = _open_xlsx(p)
    except Exception as e:  # noqa: BLE001
        return _fail(f"Cannot read the file: {e}", "table")
    try:
        sheet = _pick_sheet(wb.sheetnames, FILE_SHEET)
        if sheet is None:
            return _fail(f"Several tabs and none named '{FILE_SHEET}' (tabs: {', '.join(wb.sheetnames)})",
                         "table")
        first = next(wb[sheet].iter_rows(max_row=1, values_only=True), ())
    finally:
        wb.close()

    header = [str(h).strip() for h in first if h is not None and str(h).strip()]
    id_col = next((h for h in header if h == channel), None) or \
        next((h for h in header if h.lower() == channel.lower()), None)
    if id_col is None:
        return _fail(f"Column '{channel}' not found in tab '{sheet}'", "table")
    cols = [h for h in header if h != id_col]
    suggested = next((c for want in NAME_COLUMN_CHOICES for c in cols if c.lower() == want), None)
    msg = f"Tab '{sheet}', column '{id_col}' found. " + (
        f"Proposed name column: {suggested}" if suggested else "Choose the column holding the names")
    return MapInfo(ok=True, kind="table", sheet=sheet, id_column=id_col, columns=cols,
                   suggested=suggested, message=msg)


# ---------------------------------------------------------------- clusters
def inspect_cluster_map(path: str | Path, channel: str) -> MapInfo:
    p = Path(path)
    if is_simple(p, channel):
        return _inspect_simple(p, "Cluster")
    if p.suffix.lower() not in XLSX_EXT:
        return _fail(f"Expected an XLSX file (or a CSV/XLSX file named '{channel}')")
    try:
        wb = _open_xlsx(p)
    except Exception as e:  # noqa: BLE001
        return _fail(f"Cannot read the file: {e}", "table")
    try:
        sheet = _pick_sheet(wb.sheetnames, CLUSTER_SHEET)
        if sheet is None:
            return _fail(f"Several tabs and none named '{CLUSTER_SHEET}' (tabs: {', '.join(wb.sheetnames)})",
                         "table")
        rows = wb[sheet].iter_rows(values_only=True)
        header = {str(h).strip().lower(): i for i, h in enumerate(next(rows, ())) if h is not None}
        missing = [c for c in CLUSTER_COLUMNS if c not in header]
        if missing:
            return _fail(f"Tab '{sheet}' lacks column(s): {', '.join(missing)}", "table")
        ci = header["clustering_id"]
        counts: dict[str, int] = {}
        for r in rows:
            if ci < len(r) and r[ci] is not None and str(r[ci]).strip():
                k = str(r[ci]).strip()
                counts[k] = counts.get(k, 0) + 1
    finally:
        wb.close()

    names = list(counts)
    if channel in counts:
        return MapInfo(ok=True, kind="table", sheet=sheet, clusterings=names,
                       message=f"Clustering '{channel}' found in tab '{sheet}': "
                               f"{counts[channel]} cluster{'s' if counts[channel] != 1 else ''}")
    shown = ", ".join(names[:8]) + (" …" if len(names) > 8 else "")
    near = next((n for n in names if n.lower() == channel.lower()), None)
    hint = f" (did you mean '{near}'?)" if near else ""
    return MapInfo(ok=False, kind="table", sheet=sheet, clusterings=names,
                   message=f"'{channel}' is not listed in clustering_id{hint}. Available: {shown or 'none'}")
