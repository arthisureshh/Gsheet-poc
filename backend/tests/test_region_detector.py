"""
Phase 1 + Phase 2 region detection tests.

Run with:
    python -m pytest backend/tests/test_region_detector.py -v
"""
import pytest
from backend.models import ParsedSheet, CellGrid
from backend.region_detector import find_table_regions


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_sheet(grid_data: list[list], merged_ranges=None) -> ParsedSheet:
    """
    Build a ParsedSheet from a 2D list.
    None / "" = empty cell, any other value = populated.
    Column positions are preserved exactly.
    """
    row_count = len(grid_data)
    col_count = max((len(r) for r in grid_data), default=0)

    padded = []
    for row in grid_data:
        padded.append(row + [None] * (col_count - len(row)))

    grid = CellGrid(grid=padded, row_count=row_count, col_count=col_count)
    return ParsedSheet(
        sheet_name="Sheet1",
        rows=padded,
        grid=grid,
        merged_ranges=merged_ranges or [],
        freeze_row=0,
        max_col=col_count,
    )


# ── Phase 1 regression tests ──────────────────────────────────────────────────

class TestPhase1Regression:

    def test_single_table(self):
        """Case A — one table, full sheet."""
        sheet = _make_sheet([
            ["ID", "Name", "Age"],
            [1, "Alice", 22],
            [2, "Bob", 21],
        ])
        regions = find_table_regions(sheet)
        assert len(regions) == 1
        assert regions[0].start_row == 0
        assert regions[0].end_row == 2

    def test_vertically_stacked(self):
        """Case B — two tables separated by empty row."""
        sheet = _make_sheet([
            ["ID", "Name", "Age"],
            [1, "Alice", 22],
            [2, "Bob", 21],
            [None, None, None],
            ["Bug ID", "Desc", "Severity"],
            ["BUG-1", "Login fail", "High"],
            ["BUG-2", "Crash", "Medium"],
        ])
        regions = find_table_regions(sheet)
        assert len(regions) == 2

    def test_empty_sheet(self):
        sheet = _make_sheet([])
        assert find_table_regions(sheet) == []

    def test_single_row_ignored(self):
        """Single-row regions are noise — must be filtered."""
        sheet = _make_sheet([
            ["ID", "Name"],
            [1, "Alice"],
            [None, None],
            ["Only one row here"],
        ])
        regions = find_table_regions(sheet)
        assert all(r.end_row > r.start_row for r in regions)

    def test_numeric_headers(self):
        """Test 7 — numeric headers must not break detection."""
        sheet = _make_sheet([
            [2023, 2024, 2025],
            [100, 200, 300],
            [150, 250, 350],
        ])
        regions = find_table_regions(sheet)
        assert len(regions) == 1

    def test_merge_boundary(self):
        """Full-width merged row = table title = boundary."""
        sheet = _make_sheet(
            [
                ["Project Tracker", None, None],
                ["Item", "Owner", "Status"],
                ["Task A", "Alice", "Done"],
                ["Task B", "Bob", "Active"],
            ],
            merged_ranges=[{"min_row": 1, "max_row": 1, "min_col": 1, "max_col": 3}],
        )
        regions = find_table_regions(sheet)
        assert len(regions) == 1
        assert regions[0].label == "Project Tracker"

    def test_original_row_index_preserved(self):
        """Region start_row must reflect original sheet row index."""
        sheet = _make_sheet([
            [None, None],
            ["ID", "Name"],
            [1, "Alice"],
            [2, "Bob"],
        ])
        regions = find_table_regions(sheet)
        assert len(regions) == 1
        assert regions[0].start_row == 1


# ── Phase 2 tests ─────────────────────────────────────────────────────────────

class TestPhase2SideBySide:

    def test_simple_side_by_side(self):
        """Test 1 — two tables side by side with empty column gap."""
        sheet = _make_sheet([
            ["ID", "Name", "Age", None, None, None, None, None, "Emp-id", "Count"],
            [1, "Arthi", 22,    None, None, None, None, None, 202, 6],
            [2, "Suresh", 21,   None, None, None, None, None, 233, 7],
            [3, "Dhyana", 21,   None, None, None, None, None, 105, 8],
            [4, "Dharsha", 22,  None, None, None, None, None, None, None],
            [5, "Keni", 23,     None, None, None, None, None, None, None],
        ])
        regions = find_table_regions(sheet)
        assert len(regions) == 2
        cols = {(r.start_col, r.end_col) for r in regions}
        assert (0, 2) in cols
        assert (8, 9) in cols

    def test_side_by_side_different_heights(self):
        """Test 2 — side-by-side tables with different row counts."""
        sheet = _make_sheet([
            ["ID", "Name", "Age", None, None, "Emp-id", "Count"],
            [1, "Alice", 22,     None, None, 202, 6],
            [2, "Bob", 21,       None, None, 233, 7],
            [3, "Carol", 25,     None, None, 105, 8],
            [4, "Dave", 30,      None, None, None, None],
            [5, "Eve", 28,       None, None, None, None],
        ])
        regions = find_table_regions(sheet)
        assert len(regions) == 2
        cols = {(r.start_col, r.end_col) for r in regions}
        assert (0, 2) in cols
        assert (5, 6) in cols

    def test_vertically_stacked_and_side_by_side(self):
        """Test 3 — 2x2 layout: two rows of two side-by-side tables."""
        sheet = _make_sheet([
            ["ID", "Name", "Age",  None, None, "Emp-id", "Count"],
            [1, "Alice", 22,       None, None, 202, 6],
            [2, "Bob", 21,         None, None, 233, 7],
            [None, None, None,     None, None, None, None],
            ["Code", "Value",      None, None, None, "X", "Y"],
            ["A", 10,              None, None, None, 1, 2],
            ["B", 20,              None, None, None, 3, 4],
        ])
        regions = find_table_regions(sheet)
        assert len(regions) == 4

    def test_empty_column_separator(self):
        """Test 4 — explicit empty column gap between two tables."""
        sheet = _make_sheet([
            ["A", "B", "C", None, None, None, None, None, "I", "J"],
            [1, 2, 3,       None, None, None, None, None, 10, 20],
            [4, 5, 6,       None, None, None, None, None, 30, 40],
        ])
        regions = find_table_regions(sheet)
        assert len(regions) == 2

    def test_no_separator_stays_one_region(self):
        """Test 5 — single table spanning all columns must NOT be split."""
        sheet = _make_sheet([
            ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"],
            [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            [11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
        ])
        regions = find_table_regions(sheet)
        assert len(regions) == 1

    def test_noise_cell_in_separator_does_not_merge(self):
        """Test 6 — one noise cell in gap should not merge two real tables."""
        gap = [None, None, None, None, None]
        noise_gap = [None, "X", None, None, None]
        sheet = _make_sheet([
            ["ID", "Name", "Age"] + gap + ["Emp-id", "Count"],
            [1, "Alice", 22]      + gap + [202, 6],
            [2, "Bob", 21]        + noise_gap + [233, 7],
            [3, "Carol", 25]      + gap + [105, 8],
        ])
        regions = find_table_regions(sheet)
        assert len(regions) == 2

    def test_region_column_bounds_correct(self):
        """Each region must have correct start_col and end_col."""
        sheet = _make_sheet([
            ["ID", "Name", None, None, "Code", "Value"],
            [1, "Alice",   None, None, "A", 10],
            [2, "Bob",     None, None, "B", 20],
        ])
        regions = find_table_regions(sheet)
        assert len(regions) == 2
        r1 = next(r for r in regions if r.start_col == 0)
        r2 = next(r for r in regions if r.start_col > 0)
        assert r1.end_col == 1
        assert r2.start_col == 4
        assert r2.end_col == 5


# ── Header inference respects column bounds ───────────────────────────────────

class TestHeaderInferenceColumnBounds:

    def test_headers_scoped_to_region_columns(self):
        """detectTableStructure must not bleed headers from adjacent table."""
        from backend.header_inference import infer_headers

        sheet = _make_sheet([
            ["ID", "Name", "Age", None, None, "Emp-id", "Count"],
            [1, "Alice", 22,      None, None, 202, 6],
            [2, "Bob", 21,        None, None, 233, 7],
        ])
        regions = find_table_regions(sheet)
        assert len(regions) == 2

        for region in regions:
            table = infer_headers(region, sheet)
            assert len(table.headers) == region.end_col - region.start_col + 1
            if region.start_col == 0:
                assert "Emp-id" not in table.headers
                assert "Count" not in table.headers
            else:
                assert "ID" not in table.headers
                assert "Name" not in table.headers
