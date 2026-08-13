import xlrd
from backend.models import ParsedSheet

# xlrd default color palette (Excel 8 standard palette, indices 0-63)
# Only the most common indices needed — full palette at:
# https://xlrd.readthedocs.io/en/latest/api.html#xlrd.Book.colour_map
_XLS_PALETTE: dict[int, str] = {
    0:  "000000", 1:  "FFFFFF", 2:  "FF0000", 3:  "00FF00",
    4:  "0000FF", 5:  "FFFF00", 6:  "FF00FF", 7:  "00FFFF",
    8:  "000000", 9:  "FFFFFF", 10: "FF0000", 11: "00FF00",
    12: "0000FF", 13: "FFFF00", 14: "FF00FF", 15: "00FFFF",
    16: "800000", 17: "008000", 18: "000080", 19: "808000",
    20: "800080", 21: "008080", 22: "C0C0C0", 23: "808080",
    24: "9999FF", 25: "993366", 26: "FFFFCC", 27: "CCFFFF",
    28: "660066", 29: "FF8080", 30: "0066CC", 31: "CCCCFF",
    32: "000080", 33: "FF00FF", 34: "FFFF00", 35: "00FFFF",
    36: "800080", 37: "800000", 38: "008080", 39: "0000FF",
    40: "00CCFF", 41: "CCFFFF", 42: "CCFFCC", 43: "FFFF99",
    44: "99CCFF", 45: "FF99CC", 46: "CC99FF", 47: "FFCC99",
    48: "3366FF", 49: "33CCCC", 50: "99CC00", 51: "FFCC00",
    52: "FF9900", 53: "FF6600", 54: "666699", 55: "969696",
    56: "003366", 57: "339966", 58: "003300", 59: "333300",
    60: "993300", 61: "993366", 62: "333399", 63: "333333",
}

# Colors to skip — white/black/default backgrounds that aren't meaningful
_SKIP_COLORS = {"FFFFFF", "000000", "C0C0C0", "808080"}


def _xf_bg_color(wb: xlrd.Book, xf_index: int) -> str | None:
    """Extract background RGB hex from an XF record, return None if not meaningful."""
    try:
        xf = wb.xf_list[xf_index]
        if not xf.background.pattern_colour_index:
            return None
        idx = xf.background.pattern_colour_index
        # Try book's colour_map first (respects custom palettes)
        rgb = wb.colour_map.get(idx)
        if rgb:
            r, g, b = rgb
            hex_color = f"{r:02X}{g:02X}{b:02X}"
        else:
            hex_color = _XLS_PALETTE.get(idx)
        if not hex_color or hex_color in _SKIP_COLORS:
            return None
        return hex_color
    except Exception:
        return None


def parse_xls(file_path: str) -> list[ParsedSheet]:
    wb = xlrd.open_workbook(file_path, formatting_info=True)
    sheets = []
    for ws in wb.sheets():
        rows = []
        for ri in range(ws.nrows):
            row = []
            for ci in range(ws.ncols):
                cell = ws.cell(ri, ci)
                value = cell.value
                # Color-only cell — mirrors xlsx [bg=#RRGGBB] sentinel
                if (value is None or str(value).strip() == "") and cell.xf_index is not None:
                    color = _xf_bg_color(wb, cell.xf_index)
                    if color:
                        value = f"[bg=#{color}]"
                row.append(value)
            rows.append(row)

        sheets.append(ParsedSheet(
            sheet_name=ws.name,
            rows=rows,
            max_col=ws.ncols,
        ))
    return sheets
