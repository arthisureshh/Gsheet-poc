"""Verify ES + Qdrant have correct row counts and content after upload."""
import sys
from elasticsearch import Elasticsearch
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

es = Elasticsearch("http://localhost:9200")
qd = QdrantClient(host="localhost", port=6333)

# ES: row counts per table
try:
    res = es.search(index="spreadsheet_chunks", size=0,
        aggs={"by_table": {"terms": {"field": "table_id", "size": 30}}})
    print("=== ES rows per table ===")
    for b in res["aggregations"]["by_table"]["buckets"]:
        print(f"  {b['key']}: {b['doc_count']} rows")
except Exception as e:
    print(f"=== ES error: {e}")

# Qdrant: auto-recreate if missing, then show counts
existing = [c.name for c in qd.get_collections().collections]
if "spreadsheet_vectors" not in existing:
    print("\n=== Qdrant: collection missing — recreating ===")
    qd.create_collection("spreadsheet_vectors", vectors_config=VectorParams(size=384, distance=Distance.Cosine))
    print("  Created. Upload files to populate.")
else:
    info = qd.get_collection("spreadsheet_vectors")
    print(f"\n=== Qdrant ===")
    print(f"  Total points:  {info.points_count}")
    print(f"  Indexed:       {info.indexed_vectors_count}")

    from collections import Counter
    all_points = []
    offset = None
    while True:
        result, offset = qd.scroll("spreadsheet_vectors", limit=1000, with_payload=True, offset=offset)
        all_points.extend(result)
        if offset is None:
            break
    counts = Counter(p.payload["table_id"] for p in all_points)
    print("  Points per table:")
    for tid, cnt in sorted(counts.items()):
        print(f"    {tid}: {cnt}")

# Spot-check: show first 5 rows of a specific table if provided
if len(sys.argv) > 1:
    table_id = sys.argv[1]
    hits = es.search(index="spreadsheet_chunks",
        query={"term": {"table_id": table_id}},
        _source=["chunk_id", "chunk_text"],
        sort=[{"chunk_id": "asc"}], size=5)
    print(f"\n=== First 5 rows of '{table_id}' ===")
    for h in hits["hits"]["hits"]:
        print(f"  [{h['_source']['chunk_id']}] {h['_source']['chunk_text'][:100]}")
