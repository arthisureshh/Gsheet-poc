from dataclasses import dataclass, field
from typing import Any, Literal

ColumnType = Literal["number", "date", "category", "text", "url"]
SpreadsheetFileType = Literal["xlsx", "xls", "csv", "tsv"]


@dataclass
class CellMeta:
    """Formatting signals for a single cell."""
    bold: bool = False
    bg_color: str | None = None    # hex RRGGBB or None
    font_color: str | None = None  # hex RRGGBB or None


@dataclass
class MetaGrid:
    """Parallel grid of CellMeta, same dimensions as CellGrid."""
    grid: list[list[CellMeta]]
    row_count: int
    col_count: int

    def get(self, row: int, col: int) -> CellMeta:
        if row >= self.row_count or col >= self.col_count:
            return CellMeta()
        row_data = self.grid[row]
        return row_data[col] if col < len(row_data) else CellMeta()

    def row_is_uniform_color(self, row: int, start_col: int, end_col: int) -> bool:
        """True if >=60% of cells in the row share the same non-None bg_color."""
        cells = [self.get(row, c) for c in range(start_col, end_col + 1)]
        colors = [c.bg_color for c in cells if c.bg_color]
        if not colors:
            return False
        dominant = max(set(colors), key=colors.count)
        return colors.count(dominant) / max(len(cells), 1) >= 0.6

    def row_bold_ratio(self, row: int, start_col: int, end_col: int) -> float:
        """Fraction of cells in row that are bold."""
        cells = [self.get(row, c) for c in range(start_col, end_col + 1)]
        if not cells:
            return 0.0
        return sum(1 for c in cells if c.bold) / len(cells)


@dataclass
class CellGrid:
    """
    2D grid preserving original row + column coordinates.
    grid[row][col] = cell value or None.
    row_count and col_count are the full extents (0-indexed).
    """
    grid: list[list[Any]]   # grid[row_idx][col_idx]
    row_count: int
    col_count: int

    def cell(self, row: int, col: int) -> Any:
        if row >= self.row_count or col >= self.col_count:
            return None
        row_data = self.grid[row]
        return row_data[col] if col < len(row_data) else None

    def is_populated(self, row: int, col: int) -> bool:
        """Mirrors Paxi isCellPopulated: text OR indexedText ([bg=#RRGGBB]) counts."""
        v = self.cell(row, col)
        if v is None:
            return False
        return str(v).strip() != ""

    def col_density(self, col: int, start_row: int, end_row: int) -> float:
        """Fraction of populated cells in a column slice."""
        total = end_row - start_row + 1
        if total <= 0:
            return 0.0
        populated = sum(1 for r in range(start_row, end_row + 1) if self.is_populated(r, col))
        return populated / total

    def row_density(self, row: int, start_col: int, end_col: int) -> float:
        """Fraction of populated cells in a row slice."""
        total = end_col - start_col + 1
        if total <= 0:
            return 0.0
        populated = sum(1 for c in range(start_col, end_col + 1) if self.is_populated(row, c))
        return populated / total

    def slice_rows(self, start_row: int, end_row: int, start_col: int, end_col: int) -> list[list[Any]]:
        """Extract a 2D slice as list of rows, each row sliced to [start_col:end_col+1]."""
        result = []
        for r in range(start_row, end_row + 1):
            row_data = self.grid[r] if r < len(self.grid) else []
            sliced = []
            for c in range(start_col, end_col + 1):
                sliced.append(row_data[c] if c < len(row_data) else None)
            result.append(sliced)
        return result


@dataclass
class TableRegion:
    start_row: int
    end_row: int
    start_col: int
    end_col: int
    label: str | None = None


@dataclass
class DetectedTable:
    header_row_index: int
    headers: list[str]
    rows: list[list[Any]]
    region: TableRegion


@dataclass
class TableSchema:
    table_id: str
    file_id: str          # mirrors Paxi fileId — used in chunk_of = fileId:chunkId
    table_label: str | None
    headers: list[str]
    column_types: dict[str, ColumnType]
    row_count: int
    sample_values: dict[str, list]
    source_range: str
    file_name: str


@dataclass
class ParsedSheet:
    sheet_name: str
    rows: list[list[Any]]           # Phase 1 compat — flat row list
    grid: CellGrid | None = None    # Phase 2 — 2D grid with column positions preserved
    meta_grid: "MetaGrid | None" = None  # formatting signals (bold, bg_color, font_color)
    merged_ranges: list[dict] = field(default_factory=list)
    freeze_row: int = 0
    max_col: int = 0
