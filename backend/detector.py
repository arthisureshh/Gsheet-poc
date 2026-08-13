from pathlib import Path
from backend.models import SpreadsheetFileType


def detect_file_type(filename: str) -> SpreadsheetFileType:
    ext = Path(filename).suffix.lower()
    mapping = {".xlsx": "xlsx", ".xls": "xls", ".csv": "csv", ".tsv": "tsv"}
    result = mapping.get(ext)
    if not result:
        raise ValueError(f"Unsupported format: {ext}")
    return result
