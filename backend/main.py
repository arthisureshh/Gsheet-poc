import hashlib
import logging
import os
import tempfile
import traceback
from fastapi import FastAPI, UploadFile, File, Form, HTTPException

logger = logging.getLogger("diff")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.detector import detect_file_type
from backend.parsers import parse
from backend.region_detector import find_table_regions
from backend.header_inference import infer_headers, build_schema
from backend.indexer import ensure_indices, index_schema, index_chunks, _to_json_val, _compute_diff, DiffBlock, _embed_and_store, _get_stored_blocks, _get_next_chunk_id, _delete_chunks
from backend.header_inference import row_to_text
from backend.agents.query_agent import query as agent_query, _fetch_all_schemas

app = FastAPI(title="Spreadsheet Intelligence API")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def startup():
    try:
        ensure_indices()
    except Exception as e:
        print(f"Warning: Could not connect to ES/Qdrant on startup: {e}")


@app.post("/upload")
async def upload_file(file: UploadFile = File(...), base_file_id: str = Form(None)):
    try:
        file_type = detect_file_type(file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # If base_file_id provided (V2 upload), use it so diff looks up V1's stored blocks
    file_id = base_file_id if base_file_id else hashlib.md5(file.filename.encode()).hexdigest()[:8]

    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_type}") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        sheets = parse(tmp_path, file_type)
        all_schemas = []

        for sheet in sheets:
            if not sheet.rows:
                continue
            regions = find_table_regions(sheet)
            if not regions:
                from backend.models import TableRegion
                regions = [TableRegion(0, len(sheet.rows) - 1, 0, max(sheet.max_col - 1, 0))]

            for region_idx, region in enumerate(regions):
                # Stray notes region — index label text directly
                if region.label and region.label.startswith("stray_notes:"):
                    stray_text = region.label[len("stray_notes:"):].strip()
                    from backend.models import TableSchema
                    table_id = f"{file_id}:{sheet.sheet_name}:stray_{region_idx}"
                    schema = TableSchema(
                        table_id=table_id,
                        file_id=file_id,
                        table_label="Stray Notes",
                        headers=["note"],
                        column_types={"note": "text"},
                        row_count=1,
                        sample_values={"note": [stray_text[:50]]},
                        source_range=f"{sheet.sheet_name}!stray_{region_idx}",
                        file_name=file.filename,
                    )
                    index_schema(schema)
                    prev = _get_stored_blocks(table_id)
                    curr = [DiffBlock(text=stray_text)]
                    to_index, to_delete = _compute_diff(prev, curr, _get_next_chunk_id(table_id))
                    _delete_chunks(table_id, to_delete)
                    _embed_and_store(to_index, schema, sheet.sheet_name)
                    all_schemas.append({
                        "table_id": schema.table_id,
                        "table_label": schema.table_label,
                        "sheet_name": sheet.sheet_name,
                        "headers": schema.headers,
                        "column_types": schema.column_types,
                        "row_count": schema.row_count,
                        "sample_values": {"note": [stray_text[:50]]},
                        "source_range": schema.source_range,
                    })
                    continue
                table = infer_headers(region, sheet)
                if not table.rows:
                    continue
                schema = build_schema(table, sheet.sheet_name, region_idx, file.filename, file_id)
                index_schema(schema)
                index_chunks(table, schema)
                all_schemas.append({
                    "table_id": schema.table_id,
                    "table_label": schema.table_label,
                    "sheet_name": sheet.sheet_name,
                    "headers": schema.headers,
                    "column_types": schema.column_types,
                    "row_count": schema.row_count,
                    "sample_values": {k: [_to_json_val(v) for v in vals] for k, vals in schema.sample_values.items()},
                    "source_range": schema.source_range,
                })

        return {"file_id": file_id, "file_name": file.filename, "tables": all_schemas}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


class QueryRequest(BaseModel):
    query: str
    file_name: str | None = None


@app.post("/query")
def query_endpoint(req: QueryRequest):
    try:
        result = agent_query(req.query, req.file_name)
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/schemas")
def list_schemas(file_name: str | None = None):
    return _fetch_all_schemas(file_name)


@app.get("/chunks")
def get_chunks(table_id: str):
    from backend.indexer import _es, ES_CHUNK_INDEX
    res = _es().search(
        index=ES_CHUNK_INDEX,
        query={"term": {"table_id": table_id}},
        size=500,
        sort=[{"chunk_id": "asc"}],
    )
    return [{"chunk_index": h["_source"].get("chunk_id", i), **h["_source"]} for i, h in enumerate(res["hits"]["hits"])]


def _parse_file_to_tables(tmp_path: str, file_type: str, filename: str, file_id: str) -> list:
    """
    Returns list of (sheet_name, headers_key, schema, row_texts) per detected table.
    headers_key = frozenset of normalized header names — used for cross-version matching.
    """
    sheets = parse(tmp_path, file_type)
    tables = []
    for sheet in sheets:
        if not sheet.rows:
            continue
        regions = find_table_regions(sheet)
        if not regions:
            from backend.models import TableRegion
            regions = [TableRegion(0, len(sheet.rows) - 1, 0, max(sheet.max_col - 1, 0))]
        for region_idx, region in enumerate(regions):
            table = infer_headers(region, sheet)
            if not table.rows:
                continue
            schema = build_schema(table, sheet.sheet_name, region_idx, filename, file_id)
            texts = [tx for row in table.rows if (tx := row_to_text(row, schema.headers))]
            if not texts:
                continue
            headers_key = frozenset(schema.headers)
            tables.append((sheet.sheet_name, headers_key, schema, texts))
    return tables


def _match_tables(v1_tables: list, v2_tables: list) -> list[tuple]:
    """
    Match V1 tables to V2 tables by (sheet_name, headers_key).
    Returns list of (v1_texts, v2_texts, schema) pairs.
    """
    from collections import defaultdict
    # Group by (sheet_name, headers_key)
    def group(tables):
        d = defaultdict(list)
        for sheet_name, hkey, schema, texts in tables:
            d[(sheet_name, hkey)].append((schema, texts))
        return d

    g1 = group(v1_tables)
    g2 = group(v2_tables)
    all_keys = sorted(set(g1) | set(g2), key=lambda k: k[0])

    pairs = []
    for key in all_keys:
        entries1 = g1.get(key, [])
        entries2 = g2.get(key, [])
        # Zip by position within same sheet+headers group
        max_len = max(len(entries1), len(entries2))
        for i in range(max_len):
            s1, t1 = entries1[i] if i < len(entries1) else (None, [])
            s2, t2 = entries2[i] if i < len(entries2) else (None, [])
            schema = s2 or s1
            pairs.append((t1, t2, schema))
    return pairs


@app.post("/diff")
async def diff_files(v1: UploadFile = File(...), v2: UploadFile = File(...)):
    tmp_paths = []
    try:
        parsed = {}
        for upload, label in [(v1, "v1"), (v2, "v2")]:
            try:
                file_type = detect_file_type(upload.filename)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            file_id = hashlib.md5(upload.filename.encode()).hexdigest()[:8]
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_type}") as tmp:
                tmp.write(await upload.read())
                tmp_paths.append(tmp.name)
                parsed[label] = _parse_file_to_tables(tmp.name, file_type, upload.filename, file_id)

        from difflib import SequenceMatcher
        results = []
        pairs = _match_tables(parsed["v1"], parsed["v2"])

        logger.info("=" * 60)
        logger.info("DIFF: %s  vs  %s", v1.filename, v2.filename)
        logger.info("Tables matched: %d", len(pairs))
        logger.info("=" * 60)

        for v1_texts, v2_texts, schema in pairs:
            label = schema.table_label or schema.source_range.split("!")[0]
            diff_rows = []
            for tag, i1, i2, j1, j2 in SequenceMatcher(None, v1_texts, v2_texts, autojunk=False).get_opcodes():
                if tag == "equal":
                    for t in v1_texts[i1:i2]:
                        diff_rows.append({"status": "unchanged", "text": t})
                elif tag == "delete":
                    for t in v1_texts[i1:i2]:
                        diff_rows.append({"status": "removed", "text": t})
                elif tag == "insert":
                    for t in v2_texts[j1:j2]:
                        diff_rows.append({"status": "added", "text": t})
                elif tag == "replace":
                    for t in v1_texts[i1:i2]:
                        diff_rows.append({"status": "removed", "text": t})
                    for t in v2_texts[j1:j2]:
                        diff_rows.append({"status": "added", "text": t})

            added     = sum(1 for r in diff_rows if r["status"] == "added")
            removed   = sum(1 for r in diff_rows if r["status"] == "removed")
            unchanged = sum(1 for r in diff_rows if r["status"] == "unchanged")

            logger.info("  [%s] +%d added  -%d removed  %d unchanged",
                        label, added, removed, unchanged)
            for r in diff_rows:
                if r["status"] == "added":
                    logger.info("    + %s", r["text"][:120])
                elif r["status"] == "removed":
                    logger.info("    - %s", r["text"][:120])

            if added == 0 and removed == 0:
                continue

            results.append({
                "table_label": label,
                "sheet_name":  schema.source_range.split("!")[0],
                "headers":     schema.headers,
                "added":       added,
                "removed":     removed,
                "unchanged":   unchanged,
                "rows":        diff_rows,
            })

        logger.info("=" * 60)
        logger.info("DIFF COMPLETE: %d table(s) changed", len(results))
        logger.info("=" * 60)
        return {"tables": results}
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        for p in tmp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass


# Serve frontend
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
