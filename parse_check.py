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
    tables = {}
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
            tables[(sheet.sheet_name, ri)] = (schema, texts)
    return tables

v1 = parse_tables('complex_test_data.xlsx', 'complex_test_data.xlsx')
v2 = parse_tables('complex_test_data-v2.xlsx', 'complex_test_data-v2.xlsx')

print(f'V1 keys: {list(v1.keys())}')
print(f'V2 keys: {list(v2.keys())}')

all_keys = sorted(set(v1) | set(v2))
for key in all_keys:
    s1, v1t = v1[key] if key in v1 else (None, [])
    s2, v2t = v2[key] if key in v2 else (None, [])
    schema = s2 or s1
    added = removed = unchanged = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, v1t, v2t, autojunk=False).get_opcodes():
        if tag == 'equal': unchanged += i2-i1
        elif tag == 'delete': removed += i2-i1
        elif tag == 'insert': added += j2-j1
        elif tag == 'replace': removed += i2-i1; added += j2-j1
    if added or removed:
        print(f'\n[{key}] {schema.table_label or "unnamed"}: +{added} added, -{removed} removed, {unchanged} unchanged')
        # Show changed rows
        for tag, i1, i2, j1, j2 in SequenceMatcher(None, v1t, v2t, autojunk=False).get_opcodes():
            if tag == 'delete':
                for t in v1t[i1:i2]: print(f'  REMOVED: {t[:100]}')
            elif tag == 'insert':
                for t in v2t[j1:j2]: print(f'  ADDED:   {t[:100]}')
            elif tag == 'replace':
                for t in v1t[i1:i2]: print(f'  REMOVED: {t[:100]}')
                for t in v2t[j1:j2]: print(f'  ADDED:   {t[:100]}')
    else:
        print(f'[{key}] unchanged ({unchanged} rows)')
