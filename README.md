# Spreadsheet Intelligence

Multi-table spreadsheet parser with schema extraction, vector search, and structured query routing.

## Architecture

```
File Upload → File Detector → Parser (openpyxl/xlrd/pandas)
                                    ↓
                           Region Detector (merge signals + empty rows)
                                    ↓
                           Header Inference (per region)
                                    ↓
                    ┌───────────────┴───────────────┐
              Path A: Row Chunks              Path B: Table Schema
              ES + Qdrant index               ES schema index
                    └───────────────┬───────────────┘
                                    ↓
                             Query Agent
                    ┌───────────────┼───────────────┐
               DataFrame        Vector           Keyword
               (pandas)        (Qdrant)           (ES)
```

## Setup

### 1. Start ES + Qdrant
```bash
docker-compose up -d
```

### 2. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 3. Run the app
```bash
uvicorn backend.main:app --reload --port 8000
```

### 4. Open UI
Navigate to http://localhost:8000

## Usage

1. Upload any `.xlsx`, `.xls`, `.csv`, or `.tsv` file
2. The sidebar shows all detected tables with column types (number/date/category/text)
3. Click a table card to see its schema
4. Ask questions in the query box:
   - Structured: *"how many items are overdue?"* → DataFrame mode
   - Semantic: *"summarize sprint progress"* → Vector mode
   - Keyword: *"find rows with status Active"* → Keyword mode

## Query Routing Logic

| Query type | Mode | Backend |
|---|---|---|
| `how many`, `count`, `where <col>` | DataFrame | pandas on indexed chunks |
| Semantic / open-ended | Vector | Qdrant cosine similarity |
| Fallback | Keyword | Elasticsearch full-text |

## ES Indices

- `spreadsheet_schemas` — table metadata, column types, sample values
- `spreadsheet_chunks` — row text chunks for keyword search
- Qdrant `spreadsheet_vectors` — embeddings for semantic search
