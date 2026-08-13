"""
Run full pipeline (region detection → header inference → chunking)
on complex_test_data.xlsx.

Run:
    python test_complex_pipeline.py
"""
from backend.parsers.xlsx_parser import parse_xlsx
from backend.region_detector import find_table_regions
from backend.header_inference import infer_headers, row_to_text, batch_rows_into_chunks

SEP = "=" * 70
SUB = "-" * 50


def run_sheet(sheet):
    print(f"\n{SEP}")
    print(f"SHEET: '{sheet.sheet_name}'  ({len(sheet.rows)} rows, {sheet.max_col} cols)")
    print(SEP)

    regions = find_table_regions(sheet)
    print(f"  Detected {len(regions)} region(s)")
    for i, r in enumerate(regions):
        print(f"    Region {i}: rows {r.start_row}-{r.end_row}, "
              f"cols {r.start_col}-{r.end_col}, label={r.label!r}")

    for region_idx, region in enumerate(regions):
        print(f"\n  {SUB}")
        print(f"  REGION {region_idx}  rows {region.start_row}-{region.end_row} "
              f"cols {region.start_col}-{region.end_col}")
        print(f"  {SUB}")

        table = infer_headers(region, sheet)
        print(f"  Headers ({len(table.headers)}): {table.headers}")
        print(f"  Data rows: {len(table.rows)}")

        if not table.rows:
            print("  [no data rows — skipping chunking]")
            continue

        row_texts = [t for t in (row_to_text(r, table.headers) for r in table.rows) if t]
        chunks = batch_rows_into_chunks(row_texts)

        print(f"  Row texts: {len(row_texts)}")
        print(f"  Chunks   : {len(chunks)}")
        for ci, chunk in enumerate(chunks):
            lines = chunk.split("\n")
            tokens = sum(max(1, len(l) // 4) for l in lines)
            print(f"    Chunk {ci+1}: {len(lines)} rows, ~{tokens} tokens")
            print(f"      first: {lines[0][:100]}{'...' if len(lines[0]) > 100 else ''}")
            if len(lines) > 1:
                print(f"      last : {lines[-1][:100]}{'...' if len(lines[-1]) > 100 else ''}")


def main():
    sheets = parse_xlsx("complex_test_data.xlsx")
    print(f"Parsed {len(sheets)} sheet(s): {[s.sheet_name for s in sheets]}")

    for sheet in sheets:
        run_sheet(sheet)

    print(f"\n{SEP}")
    print("Done.")


if __name__ == "__main__":
    main()
