"""
Test the full chunking pipeline on test_chunking.csv.

Shows output of each step:
  Step 1 — detectTableStructure (header inference)
  Step 2 — data rows slice
  Step 3 — row_to_text (chunkTableContentToMetadata)
  Step 4 — batch_rows_into_chunks

Run:
    python test_chunking_pipeline.py
"""
from backend.parsers.dsv_parser import parse_dsv
from backend.region_detector import find_table_regions
from backend.header_inference import infer_headers, row_to_text, batch_rows_into_chunks
from backend.models import TableRegion

SEP = "-" * 70


def main():
    # ── Parse ──────────────────────────────────────────────────────────────
    sheets = parse_dsv("test_chunking.csv", "csv")
    sheet = sheets[0]
    print(f"Parsed sheet: '{sheet.sheet_name}'")
    print(f"Total rows (including header): {len(sheet.rows)}")
    print(SEP)

    # ── Step 1: detectTableStructure ───────────────────────────────────────
    regions = find_table_regions(sheet)
    if not regions:
        # CSV has no merge signals — treat whole sheet as one region
        regions = [TableRegion(0, len(sheet.rows) - 1, 0, sheet.max_col - 1)]

    print(f"Detected {len(regions)} region(s)")
    for i, region in enumerate(regions):
        print(f"  Region {i}: rows {region.start_row}-{region.end_row}, "
              f"cols {region.start_col}-{region.end_col}, label={region.label}")
    print(SEP)

    for region_idx, region in enumerate(regions):
        table = infer_headers(region, sheet)

        print(f"STEP 1 — detectTableStructure (region {region_idx})")
        print(f"  headerIndex    : {table.header_row_index}")
        print(f"  dataStartIndex : {region.start_row + table.header_row_index + 1}")
        print(f"  headers        : {table.headers}")
        print(f"  data rows      : {len(table.rows)}")
        print(SEP)

        # ── Step 2: slice data rows ────────────────────────────────────────
        data_rows = table.rows
        print(f"STEP 2 — data rows slice (first 3 shown)")
        for row in data_rows[:3]:
            print(f"  {row}")
        print(f"  ... ({len(data_rows)} total rows)")
        print(SEP)

        # ── Step 3: row_to_text ────────────────────────────────────────────
        print(f"STEP 3 — row_to_text / chunkTableContentToMetadata (first 3 shown)")
        row_texts = []
        for row in data_rows:
            text = row_to_text(row, table.headers)
            if text:
                row_texts.append(text)

        for t in row_texts[:3]:
            print(f"  {t}")
        print(f"  ... ({len(row_texts)} total row texts)")
        print(SEP)

        # ── Step 4: batchRowsIntoChunks ────────────────────────────────────
        chunks = batch_rows_into_chunks(row_texts)
        print(f"STEP 4 — batchRowsIntoChunks")
        print(f"  Total chunks   : {len(chunks)}")
        for i, chunk in enumerate(chunks):
            lines = chunk.split("\n")
            token_est = sum(max(1, len(l) // 4) for l in lines)
            print(f"\n  Chunk {i + 1} — {len(lines)} rows, ~{token_est} tokens")
            print(f"  First row : {lines[0]}")
            if len(lines) > 1:
                print(f"  Last row  : {lines[-1]}")
        print(SEP)


if __name__ == "__main__":
    main()
