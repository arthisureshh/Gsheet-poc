import pandas as pd
from backend.models import ParsedSheet


def parse_dsv(file_path: str, file_type: str) -> list[ParsedSheet]:
    sep = "\t" if file_type == "tsv" else ","
    df = pd.read_csv(file_path, sep=sep, dtype=str, encoding_errors="replace")
    rows = [list(df.columns)] + df.values.tolist()
    return [ParsedSheet(sheet_name="Sheet1", rows=rows, max_col=len(df.columns))]
