from backend.models import ParsedSheet, SpreadsheetFileType
from backend.parsers.xlsx_parser import parse_xlsx
from backend.parsers.xls_parser import parse_xls
from backend.parsers.dsv_parser import parse_dsv


def parse(file_path: str, file_type: SpreadsheetFileType) -> list[ParsedSheet]:
    if file_type == "xlsx":
        return parse_xlsx(file_path)
    elif file_type == "xls":
        return parse_xls(file_path)
    elif file_type in ("csv", "tsv"):
        return parse_dsv(file_path, file_type)
    raise ValueError(f"Unknown file type: {file_type}")
