"""
Run full pipeline on stress_test.xlsx and output chunks as JSON.

Each chunk object:
{
  "file": "stress_test.xlsx",
  "sheet": "...",
  "region": 0,
  "chunk_index": 0,
  "headers": [...],
  "row_count": 10,
  "rows": [
    {"header1": "value1", "header2": "value2", ...},
    ...
  ]
}

Run:
    python test_stress_pipeline.py
"""
import json
from backend.parsers.xlsx_parser import parse_xlsx
from backend.region_detector import find_table_regions
from backend.header_inference import infer_headers, batch_rows_into_chunks, _get_cell_text


FILE = "stress_test.xlsx"


def row_to_dict(row: list, headers: list[str]) -> dict:
    result = {}
    for i, h in enumerate(headers):
        val = row[i] if i < len(row) else None
        text = _get_cell_text(val)
        if text:
            result[h] = text
    return result


def main():
    sheets = parse_xlsx(FILE)
    all_chunks = []

    for sheet in sheets:
        regions = find_table_regions(sheet)
        for ri, region in enumerate(regions):
            table = infer_headers(region, sheet)
            if not table.rows or not table.headers:
                continue

            # Build row dicts, skip fully empty rows
            row_dicts = [d for row in table.rows if (d := row_to_dict(row, table.headers))]
            if not row_dicts:
                continue

            # Batch into chunks (10 rows / 512 tokens)
            batches: list[list[dict]] = []
            current: list[dict] = []
            current_tokens = 0
            for rd in row_dicts:
                text = ", ".join(f"{k}: {v}" for k, v in rd.items())
                tokens = max(1, len(text) // 4)
                if len(current) >= 10 or (current and current_tokens + tokens > 512):
                    batches.append(current)
                    current = []
                    current_tokens = 0
                current.append(rd)
                current_tokens += tokens
            if current:
                batches.append(current)

            for ci, batch in enumerate(batches):
                all_chunks.append({
                    "file": FILE,
                    "sheet": sheet.sheet_name,
                    "region": ri,
                    "chunk_index": ci,
                    "headers": table.headers,
                    "row_count": len(batch),
                    "rows": batch,
                })

    print(json.dumps(all_chunks, indent=2))


if __name__ == "__main__":
    main()
