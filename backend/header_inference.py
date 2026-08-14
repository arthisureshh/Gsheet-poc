"""
Header inference — aligned with TypeScript chunking pipeline.

Ports:
  detectTableStructure  → infer_headers()
  scoreHeaderCandidate  → _score_header_candidate()
  selectHeaderRows      → _select_header_rows()
  buildHeaders          → _build_headers()
  normalizeHeaders      → _normalize_header()
"""
import re
from datetime import datetime
from backend.models import ParsedSheet, TableRegion, DetectedTable, TableSchema, ColumnType
from backend.region_detector import score_header_candidate as _region_score  # re-exported below

_DATE_PATTERNS = [
    re.compile(r"^\d{4}-\d{2}-\d{2}"),
    re.compile(r"^\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}"),
]
_URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)

# Keywords that strongly indicate a header row (port of TS keyword list)
_HEADER_KEYWORDS = {
    "status", "owner", "date", "sprint", "name", "title", "type", "id",
    "description", "priority", "assignee", "due", "created", "updated",
    "category", "label", "tag", "project", "task", "item", "count",
    "total", "amount", "budget", "cost", "score", "rank", "level",
}

# Scoring thresholds (port of TS constants)
_HEADER_SCORE_THRESHOLD = 8   # relaxed from TS 15 per Phase 1 spec
_MAX_CHUNK_ROWS = 10
_MAX_CHUNK_TOKENS = 512


# ── Type inference ────────────────────────────────────────────────────────────

def _infer_column_type(values: list) -> ColumnType:
    non_null = [v for v in values if v is not None and str(v).strip() != ""]
    if not non_null:
        return "text"
    if all(isinstance(v, datetime) for v in non_null):
        return "date"
    if all(isinstance(v, bool) for v in non_null):
        return "category"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null):
        return "number"
    strs = [str(v).strip() for v in non_null]
    if all(any(p.match(s) for p in _DATE_PATTERNS) for s in strs):
        return "date"
    if all(_URL_PATTERN.match(s) for s in strs):
        return "url"
    def _is_numeric(s: str) -> bool:
        try:
            float(s.replace(",", ""))
            return True
        except ValueError:
            return False
    if all(_is_numeric(s) for s in strs):
        return "number"
    if len(set(s.lower() for s in strs)) <= max(10, len(strs) // 5):
        return "category"
    return "text"


# ── Header scoring (port of TS scoreHeaderCandidate) ─────────────────────────

def _looks_like_date_band(row: list) -> bool:
    """True if ≥60% of non-empty cells look like dates."""
    non_empty = [c for c in row if c is not None and str(c).strip()]
    if not non_empty:
        return False
    date_count = sum(
        1 for c in non_empty
        if isinstance(c, datetime) or any(p.match(str(c).strip()) for p in _DATE_PATTERNS)
    )
    return date_count / len(non_empty) >= 0.6


def _score_header_candidate(rows: list[list], idx: int) -> float:
    """Port of TS scoreHeaderCandidate with all 6 scoring rules."""
    if idx >= len(rows):
        return 0.0
    row = rows[idx]
    non_empty = [c for c in row if c is not None and str(c).strip()]
    if not non_empty:
        return 0.0

    score = 0.0

    # +2 per text cell (non-numeric), +1 per unique value
    unique_vals = set()
    for c in non_empty:
        s = str(c).strip()
        if not isinstance(c, (int, float)) or isinstance(c, bool):
            score += 2
        score += 1
        unique_vals.add(s.lower())

    # +5 if average cell length < 30
    avg_len = sum(len(str(c).strip()) for c in non_empty) / len(non_empty)
    if avg_len < 30:
        score += 5

    # -10 if only 1 cell and it's long (title row penalty)
    if len(non_empty) == 1 and avg_len > 30:
        score -= 10

    # -8 if ≥60% of cells look like dates AND no keyword cells present
    if _looks_like_date_band(row):
        has_keyword = any(
            re.sub(r"[^a-z0-9]", "", str(c).lower()) in _HEADER_KEYWORDS
            for c in non_empty
        )
        if not has_keyword:
            score -= 8

    # +6 per keyword match
    for c in non_empty:
        word = re.sub(r"[^a-z0-9]", "", str(c).lower())
        if word in _HEADER_KEYWORDS:
            score += 6

    # +up to 10 if following rows have similar column widths (consistency bonus)
    if idx + 1 < len(rows):
        next_rows = rows[idx + 1: idx + 4]
        if next_rows:
            base_len = len(non_empty)
            consistency = sum(
                1 for r in next_rows
                if abs(len([c for c in r if c is not None and str(c).strip()]) - base_len) <= 1
            )
            score += min(10, consistency * 3)

    return score


def score_header_candidate(rows: list[list], idx: int) -> float:
    """Public re-export used by region_detector."""
    return _score_header_candidate(rows, idx)


# ── Multi-row header detection (port of TS selectHeaderRows) ─────────────────

def _looks_header_like(row: list) -> bool:
    non_empty = [c for c in row if c is not None and str(c).strip()]
    if not non_empty:
        return False
    # Color sentinels are data values, not headers
    non_sentinel = [c for c in non_empty if not str(c).startswith("[bg=#")]
    if not non_sentinel:
        return False
    return all(not isinstance(c, (int, float)) or isinstance(c, bool) for c in non_sentinel)


def _select_header_rows(rows: list[list], header_idx: int) -> tuple[list[list], int]:
    """
    Port of TS selectHeaderRows.
    Returns (header_row_list, data_start_index).
    Detects multi-row headers: if base header has duplicates (merged spans)
    AND next row looks like sub-headers, both rows form the header.
    """
    base = rows[header_idx]
    base_normalized = [str(c).strip().lower() if c is not None else "" for c in base]

    # Check for duplicate column names (merged cell spans)
    non_empty_base = [v for v in base_normalized if v]
    duplicate_count = len(non_empty_base) - len(set(non_empty_base))
    if header_idx + 1 < len(rows) and duplicate_count > 0:
        next_row = rows[header_idx + 1]
        if _looks_header_like(next_row):
            return [base, next_row], header_idx + 2

    return [base], header_idx + 1


# ── Header building (port of TS buildHeaders + normalizeHeaders) ──────────────

def _normalize_header(name: str) -> str:
    """Port of TS normalizeHeaders — snake_case, alphanumeric only."""
    # Strip color sentinels like [bg=#FF0000] from header names
    name = re.sub(r"\[bg=#[0-9A-Fa-f]+\]", "", name)
    return re.sub(r"[^a-z0-9_]", "", name.strip().lower().replace(" ", "_").replace("-", "_"))


def _build_headers(header_rows: list[list], max_cols: int) -> tuple[list[str], list[str]]:
    """
    Port of TS buildHeaders.
    Returns (display_names, normalized_keys).
    - Forward-fills empty cells for merged spans
    - Joins multi-row headers with ' | '
    - Normalizes to snake_case keys
    - Deduplicates with _1, _2 suffix
    """
    levels: list[list[str]] = []
    for row in header_rows:
        # Forward-fill empty cells (handles merged cell spans)
        filled: list[str] = []
        carry = ""
        for i in range(max_cols):
            val = row[i] if i < len(row) else None
            # Convert datetime header values to short readable strings (e.g. 02_jan)
            if isinstance(val, datetime):
                s = f"{val.day:02d}_{val.strftime('%b').lower()}"
            else:
                s = str(val).strip() if val is not None else ""
            # Strip color sentinels from header cells
            s = re.sub(r"\[bg=#[0-9A-Fa-f]+\]", "", s).strip()
            if s:
                carry = s
            else:
                s = carry  # forward-fill
            filled.append(s)
        levels.append(filled)

    display_names: list[str] = []
    normalized_keys: list[str] = []
    seen: dict[str, int] = {}

    for col_idx in range(max_cols):
        # Join multi-row header levels with ' | '
        parts = [levels[lvl][col_idx] for lvl in range(len(levels)) if levels[lvl][col_idx]]
        display = " | ".join(parts) if parts else f"col_{col_idx}"
        key = _normalize_header(display) or f"col_{col_idx}"

        # Deduplicate
        if key in seen:
            seen[key] += 1
            key = f"{key}_{seen[key]}"
        else:
            seen[key] = 0

        display_names.append(display)
        normalized_keys.append(key)

    return display_names, normalized_keys


# ── Main header inference (port of TS detectTableStructure) ──────────────────

def infer_headers(region: TableRegion, sheet: ParsedSheet) -> DetectedTable:
    # Phase 2: slice to column bounds if grid available
    if sheet.grid is not None:
        rows = sheet.grid.slice_rows(
            region.start_row, region.end_row,
            region.start_col, region.end_col,
        )
    else:
        rows = sheet.rows[region.start_row: region.end_row + 1]

    if not rows:
        return DetectedTable(header_row_index=0, headers=[], rows=[], region=region)

    # Build per-row formatting bonus from MetaGrid
    meta_bonus: list[float] = [0.0] * len(rows)
    if sheet.meta_grid is not None:
        mg = sheet.meta_grid
        sc, ec = region.start_col, region.end_col
        for local_i in range(len(rows)):
            sheet_row = region.start_row + local_i
            bold_ratio = mg.row_bold_ratio(sheet_row, sc, ec)
            has_color = mg.row_is_uniform_color(sheet_row, sc, ec)
            meta_bonus[local_i] = bold_ratio * 8 + (5 if has_color else 0)

    # Path A — frozen row heuristic
    header_idx = None
    if sheet.freeze_row > 0 and region.start_row == 0:
        candidate = min(sheet.freeze_row - 1, len(rows) - 1)
        base = _score_header_candidate(rows, candidate)
        if base + meta_bonus[candidate] >= _HEADER_SCORE_THRESHOLD:
            header_idx = candidate

    # Path B — scoring fallback (content score + formatting bonus)
    if header_idx is None:
        header_idx = max(
            range(len(rows)),
            key=lambda i: _score_header_candidate(rows, i) + meta_bonus[i]
        )

    # Multi-row header detection
    header_row_list, data_start = _select_header_rows(rows, header_idx)
    max_cols = region.end_col - region.start_col + 1
    display_names, normalized_keys = _build_headers(header_row_list, max_cols)

    data_rows = rows[data_start:]
    # Stray/micro region: if header consumed all rows, treat header row as data
    if not data_rows and header_row_list:
        data_rows = header_row_list[:1]
        header_row_list = []
        display_names = [f"col_{i}" for i in range(max_cols)]
        normalized_keys = display_names
    return DetectedTable(
        header_row_index=header_idx,
        headers=normalized_keys,
        rows=data_rows,
        region=region,
    )


# ── Row text conversion (port of TS chunkTableContentToMetadata) ──────────────

def _get_cell_text(value) -> str:
    """
    Port of TS getCellText -> toStructureText.
    Color-only sentinel -> '[formatted]' (matches Paxi toStructureText exactly).
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        if value == int(value):
            return str(int(value))
        return str(value)
    s = str(value).strip()
    # Paxi indexedText format: [bg=#RRGGBB] -> return as-is (mirrors indexedText in chunk)
    if s.startswith("[bg=#") and s.endswith("]"):
        return s
    return s


def row_to_text(row: list, headers: list[str]) -> str:
    """
    Port of TS chunkTableContentToMetadata row conversion.
    Produces: 'header: value, header: value, ...'
    Empty cells are skipped entirely.
    """
    parts = []
    for col_idx, header in enumerate(headers):
        val = row[col_idx] if col_idx < len(row) else None
        text = _get_cell_text(val)
        if text:
            parts.append(f"{header}: {text}")
    return ", ".join(parts)


# ── Chunking (port of TS batchRowsIntoChunks) ────────────────────────────────

def _count_tokens(text: str) -> int:
    """Approximate token count — 1 token ≈ 4 chars (cl100k_base approximation)."""
    return max(1, len(text) // 4)


def batch_rows_into_chunks(row_texts: list[str]) -> list[str]:
    """
    Port of TS batchRowsIntoChunks.
    Groups row text strings into chunks respecting:
      - MAX_ROWS_PER_CHUNK = 10
      - MAX_CHUNK_TOKENS = 512
    A single oversized row becomes its own chunk (no truncation).
    Returns list of chunk text strings (rows joined with newline).
    """
    chunks: list[str] = []
    current_batch: list[str] = []
    current_tokens = 0

    for row_text in row_texts:
        row_tokens = _count_tokens(row_text)
        would_exceed_rows = len(current_batch) >= _MAX_CHUNK_ROWS
        would_exceed_tokens = len(current_batch) > 0 and current_tokens + row_tokens > _MAX_CHUNK_TOKENS

        if would_exceed_rows or would_exceed_tokens:
            chunks.append("\n".join(current_batch))
            current_batch = []
            current_tokens = 0

        current_batch.append(row_text)
        current_tokens += row_tokens

    if current_batch:
        chunks.append("\n".join(current_batch))

    return chunks


# ── Schema builder ────────────────────────────────────────────────────────────

def build_schema(
    table: DetectedTable,
    sheet_name: str,
    region_index: int,
    file_name: str,
    file_id: str,
) -> TableSchema:
    headers = table.headers
    rows = table.rows

    col_values: dict[str, list] = {h: [] for h in headers}
    for row in rows:
        for i, h in enumerate(headers):
            val = row[i] if i < len(row) else None
            col_values[h].append(val)

    column_types: dict[str, ColumnType] = {h: _infer_column_type(col_values[h]) for h in headers}
    sample_values = {h: [v for v in col_values[h] if v is not None][:3] for h in headers}

    start = table.region.start_row + table.header_row_index
    end = table.region.end_row
    col_start = table.region.start_col + 1
    col_end = table.region.end_col + 1
    source_range = f"{sheet_name}!R{start + 1}C{col_start}:R{end + 1}C{col_end}"

    return TableSchema(
        table_id=f"{file_id}:{sheet_name}:{region_index}",
        file_id=file_id,
        table_label=table.region.label,
        headers=headers,
        column_types=column_types,
        row_count=len(rows),
        sample_values=sample_values,
        source_range=source_range,
        file_name=file_name,
    )
