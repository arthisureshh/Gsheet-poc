import sys, hashlib
from difflib import SequenceMatcher
sys.path.insert(0, '.')
from backend.parsers import parse
from backend.detector import detect_file_type
from backend.region_detector import find_table_regions
from backend.header_inference import infer_headers, build_schema, row_to_text
from backend.models import TableRegion

def parse_tables(path, filename):
    ft = detect_file_type(filename)
    sheets = parse(path, ft)
    file_id = hashlib.md5(filename.encode()).hexdigest()[:8]
    tables = []
    for sheet in sheets:
        if not sheet.rows: continue
        regions = find_table_regions(sheet)
        if not regions:
            regions = [TableRegion(0, len(sheet.rows)-1, 0, max(sheet.max_col-1,0))]
        for ri, region in enumerate(regions):
            table = infer_headers(region, sheet)
            if not table.headers or not table.rows: continue
            schema = build_schema(table, sheet.sheet_name, ri, filename, file_id)
            texts = [t for row in table.rows if (t := row_to_text(row, schema.headers))]
            tables.append((sheet.sheet_name, schema.table_label, texts))
    return tables

v1 = parse_tables('complex_test_data.xlsx', 'complex_test_data.xlsx')
v2 = parse_tables('complex_test_data-v2.xlsx', 'complex_test_data-v2.xlsx')

print(f'V1 tables: {len(v1)}')
for i, (sheet, label, texts) in enumerate(v1):
    print(f'  [{i}] sheet={sheet} label={label} rows={len(texts)}')

print(f'\nV2 tables: {len(v2)}')
for i, (sheet, label, texts) in enumerate(v2):
    print(f'  [{i}] sheet={sheet} label={label} rows={len(texts)}')
