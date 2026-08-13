import uuid
import hashlib
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from elasticsearch import Elasticsearch
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
from sentence_transformers import SentenceTransformer
from backend.models import TableSchema, DetectedTable
from backend.header_inference import row_to_text, batch_rows_into_chunks

ES_SCHEMA_INDEX = "spreadsheet_schemas"
ES_CHUNK_INDEX  = "spreadsheet_chunks"
QDRANT_COLLECTION = "spreadsheet_vectors"
VECTOR_DIM  = 384
BATCH_SIZE  = 32

_model: SentenceTransformer | None = None
_executor = ThreadPoolExecutor(max_workers=4)


def _embed_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def _es() -> Elasticsearch:
    return Elasticsearch("http://localhost:9200")


def _qdrant() -> QdrantClient:
    return QdrantClient(host="localhost", port=6333)


# ── Chunk identity (mirrors Paxi chunk_of = fileId:chunkId) ──────────────────

@dataclass
class DiffBlock:
    chunk_id: str        # stable ES document _id
    chunk_text: str
    row_index: int       # sequential chunk number within the table
    row_hash: str        # md5 of chunk_text — used for diff


def _chunk_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _chunk_of(file_id: str, chunk_id: str) -> str:
    """Mirrors Paxi's chunk_of = fileId:chunkId payload field."""
    return f"{file_id}:{chunk_id}"


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
    "chunk_of":    {"type": "keyword"},
    "sheet_name":  {"type": "keyword"},
    "region_index":{"type": "integer"},
    "chunk_text":  {"type": "text"},
    "row_hash":    {"type": "keyword"},
    "row_index":   {"type": "integer"},
}}


def ensure_indices():
    es = _es()
    for index, mappings in [(ES_SCHEMA_INDEX, _SCHEMA_MAPPINGS), (ES_CHUNK_INDEX, _CHUNK_MAPPINGS)]:
        try:
            es.indices.create(index=index, mappings=mappings, settings=_INDEX_SETTINGS)
        except Exception as e:
            if "resource_already_exists" not in str(e).lower() and "already exists" not in str(e).lower():
                raise
            # Index exists — ensure field limit is applied
            try:
                es.indices.put_settings(index=index, settings=_INDEX_SETTINGS)
            except Exception:
                pass

    qd = _qdrant()
    existing = [c.name for c in qd.get_collections().collections]
    if QDRANT_COLLECTION not in existing:
        qd.create_collection(QDRANT_COLLECTION, vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE))


def _to_json_val(v) -> str:
    if isinstance(v, __import__('datetime').datetime):
        return v.date().isoformat()
    return str(v)


def index_schema(schema: TableSchema):
    es = _es()
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
    es.index(index=ES_SCHEMA_INDEX, id=schema.table_id, document=doc)


# ── Build chunks (TS pipeline port) ──────────────────────────────────────────

def _build_diff_blocks(table: DetectedTable, schema: TableSchema) -> list[DiffBlock]:
    """
    Mirrors Paxi's chunkTableContentToMetadata + batchRowsIntoChunks.
    Returns DiffBlock list — chunk_id assigned here as stable uuid based on content hash.
    """
    row_texts = [t for row in table.rows if (t := row_to_text(row, schema.headers))]
    if not row_texts:
        return []

    chunk_texts = batch_rows_into_chunks(row_texts)
    blocks = []
    for i, chunk_text in enumerate(chunk_texts):
        row_hash = _chunk_hash(chunk_text)
        chunk_id = hashlib.md5(f"{schema.table_id}:{i}".encode()).hexdigest()
        blocks.append(DiffBlock(
            chunk_id=chunk_id,
            chunk_text=chunk_text,
            row_index=i,
            row_hash=row_hash,
        ))
    return blocks


# ── computeSheetDiff (mirrors Paxi's computeSheetDiff) ───────────────────────

@dataclass
class DiffResult:
    to_index: list[DiffBlock]      # new or changed chunks → embed + store
    delete_chunk_ids: list[str]    # stale ES doc IDs → delete from ES + Qdrant


def _get_previous_blocks(table_id: str) -> list[DiffBlock]:
    """Load previously indexed chunks from ES — mirrors Paxi's getChunksByFileId."""
    try:
        res = _es().search(
            index=ES_CHUNK_INDEX,
            query={"bool": {"must": [
                {"term": {"table_id.keyword": table_id}},
                {"range": {"row_index": {"gte": 0}}},
            ]}},
            _source=["chunk_text", "md_text", "row_index", "row_hash", "chunk_of"],
            size=10000,
            sort=[{"row_index": "asc"}],
        )
        blocks = []
        for h in res["hits"]["hits"]:
            s = h["_source"]
            # chunk_id is the ES doc _id
            blocks.append(DiffBlock(
                chunk_id=h["_id"],
                chunk_text=s.get("chunk_text", ""),
                row_index=s.get("row_index", 0),
                row_hash=s.get("row_hash", ""),
            ))
        return blocks
    except Exception:
        return []


def compute_sheet_diff(previous: list[DiffBlock], current: list[DiffBlock]) -> DiffResult:
    """
    Text-level diff — mirrors Paxi's computeSheetDiff / computeDiff.

    Strategy:
    - Build a map of previous chunks by row_hash
    - Current chunks whose hash exists in previous → unchanged, skip
    - Current chunks whose hash is new → to_index
    - Previous chunk IDs not matched by any current chunk → delete_chunk_ids
    """
    prev_by_hash: dict[str, DiffBlock] = {b.row_hash: b for b in previous}
    current_hashes = {b.row_hash for b in current}

    to_index: list[DiffBlock] = []
    for block in current:
        if block.row_hash not in prev_by_hash:
            to_index.append(block)  # new or changed

    delete_chunk_ids: list[str] = [
        b.chunk_id for b in previous if b.row_hash not in current_hashes
    ]

    return DiffResult(to_index=to_index, delete_chunk_ids=delete_chunk_ids)


# ── Delete stale chunks (mirrors Paxi's deleteByQuery + deleteByFilter) ───────

def _delete_chunks_by_ids(chunk_ids: list[str]):
    """Delete specific chunk IDs from ES + Qdrant — mirrors Paxi's stale chunk deletion."""
    if not chunk_ids:
        return
    es = _es()
    qd = _qdrant()
    # ES: delete by _id
    try:
        es.delete_by_query(
            index=ES_CHUNK_INDEX,
            query={"ids": {"values": chunk_ids}},
            refresh=True,
        )
    except Exception:
        pass
    # Qdrant: delete by chunk_id payload field
    for cid in chunk_ids:
        try:
            qd.delete(
                collection_name=QDRANT_COLLECTION,
                points_selector=Filter(must=[
                    FieldCondition(key="chunk_id", match=MatchValue(value=cid))
                ]),
            )
        except Exception:
            pass


def _delete_all_table_chunks(table_id: str):
    """Full wipe for a table — used on first upload (no previous state)."""
    try:
        _es().delete_by_query(
            index=ES_CHUNK_INDEX,
            query={"term": {"table_id.keyword": table_id}},
            refresh=True,
        )
        _qdrant().delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=Filter(must=[
                FieldCondition(key="table_id", match=MatchValue(value=table_id))
            ]),
        )
    except Exception:
        pass


# ── Embed + store (mirrors Paxi's VectorizeProcessor) ────────────────────────

def _ensure_qdrant_collection():
    qd = _qdrant()
    existing = [c.name for c in qd.get_collections().collections]
    if QDRANT_COLLECTION not in existing:
        qd.create_collection(QDRANT_COLLECTION, vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE))


def _embed_and_store(blocks: list[DiffBlock], schema: TableSchema):
    """
    Mirrors Paxi's VectorizeProcessor:
    - Batch embed chunk_text
    - Upsert to Qdrant (vector store)
    - Bulk index to ES (event store)
    """
    if not blocks:
        return

    _ensure_qdrant_collection()
    es = _es()
    qd = _qdrant()
    model = _embed_model()
    region_idx = int(schema.table_id.split(":")[-1])
    sheet_name = schema.source_range.split("!")[0]

    texts = [b.chunk_text for b in blocks]
    vectors = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start: start + BATCH_SIZE]
        vectors.extend(model.encode(batch, show_progress_bar=False).tolist())

    es_ops = []
    qdrant_points = []
    for block, vector in zip(blocks, vectors):
        chunk_of = _chunk_of(schema.file_id, block.chunk_id)
        es_ops.append({"index": {"_index": ES_CHUNK_INDEX, "_id": block.chunk_id}})
        es_ops.append({
            "table_id":     schema.table_id,
            "file_name":    schema.file_name,
            "file_id":      schema.file_id,
            "chunk_of":     chunk_of,
            "sheet_name":   sheet_name,
            "region_index": region_idx,
            "chunk_text":   block.chunk_text,
            "row_hash":     block.row_hash,
            "row_index":    block.row_index,
        })
        qdrant_points.append(PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "table_id":   schema.table_id,
                "file_id":    schema.file_id,
                "chunk_id":   block.chunk_id,
                "chunk_of":   chunk_of,
                "region_index": region_idx,
                "chunk_text": block.chunk_text,
                "row_hash":   block.row_hash,
            },
        ))

    if es_ops:
        es.bulk(operations=es_ops)
    if qdrant_points:
        qd.upsert(collection_name=QDRANT_COLLECTION, points=qdrant_points)


# ── Public API ────────────────────────────────────────────────────────────────

def index_chunks(table: DetectedTable, schema: TableSchema, incremental: bool = True):
    """
    Main entry point — mirrors Paxi's full diff + index + delete flow:

    1. Build current DiffBlocks from table rows (chunkTableContentToMetadata + batchRowsIntoChunks)
    2. Load previous DiffBlocks from ES (getChunksByFileId)
    3. computeSheetDiff → to_index + delete_chunk_ids
    4. _embed_and_store(to_index)   ← VectorizeProcessor
    5. _delete_chunks_by_ids(delete_chunk_ids)  ← deleteByQuery + deleteByFilter
    """
    if not table.rows or not schema.headers:
        return

    current_blocks = _build_diff_blocks(table, schema)
    if not current_blocks:
        return

    if incremental:
        previous_blocks = _get_previous_blocks(schema.table_id)
        diff = compute_sheet_diff(previous_blocks, current_blocks)
        _embed_and_store(diff.to_index, schema)
        _delete_chunks_by_ids(diff.delete_chunk_ids)
    else:
        # First upload — wipe and index everything
        _delete_all_table_chunks(schema.table_id)
        _embed_and_store(current_blocks, schema)
