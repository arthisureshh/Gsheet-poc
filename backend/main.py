import hashlib
import os
import tempfile
import traceback
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.detector import detect_file_type
from backend.parsers import parse
from backend.region_detector import find_table_regions
from backend.header_inference import infer_headers, build_schema
from backend.indexer import ensure_indices, index_schema, index_chunks, _to_json_val
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
async def upload_file(file: UploadFile = File(...)):
    try:
        file_type = detect_file_type(file.filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    file_id = hashlib.md5(file.filename.encode()).hexdigest()[:8]

    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_type}") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        sheets = parse(tmp_path, file_type)
        all_schemas = []

        for sheet in sheets:
            if not sheet.rows:  # #10 empty sheet guard
                continue
            regions = find_table_regions(sheet)
            if not regions:
                from backend.models import TableRegion
                regions = [TableRegion(0, len(sheet.rows) - 1, 0, max(sheet.max_col - 1, 0))]

            for region_idx, region in enumerate(regions):
                table = infer_headers(region, sheet)
                if not table.headers or not table.rows:  # #10 skip empty tables
                    continue
                schema = build_schema(table, sheet.sheet_name, region_idx, file.filename, file_id)
                index_schema(schema)
                index_chunks(table, schema, incremental=False)
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
        query={"term": {"table_id.keyword": table_id}},
        size=500,
        sort=[{"row_index": "asc"}],
    )
    return [{"chunk_index": i, **h["_source"]} for i, h in enumerate(res["hits"]["hits"])]


# Serve frontend
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
