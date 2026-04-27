# AGENTS.md

## Project Overview

**XCMemory Interest** (v0.4.0) is a Python 3.10+ structured memory management system built around a 6-slot memory representation (`<scene><subject><action><object><purpose><result>`). It provides:

- ChromaDB-backed vector storage with semantic search
- MQL (Memory Query Language) — a custom SQL-like DSL for CRUD and graph traversal
- NL Pipeline — natural language to MQL conversion via LLMs
- HTTP + WebSocket API server
- Independent chat application with character role-playing and memory integration
- Gradio WebUI for visual management
- Multi-system management with user auth and permissions

## Tech Stack

- **Language**: Python 3.10+
- **Build**: setuptools (PEP 517/518 via `pyproject.toml`)
- **Package Manager**: pip (with venv at `venv/`)
- **Vector DB**: ChromaDB >= 1.5.7
- **KV Store**: LMDB >= 1.4.0
- **Metadata DB**: SQLite (stdlib)
- **LLM**: OpenAI-compatible API (openai package)
- **Web Server**: stdlib `http.server` + `socketserver`, `websockets` for WS
- **WebUI**: Gradio
- **ML (optional)**: PyTorch (for InterestEncoder embedding model)
- **Testing**: pytest

## Build & Run Commands

```bash
# Install in editable mode
venv/Scripts/python.exe -m pip install -e .

# Start the main server (HTTP + WS + optional Gradio)
venv/Scripts/python.exe start_server.py
venv/Scripts/python.exe start_server.py --gradio
venv/Scripts/python.exe start_server.py --host 0.0.0.0 --port 8080

# Start API-only (no torch, no lifecycle manager)
venv/Scripts/python.exe start_api_only.py

# Start without torch
venv/Scripts/python.exe start_server_notorch.py

# Start the chat application
venv/Scripts/python.exe chat/main.py
```

## Test Commands

```bash
# Run all tests
venv/Scripts/python.exe -m pytest tests/ -v

# Specific test files
venv/Scripts/python.exe -m pytest tests/test_nl_modules.py -v
venv/Scripts/python.exe -m pytest tests/test_mql.py -v

# Internal module tests
venv/Scripts/python.exe -m pytest src/xcmemory_interest/basic_crud/test_basic_crud.py -v
venv/Scripts/python.exe -m pytest src/xcmemory_interest/vector_db/test_vector_db.py -v
venv/Scripts/python.exe -m pytest src/xcmemory_interest/pyapi/test_pyapi.py -v
venv/Scripts/python.exe -m pytest src/xcmemory_interest/lifecycle_manager/test_access_decay.py -v
venv/Scripts/python.exe -m pytest src/xcmemory_interest/lifecycle_manager/test_lifecycle_update.py -v
```

Notes on testing:
- Tests in `tests/test_nl_modules.py` use `unittest.mock` (AsyncMock, MagicMock, patch) to mock LLM clients — no real API calls.
- Tests that import `torch` will fail if PyTorch is not installed; use `start_server_notorch.py` pattern to bypass.
- There is **no linting or type-checking configuration** in this project (no ruff, mypy, flake8, pre-commit).

## Architecture

```
src/xcmemory_interest/
├── mql/                    # MQL lexer → parser → interpreter pipeline
│   ├── lexer.py            # Tokenizer
│   ├── parser.py           # Parser (tokens → AST)
│   ├── interpreter_extended.py  # Main interpreter (AST → operations)
│   ├── interpreter_dryrun.py    # Dry-run mode (print operations without executing)
│   ├── time_filters.py     # Time range filter logic
│   └── sto_operations.py   # Store-to-operations conversion
├── pyapi/                  # Python Application API
│   └── core.py             # PyAPI (multi-system manager) + MemorySystem (single system)
├── netapi/                 # HTTP + WebSocket API server
│   └── __init__.py         # APIServer class (~1173 lines)
├── basic_crud/             # Vector DB CRUD operations
│   └── vec_db_crud.py      # Main VecDBCRUD class
├── vector_db/              # ChromaDB wrapper
│   ├── chroma_vector_db.py # ChromaDB management
│   ├── subspace_search.py  # Per-slot vector search
│   └── reranker.py         # Result re-ranking
├── auxiliary_query/        # Auxiliary indexes and storage
│   ├── indexes/            # Time index, slot index
│   ├── storage/            # KV DB (LMDB), SQL DB (SQLite)
│   ├── interpreter/        # Mini DSL interpreter for aux queries
│   └── scheduler/          # Background task scheduler
├── nl/                     # Natural Language pipeline
│   ├── intent_classifier.py    # Classify query intent
│   ├── mql_generator.py        # NL → MQL generation via LLM
│   ├── slot_extractor.py       # Extract slot values from NL
│   ├── query_rewriter.py       # Rewrite/optimize queries
│   ├── sufficiency_checker.py  # Check if results are sufficient
│   ├── llm_ranker.py           # LLM-based result ranking
│   ├── reinforcement.py        # Dedup + salience scoring
│   ├── hybrid_search.py        # Hybrid vector + keyword search
│   └── pipeline.py             # Full NLSearchPipeline orchestrator
├── lifecycle_manager/      # Memory lifecycle (expiry, decay, access decay)
│   └── core.py             # Requires torch (can be bypassed)
├── embedding_coder/        # InterestEncoder model (6-slot transformer, ~12.5M params)
│   └── model.py            # Currently disabled / not supported at runtime
├── version_control/        # Memory version control (commit, rollback, diff)
├── graph_query/            # Implicit graph queries (multi-hop slot exploration)
├── online_learning/        # Placeholder for online learning (only DESIGN.md)
├── config.py               # SLOT_NAMES, model config, training defaults
└── user_manager.py         # User auth + permissions (SQLite-backed)
```

## Key Conventions

- **Package lazy imports**: `src/xcmemory_interest/__init__.py` uses lazy imports to avoid torch DLL issues on import.
- **Config**: Server config is in `config.toml` (auto-generated on first boot). In-code constants are in `src/xcmemory_interest/config.py`.
- **6 Slots**: `scene`, `subject`, `action`, `object`, `purpose`, `result` — the core memory representation.
- **API design**: All modules follow a layered architecture: MQL → PyAPI → NetAPI. Each layer builds on the one below.
- **Error handling**: Use exception classes from `mql/__init__.py` (query errors, parse errors, etc.).
- **Docstrings**: Modules use docstrings and type annotations, but there is no enforced style.
- **No linting/formatting tools configured** — follow existing code style when making changes.

## Entry Points

| File | Purpose |
|------|---------|
| `start_server.py` | Main server startup (HTTP+WS+Gradio). Auto-generates `config.toml` and admin API key on first run. |
| `start_api_only.py` | Minimal API-only startup (no torch, no lifecycle manager). |
| `start_server_notorch.py` | Monkey-patches torch out, then starts server. |
| `chat/main.py` | Character role-playing chat application entry point. |
| `webui/app.py` | Gradio WebUI functions (`init_webui()`, `launch_gradio()`). |

## Data Flow

```
User Query (NL) → NL Pipeline → MQL → MQL Interpreter → PyAPI → VecDBCRUD → ChromaDB
                                              ↓
User Query (MQL) ─────────────────────────────┘
                                              ↓
User Query (HTTP/WS) → NetAPI → PyAPI → VecDBCRUD → ChromaDB
```

## External References

- `memU-main/` and `text2mem-main/` are reference research projects for design inspiration.
- `EverOS-main/` is a downloaded reference project.
- `docs/MEMU_TEXT2MEM_REFERENCE.md` documents design patterns borrowed from these projects.
- Each major subpackage has its own `DESIGN.md` with module-specific architecture details.

## Documentation

| Document | Path |
|----------|------|
| README | `README.md` |
| User Guide | `docs/USER_GUIDE.md` |
| Developer Guide | `docs/DEVELOPER_GUIDE.md` |
| API Reference | `docs/API_REFERENCE.md` |
| MQL Reference | `docs/MQL_REFERENCE.md` |
| MQL Spec (Chinese) | `docs/MQL规范.md` |
| Retrieval Design | `RETRIEVAL_DESIGN.md` |
| Chat Design | `chat/DESIGN.md` |
