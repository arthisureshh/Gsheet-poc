import pandas as pd
from elasticsearch import Elasticsearch
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from backend.indexer import ES_SCHEMA_INDEX, ES_CHUNK_INDEX, QDRANT_COLLECTION, _embed_model

_es_client: Elasticsearch | None = None
_qdrant_client: QdrantClient | None = None


def _es() -> Elasticsearch:
    global _es_client
    if _es_client is None:
        _es_client = Elasticsearch("http://localhost:9200")
    return _es_client


def _qdrant() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(host="localhost", port=6333)
    return _qdrant_client


def _fetch_all_schemas(file_name: str | None = None) -> list[dict]:
    query = {"match_all": {}} if not file_name else {"term": {"file_name.keyword": file_name}}
    try:
        res = _es().search(index=ES_SCHEMA_INDEX, query=query, size=50)
        return [h["_source"] for h in res["hits"]["hits"]]
    except Exception:
        return []


def _fetch_chunks_for_table(table_id: str) -> list[dict]:
    res = _es().search(
        index=ES_CHUNK_INDEX,
        query={"bool": {"must": [
            {"term": {"table_id.keyword": table_id}},
            {"range": {"row_index": {"gte": 0}}},
        ]}},
        size=10000,
    )
    return [h["_source"] for h in res["hits"]["hits"]]


def _keyword_search(query: str, table_id: str) -> list[dict]:
    """#4 search both chunk_text and col_fields via multi_match."""
    res = _es().search(
        index=ES_CHUNK_INDEX,
        query={"bool": {"must": [
            {"term": {"table_id.keyword": table_id}},
            {"multi_match": {"query": query, "fields": ["chunk_text", "md_text"]}},
        ]}},
        size=10,
    )
    return [h["_source"] for h in res["hits"]["hits"]]


def _vector_search(query: str, table_id: str) -> list[dict]:
    """#6 region-scoped vector search."""
    model = _embed_model()
    vector = model.encode([query])[0].tolist()
    filt = Filter(must=[FieldCondition(key="table_id", match=MatchValue(value=table_id))])
    results = _qdrant().search(
        collection_name=QDRANT_COLLECTION,
        query_vector=vector,
        query_filter=filt,
        limit=10,
    )
    return [{"chunk_text": r.payload.get("md_text") or r.payload["chunk_text"], "score": r.score} for r in results]


def _best_schema(schemas: list[dict], q_lower: str) -> dict:
    """#5 schema-driven routing — pick table whose headers/label best match query."""
    scored = []
    for s in schemas:
        score = 0
        for h in s.get("headers", []):
            if h.lower() in q_lower:
                score += 2
        label = (s.get("table_label") or "").lower()
        if label and label in q_lower:
            score += 3
        # Bonus: sample values match query terms
        for vals in s.get("sample_values", {}).values():
            for v in vals:
                if str(v).lower() in q_lower:
                    score += 1
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _dataframe_query(query: str, table_id: str, schema: dict) -> str:
    """#2 #5 type-aware DataFrame queries using schema column_types."""
    chunks = _fetch_chunks_for_table(table_id)
    if not chunks:
        return "No data found for this table."

    headers = schema["headers"]
    column_types = schema.get("column_types", {})

    records = []
    for chunk in chunks:
        text = chunk.get("chunk_text", "")
        record = {}
        for part in text.split(", "):
            if ": " in part:
                k, v = part.split(": ", 1)
                record[k.strip()] = v.strip()
        if record:
            records.append(record)

    if not records:
        return "No data found."

    df = pd.DataFrame(records)
    # Coerce numeric columns (#2 type awareness)
    for col in headers:
        if col in df.columns and column_types.get(col) == "number":
            df[col] = pd.to_numeric(df[col], errors="coerce")

    q_lower = query.lower()

    # Count queries — match cell values first
    if "how many" in q_lower or "count" in q_lower:
        for col in headers:
            if col not in df.columns:
                continue
            for val in df[col].dropna().unique():
                if str(val).lower() in q_lower:
                    count = (df[col].astype(str).str.lower() == str(val).lower()).sum()
                    return f"{count} row(s) where {col} = '{val}'"
        for col in headers:
            if col in df.columns and col.lower() in q_lower:
                counts = df[col].value_counts().to_dict()
                return f"Value counts for '{col}':\n" + "\n".join(f"  {k}: {v}" for k, v in counts.items())
        return f"Total rows: {len(df)}"

    # Aggregation: sum / average (#2 numeric type)
    for agg, fn in [("sum", "sum"), ("total", "sum"), ("average", "mean"), ("avg", "mean")]:
        if agg in q_lower:
            for col in headers:
                if col in df.columns and column_types.get(col) == "number" and col.lower() in q_lower:
                    result = getattr(df[col], fn)()
                    return f"{fn.capitalize()} of '{col}': {result:.2f}"

    # Filter: "where <col> is <value>"
    for col in headers:
        if col not in df.columns:
            continue
        col_lower = col.lower()
        for sep in [f"{col_lower} is ", f"{col_lower}="]:
            if sep in q_lower:
                val = q_lower.split(sep, 1)[1].split()[0].strip("'\"")
                filtered = df[df[col].astype(str).str.lower() == val]
                if not filtered.empty:
                    return filtered[headers].to_string(index=False)

    # Value match in query
    for col in headers:
        if col not in df.columns:
            continue
        for val in df[col].dropna().unique():
            if str(val).lower() in q_lower:
                filtered = df[df[col].astype(str).str.lower() == str(val).lower()]
                if not filtered.empty:
                    return filtered[headers].to_string(index=False)

    return f"Table has {len(df)} rows, columns: {', '.join(headers)}"


def query(user_query: str, file_name: str | None = None) -> dict:
    schemas = _fetch_all_schemas(file_name)
    if not schemas:
        return {"mode": "no_data", "results": [], "message": "No indexed data found. Upload a file first."}

    q_lower = user_query.lower()
    schema = _best_schema(schemas, q_lower)  # #5 schema-driven routing
    table_id = schema["table_id"]

    is_structured = (
        any(kw in q_lower for kw in ["how many", "count", "where", "filter", "total", "sum", "average", "avg"])
        or any(str(v).lower() in q_lower
               for vals in schema.get("sample_values", {}).values() for v in vals)
        or any(h.lower() in q_lower for h in schema.get("headers", []))
    )

    if is_structured:
        result_text = _dataframe_query(user_query, table_id, schema)
        return {"mode": "dataframe", "table_id": table_id, "schema": schema, "results": [{"chunk_text": result_text}]}

    vector_results = _vector_search(user_query, table_id)
    if vector_results:
        return {"mode": "vector", "table_id": table_id, "schema": schema, "results": vector_results}

    keyword_results = _keyword_search(user_query, table_id)
    return {"mode": "keyword", "table_id": table_id, "schema": schema, "results": keyword_results}
