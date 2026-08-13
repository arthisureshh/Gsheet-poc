"""
Region detector — Phase 1 + Phase 2.

Phase 1: row-boundary detection (preserved exactly).
Phase 2: extends to 2D by adding column-gap detection and recursive splitting.

Public API (unchanged from Phase 1):
    find_table_regions(sheet) -> list[TableRegion]
    score_header_candidate(rows, idx) -> float
"""
import logging
from backend.models import ParsedSheet, TableRegion, CellGrid

log = logging.getLogger(__name__)

# ── Constants (configurable, no magic numbers scattered in code) ──────────────
MIN_REGION_ROWS = 2          # minimum rows for a valid region
MIN_REGION_COLS = 1          # minimum columns for a valid region
MIN_DENSITY = 0.15           # minimum cell population density to accept a region
COL_GAP_THRESHOLD = 0.15     # column is a gap if density <= this across the row span
COL_GAP_MIN_WIDTH = 1        # minimum consecutive empty columns to form a gap
MAX_RECURSION_DEPTH = 8      # safety limit for recursive splitting


# ── Header scoring (Phase 1 — unchanged) ─────────────────────────────────────

def _score_header_candidate(rows: list[list], idx: int) -> float:
    if idx >= len(rows):
        return 0.0
    row = rows[idx]
    non_empty = [c for c in row if c is not None and str(c).strip()]
    if not non_empty:
        return 0.0

    score = len(non_empty) / max(len(row), 1)

    if all(isinstance(c, str) for c in non_empty):
        score += 0.4

    if idx + 1 < len(rows):
        next_non_empty = [c for c in rows[idx + 1] if c is not None and str(c).strip()]
        if next_non_empty and not all(isinstance(c, str) for c in next_non_empty):
            score += 0.3

    if any(isinstance(c, (int, float)) for c in non_empty):
        score -= 0.3

    return score


def score_header_candidate(rows: list[list], idx: int) -> float:
    return _score_header_candidate(rows, idx)


# ── Phase 1: row boundary detection ──────────────────────────────────────────

def _is_empty_row(row: list) -> bool:
    return all(c is None or str(c).strip() == "" for c in row)


def _find_boundary_rows(sheet: ParsedSheet) -> set[int]:
    boundaries = set()
    rows = sheet.rows

    # Signal 1: full-width merged rows
    for m in sheet.merged_ranges:
        if m["min_col"] == 1 and m["max_col"] >= max(sheet.max_col, 1):
            boundaries.add(m["min_row"] - 1)  # 0-indexed

    # Signal 2: empty rows
    for i, row in enumerate(rows):
        if _is_empty_row(row):
            boundaries.add(i)

    # Signal 3: uniform-color rows (title/header bands without bold or merge)
    if sheet.meta_grid is not None:
        mg = sheet.meta_grid
        max_col = sheet.max_col - 1
        for i, row in enumerate(rows):
            if _is_empty_row(row):
                continue
            non_empty = [c for c in row if c is not None and str(c).strip()]
            # Single-cell row with a distinct bg color = title row boundary
            if len(non_empty) == 1 and mg.row_is_uniform_color(i, 0, max_col):
                boundaries.add(i)

    return boundaries


def _label_above(sheet: ParsedSheet, seg_start: int, boundaries: set[int]) -> str | None:
    """Extract title text from a boundary row just above a segment."""
    if seg_start > 0:
        above = seg_start - 1
        if above in boundaries:
            above_row = sheet.rows[above]
            text = " ".join(str(c) for c in above_row if c is not None and str(c).strip())
            if text:
                return text.strip()
    return None


def _row_segments(sheet: ParsedSheet, boundaries: set[int]) -> list[tuple[int, int]]:
    """Phase 1: split sheet into (start_row, end_row) segments at boundary rows."""
    rows = sheet.rows
    segments: list[tuple[int, int]] = []
    start = None
    for i in range(len(rows)):
        if i in boundaries:
            if start is not None:
                segments.append((start, i - 1))
                start = None
        else:
            if start is None:
                start = i
    if start is not None:
        segments.append((start, len(rows) - 1))
    return [(s, e) for s, e in segments if e - s + 1 >= MIN_REGION_ROWS]


# ── Phase 2: column gap detection ────────────────────────────────────────────

def _find_column_gaps(grid: CellGrid, start_row: int, end_row: int,
                      start_col: int, end_col: int) -> list[tuple[int, int]]:
    """
    Find column ranges that are empty (density <= COL_GAP_THRESHOLD)
    within the given row span. Returns list of (gap_start_col, gap_end_col).
    """
    gaps: list[tuple[int, int]] = []
    gap_start = None

    for c in range(start_col, end_col + 1):
        density = grid.col_density(c, start_row, end_row)
        is_gap = density <= COL_GAP_THRESHOLD
        if is_gap:
            if gap_start is None:
                gap_start = c
        else:
            if gap_start is not None:
                if c - gap_start >= COL_GAP_MIN_WIDTH:
                    gaps.append((gap_start, c - 1))
                gap_start = None

    if gap_start is not None and end_col - gap_start + 1 >= COL_GAP_MIN_WIDTH:
        gaps.append((gap_start, end_col))

    return gaps


def _split_by_column_gaps(grid: CellGrid, start_row: int, end_row: int,
                           start_col: int, end_col: int) -> list[tuple[int, int]]:
    """
    Split [start_col, end_col] into non-gap column bands.
    Returns list of (band_start_col, band_end_col).
    """
    gaps = _find_column_gaps(grid, start_row, end_row, start_col, end_col)
    if not gaps:
        return [(start_col, end_col)]

    bands: list[tuple[int, int]] = []
    cursor = start_col
    for gap_s, gap_e in gaps:
        if gap_s > cursor:
            bands.append((cursor, gap_s - 1))
        cursor = gap_e + 1
    if cursor <= end_col:
        bands.append((cursor, end_col))

    return [b for b in bands if b[1] - b[0] + 1 >= MIN_REGION_COLS]


# ── Phase 2: region density validation ───────────────────────────────────────

def _region_density(grid: CellGrid, start_row: int, end_row: int,
                    start_col: int, end_col: int) -> float:
    total = (end_row - start_row + 1) * (end_col - start_col + 1)
    if total <= 0:
        return 0.0
    populated = sum(
        1 for r in range(start_row, end_row + 1)
        for c in range(start_col, end_col + 1)
        if grid.is_populated(r, c)
    )
    return populated / total


def _is_valid_region(grid: CellGrid, start_row: int, end_row: int,
                     start_col: int, end_col: int) -> bool:
    if end_row - start_row + 1 < MIN_REGION_ROWS:
        return False
    if end_col - start_col + 1 < MIN_REGION_COLS:
        return False
    density = _region_density(grid, start_row, end_row, start_col, end_col)
    if density < MIN_DENSITY:
        log.debug(
            "Rejected candidate region rows=%d-%d cols=%d-%d reason=low_density density=%.2f",
            start_row, end_row, start_col, end_col, density,
        )
        return False
    return True


# ── Phase 2: recursive 2D region splitting ───────────────────────────────────

def _split_region_recursive(
    grid: CellGrid,
    sheet: ParsedSheet,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    boundaries: set[int],
    depth: int = 0,
) -> list[TableRegion]:
    """
    Recursively split a rectangular area into 2D table regions.

    Strategy:
    1. Split by row boundaries (Phase 1 logic) within the row span.
    2. For each row band, split by column gaps (Phase 2).
    3. Validate each resulting rectangle.
    4. Recurse if further splitting is possible.
    """
    if depth > MAX_RECURSION_DEPTH:
        return []

    if not _is_valid_region(grid, start_row, end_row, start_col, end_col):
        return []

    # Step 1: find row boundaries within this span
    local_boundaries = {b for b in boundaries if start_row <= b <= end_row}

    # Build row segments within [start_row, end_row]
    row_segs: list[tuple[int, int]] = []
    cursor = start_row
    for b in sorted(local_boundaries):
        if b > cursor:
            row_segs.append((cursor, b - 1))
        cursor = b + 1
    if cursor <= end_row:
        row_segs.append((cursor, end_row))
    row_segs = [(s, e) for s, e in row_segs if e - s + 1 >= MIN_REGION_ROWS]

    if not row_segs:
        return []

    regions: list[TableRegion] = []

    for rs, re in row_segs:
        # Step 2: split by column gaps within this row band
        col_bands = _split_by_column_gaps(grid, rs, re, start_col, end_col)

        if len(col_bands) == 1 and col_bands[0] == (start_col, end_col):
            # No column split — this is a leaf region
            cs, ce = col_bands[0]
            if _is_valid_region(grid, rs, re, cs, ce):
                label = _label_above(sheet, rs, boundaries)
                log.debug(
                    "Detected region sheet=%s rows=%d-%d cols=%d-%d label=%s",
                    sheet.sheet_name, rs, re, cs, ce, label,
                )
                regions.append(TableRegion(
                    start_row=rs, end_row=re,
                    start_col=cs, end_col=ce,
                    label=label,
                ))
        else:
            # Column split found — recurse into each band
            for cs, ce in col_bands:
                sub = _split_region_recursive(
                    grid, sheet, rs, re, cs, ce, boundaries, depth + 1
                )
                if sub:
                    regions.extend(sub)
                elif _is_valid_region(grid, rs, re, cs, ce):
                    label = _label_above(sheet, rs, boundaries)
                    log.debug(
                        "Detected region sheet=%s rows=%d-%d cols=%d-%d label=%s",
                        sheet.sheet_name, rs, re, cs, ce, label,
                    )
                    regions.append(TableRegion(
                        start_row=rs, end_row=re,
                        start_col=cs, end_col=ce,
                        label=label,
                    ))

    return regions


# ── Public API ────────────────────────────────────────────────────────────────

def find_table_regions(sheet: ParsedSheet) -> list[TableRegion]:
    """
    Phase 1 + Phase 2 region detection.

    If the sheet has a CellGrid (xlsx), uses 2D recursive splitting.
    Falls back to Phase 1 row-only detection for xls/csv/tsv.
    """
    if not sheet.rows:
        return []

    boundaries = _find_boundary_rows(sheet)

    # Phase 2 path — grid available (xlsx)
    if sheet.grid is not None:
        grid = sheet.grid
        regions = _split_region_recursive(
            grid=grid,
            sheet=sheet,
            start_row=0,
            end_row=grid.row_count - 1,
            start_col=0,
            end_col=grid.col_count - 1,
            boundaries=boundaries,
        )
        if regions:
            return regions
        # Fall through to Phase 1 if 2D found nothing

    # Phase 1 fallback — row-only (xls/csv/tsv or empty grid result)
    return _phase1_regions(sheet, boundaries)


def _phase1_regions(sheet: ParsedSheet, boundaries: set[int]) -> list[TableRegion]:
    """Original Phase 1 logic — preserved exactly."""
    rows = sheet.rows
    segments = _row_segments(sheet, boundaries)
    regions = []
    for seg_start, seg_end in segments:
        seg_rows = rows[seg_start: seg_end + 1]
        max_col = max((len(r) for r in seg_rows), default=0)
        if max_col == 0:
            continue
        label = _label_above(sheet, seg_start, boundaries)
        regions.append(TableRegion(
            start_row=seg_start,
            end_row=seg_end,
            start_col=0,
            end_col=max_col - 1,
            label=label,
        ))
    return regions
