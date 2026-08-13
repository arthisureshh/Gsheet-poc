import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from elasticsearch import Elasticsearch
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
from sentence_transformers import SentenceTransformer
from backend.models import TableSchema, DetectedTable
from backend.header_inference import row_to_text

ES_SCHEMA_INDEX   = "spreadsheet_schemas"
ES_CHUNK_INDEX    = "spreadsheet_chunks"
QDRANT_COLLECTION = "spreadsheet_vectors"
VECTOR_DIM  = 384
BATCH_SIZE  = 32

_model: SentenceTransformer | None = None


def _embed_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def _es() -> Elasticsearch:
    return Elasticsearch("http://localhost:9200")


def _qdrant() -> QdrantClient:
    return QdrantClient(host="localhost", port=6333)


# ── ES / Qdrant setup ─────────────────────────────────────────────────────────

_INDEX_SETTINGS = {"index.mapping.total_fields.limit": 2000}

_SCHEMA_MAPPINGS = {"properties": {
    "table_id":     {"type": "keyword"},
    "file_name":    {"type": "keyword"},
    "file_id":      {"type": "keyword"},
    "table_label":  {"type": "text"},
    "headers":      {"type": "keyword"},
    "column_types": {"enabled": False},
    "row_count":    {"type": "integer"},
    "sample_values":{"enabled": False},
    "source_range": {"type": "keyword"},
}}

_CHUNK_MAPPINGS = {"properties": {
    "table_id":    {"type": "keyword"},
    "file_name":   {"type": "keyword"},
    "file_id":     {"type": "keyword"},
    "sheet_name":  {"type": "keyword"},
    "region_index":{"type": "integer"},
    "chunk_id":    {"type": "integer"},   # sequential int, mirrors TS chunk_id counter
    "chunk_text":  {"type": "keyword"},   # keyword for exact fetch
    "chunk_text_search": {"type": "text"}, # copy for full-text search
}}


def _table_id_is_keyword(es: Elasticsearch, index: str) -> bool:
    try:
        m = es.indices.get_mapping(index=index)
        props = m[index]["mappings"].get("properties", {})
        return props.get("table_id", {}).get("type") == "keyword"
    except Exception:
        return False


def ensure_indices():
    es = _es()
    for index, mappings in [(ES_SCHEMA_INDEX, _SCHEMA_MAPPINGS), (ES_CHUNK_INDEX, _CHUNK_MAPPINGS)]:
        # Always delete and recreate to guarantee correct keyword mappings
        # ES auto-mapping from first bulk write overrides explicit mappings if index pre-exists
        if es.indices.exists(index=index):
            if _table_id_is_keyword(es, index):
                continue  # already correct, skip
            es.indices.delete(index=index)
        es.indices.create(index=index, mappings=mappings, settings=_INDEX_SETTINGS)

    qd = _qdrant()
    existing = [c.name for c in qd.get_collections().collections]
    if QDRANT_COLLECTION not in existing:
        qd.create_collection(QDRANT_COLLECTION, vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE))


def _to_json_val(v) -> str:
    if isinstance(v, __import__('datetime').datetime):
        return v.date().isoformat()
    return str(v)


# ── Schema index ──────────────────────────────────────────────────────────────

def index_schema(schema: TableSchema):
    doc = {
        "table_id":     schema.table_id,
        "file_name":    schema.file_name,
        "file_id":      schema.file_id,
        "table_label":  schema.table_label,
        "headers":      schema.headers,
        "column_types": schema.column_types,
        "row_count":    schema.row_count,
        "sample_values":{k: [_to_json_val(v) for v in vals] for k, vals in schema.sample_values.items()},
        "source_range": schema.source_range,
    }
    _es().index(index=ES_SCHEMA_INDEX, id=schema.table_id, document=doc)


# ── DiffBlock — mirrors TS DiffBlock ─────────────────────────────────────────

@dataclass
class DiffBlock:
    text: str
    chunk_id: int | None = None   # None for current (not yet indexed) blocks


# ── compute_diff — mirrors TS computeDiff / diffArrays ───────────────────────

def _compute_diff(
    previous: list[DiffBlock],
    current: list[DiffBlock],
    starting_chunk_id: int,
) -> tuple[list[tuple[int, str]], list[int]]:
    """
    Positional diff using SequenceMatcher (mirrors TS diffArrays).
    Returns:
      to_index: [(chunk_id, chunk_text), ...]  — new/changed chunks to embed
      to_delete: [chunk_id, ...]               — removed chunk_ids to delete
    """
    prev_texts = [b.text.strip() for b in previous]
    curr_texts = [b.text.strip() for b in current]

    matcher = SequenceMatcher(None, prev_texts, curr_texts, autojunk=False)

    to_index: list[tuple[int, str]] = []
    to_delete: list[int] = []
    next_chunk_id = starting_chunk_id

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        elif tag == "delete":
            for block in previous[i1:i2]:
                if block.chunk_id is not None:
                    to_delete.append(block.chunk_id)

        elif tag == "insert":
            for block in current[j1:j2]:
                to_index.append((next_chunk_id, block.text))
                next_chunk_id += 1

        elif tag == "replace":
            for block in previous[i1:i2]:
                if block.chunk_id is not None:
                    to_delete.append(block.chunk_id)
            for block in current[j1:j2]:
                to_index.append((next_chunk_id, block.text))
                next_chunk_id += 1

    return to_index, to_delete


# ── Fetch stored chunks for a table ──────────────────────────────────────────

def _get_stored_blocks(table_id: str) -> list[DiffBlock]:
    """
    Returns previous DiffBlocks ordered by chunk_id.
    chunk_id is the sequential int stored in ES.
    """
    try:
        res = _es().search(
            index=ES_CHUNK_INDEX,
            query={"term": {"table_id": str(table_id)}},
            _source=["chunk_id", "chunk_text"],
            size=10000,
            sort=[{"chunk_id": "asc"}],
        )
        return [
            DiffBlock(text=h["_source"]["chunk_text"], chunk_id=int(h["_source"]["chunk_id"]))
            for h in res["hits"]["hits"]
        ]
    except Exception:
        import traceback; traceback.print_exc()
        return []


def _get_next_chunk_id(table_id: str) -> int:
    """Returns max(chunk_id) + 1 for this table, or 0 if none stored."""
    try:
        res = _es().search(
            index=ES_CHUNK_INDEX,
            query={"term": {"table_id": str(table_id)}},
            aggs={"max_id": {"max": {"field": "chunk_id"}}},
            size=0,
        )
        val = res["aggregations"]["max_id"]["value"]
        return int(val) + 1 if val is not None else 0
    except Exception:
        import traceback; traceback.print_exc()
        return 0


def _ensure_qdrant_collection():
    """Recreate Qdrant collection if missing — called before any read/write."""
    qd = _qdrant()
    existing = [c.name for c in qd.get_collections().collections]
    if QDRANT_COLLECTION not in existing:
        qd.create_collection(QDRANT_COLLECTION, vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE))


# ── Delete chunks by chunk_id ─────────────────────────────────────────────────

def _delete_chunks(table_id: str, chunk_ids: list[int]):
    if not chunk_ids:
        return
    _ensure_qdrant_collection()
    # ES: delete all matching chunk_ids in one query
    _es().delete_by_query(
        index=ES_CHUNK_INDEX,
        query={"bool": {"must": [
            {"term": {"table_id": str(table_id)}},
            {"terms": {"chunk_id": [int(c) for c in chunk_ids]}},
        ]}},
        refresh=True,
    )
    # Qdrant: batch delete all chunk_ids in one filter using should
    _qdrant().delete(
        collection_name=QDRANT_COLLECTION,
        points_selector=Filter(
            must=[FieldCondition(key="table_id", match=MatchValue(value=str(table_id)))],
            should=[FieldCondition(key="chunk_id", match=MatchValue(value=int(cid))) for cid in chunk_ids],
        ),
    )


# ── Embed + store new chunks ──────────────────────────────────────────────────

def _embed_and_store(to_index: list[tuple[int, str]], schema: TableSchema, sheet_name: str):
    if not to_index:
        return

    es = _es()
    qd = _qdrant()
    model = _embed_model()
    region_idx = int(schema.table_id.split(":")[-1])

    texts = [ct for _, ct in to_index]
    vectors = []
    for start in range(0, len(texts), BATCH_SIZE):
        vectors.extend(model.encode(texts[start: start + BATCH_SIZE], show_progress_bar=False).tolist())

    es_ops = []
    qdrant_points = []
    for (chunk_id, chunk_text), vector in zip(to_index, vectors):
        doc_id = str(uuid.uuid4())
        es_ops.append({"index": {"_index": ES_CHUNK_INDEX, "_id": doc_id}})
        es_ops.append({
            "table_id":          schema.table_id,
            "file_name":         schema.file_name,
            "file_id":           schema.file_id,
            "sheet_name":        sheet_name,
            "region_index":      region_idx,
            "chunk_id":          chunk_id,
            "chunk_text":        chunk_text,
            "chunk_text_search": chunk_text,
        })
        qdrant_points.append(PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "table_id":   schema.table_id,
                "file_id":    schema.file_id,
                "chunk_id":   chunk_id,
                "chunk_text": chunk_text,
            },
        ))

    if es_ops:
        es.bulk(operations=es_ops, refresh=True)
    if qdrant_points:
        _ensure_qdrant_collection()
        qd.upsert(collection_name=QDRANT_COLLECTION, points=qdrant_points)


# ── Public API ────────────────────────────────────────────────────────────────

def index_chunks(table: DetectedTable, schema: TableSchema):
    """
    Chunk-level diff using SequenceMatcher (mirrors TS computeSheetDiff / diffArrays):
      equal   → skip
      insert  → embed + store new chunks
      delete  → delete old chunks from ES + Qdrant
      replace → delete old + embed new (changed chunk)
    """
    if not table.rows or not schema.headers:
        return

    sheet_name = schema.source_range.split("!")[0]
    row_texts = [t for row in table.rows if (t := row_to_text(row, schema.headers))]
    if not row_texts:
        return

    current_blocks = [DiffBlock(text=rt) for rt in row_texts]
    previous_blocks = _get_stored_blocks(schema.table_id)
    starting_chunk_id = _get_next_chunk_id(schema.table_id)

    to_index, to_delete = _compute_diff(previous_blocks, current_blocks, starting_chunk_id)

    print(f"[diff] {schema.table_id}: prev={len(previous_blocks)} curr={len(current_blocks)} to_index={len(to_index)} to_delete={len(to_delete)}")

    _delete_chunks(schema.table_id, to_delete)
    _embed_and_store(to_index, schema, sheet_name)
