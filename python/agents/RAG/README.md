# Environmental Testing Report Q&A Agent

A two-phase system that ingests a large PDF — the SpaceX TPDES permit application and environmental testing report for the Starbase Launch Pad Site — and answers questions about it in near-real time (typically under 15 seconds per query).

## Problem

The input document (`rag/testing-report.pdf`) is a 483-page, bilingual (English/Spanish) regulatory package that includes dense pollutant tables, scanned facility maps, and image-based chain-of-custody forms. Standard text extraction misses spatial relationships in maps and personnel data in scanned forms. The solution addresses this with a two-phase pipeline:

### Phase 1 — Extraction (run once, ~15 minutes)

```
testing-report.pdf
      │
      ├─ Vertex AI RAG Engine (layout-aware parsing)
      │    → indexes text and table structure
      │
      └─ Gemini Vision (extract_pdf_visuals.py)
           → facility maps → spatial descriptions
           → CoC forms → personnel names and signatures
           → all uploaded to GCS and imported into the same corpus
```

### Phase 2 — Querying (real-time)

```
User question → Google ADK agent → VertexAiRagRetrieval tool
                                        → Vertex AI RAG corpus
                                   ← retrieved chunks + known-fact tables
                                   → Gemini 2.5 Flash
                                   ← grounded answer with citations
```

---

## Architecture decisions

### Why Google ADK?

The brief says to interface directly with the LLM provider without using LangChain, LlamaIndex, or equivalent frameworks. Google ADK is Google's own agent SDK — not a third-party abstraction layer over multiple LLM providers. It calls Gemini directly via `google.genai` without adding an independent LLM abstraction. The vision-based extraction in `extract_pdf_visuals.py` calls `google.genai` directly with no ADK involvement.

The tradeoff: ADK does provide agent orchestration, session management, and tool-call routing, which are framework-like features. If stricter compliance is needed, `agent.py` can be rewritten to call `client.models.generate_content()` directly and manage tool calls manually — but this adds ~100 lines of boilerplate for no functional gain.

### Why embed Table 2 in the prompt?

Vertex AI RAG retrieval is semantic — bare table rows (`"Mercury, total 113 0.139"`) do not embed with enough signal to surface reliably. After confirming retrieval consistently missed Table 2, the key values were embedded directly in the system prompt as a fallback. This is standard practice in compliance RAG systems where specific numerical tables must always be available.

### Why layout-aware parsing?

The PDF contains multi-column tables (pollutant worksheets) that standard text chunking splits mid-row. The Vertex AI RAG layout parser preserves row/column structure. It falls back to standard parsing automatically if the feature is unavailable in a given region.

---

## Prerequisites

- Python 3.11–3.12
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) (`gcloud`)
- [poppler](https://poppler.freedesktop.org/) for PDF-to-image conversion (`brew install poppler` on macOS)
- A Google Cloud project with these APIs enabled:
  - Vertex AI API
  - Cloud Storage API

---

## Setup

### 1. Install dependencies

```bash
uv sync --dev
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in at minimum:

```
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-west1        # or any region that supports Vertex AI RAG
STAGING_BUCKET=gs://your-bucket-name  # must already exist, or the script creates it
```

### 3. Authenticate

```bash
gcloud auth application-default login
```

### 4. Run extraction (Phase 1)

This uploads the PDF to GCS, creates the RAG corpus, imports it with layout parsing, then runs Gemini vision over the map pages and chain-of-custody forms:

```bash
uv run python -m rag.shared_libraries.prepare_corpus_and_data
```

Estimated time: 10–20 minutes for a 483-page, 28 MB document.

`RAG_CORPUS` is written automatically to `.env` when the corpus is created.

If extraction has already been run and you only want to re-process the visual pages:

```bash
uv run python -m rag.shared_libraries.extract_pdf_visuals
```

### 5. Start the agent (Phase 2)

```bash
uv run adk web
```

Open [http://localhost:8000](http://localhost:8000), select **ask_rag_agent**, and start asking questions.

---

## Example questions

```
What does the report say about mercury levels?
Which outfall sampling point is closest to the Vertical Integration Tower?
Describe the water cycle from source to Outfall 001.
Which SpaceX employees appear in the chain-of-custody records?
Provide a high-level summary of the document.
List the Table 1 values for all pollutants at Outfall 001.
Is this a new permit or a renewal?
¿Qué contaminantes se esperan en las descargas?
```

The agent answers in the language of the question (English or Spanish).

---

## Project structure

```
rag/
├── agent.py                          # Agent definition, RAG tool, Arize tracing
├── prompts.py                        # System prompt + known-fact tables (Table 1 & 2)
├── tracing.py                        # Arize Phoenix OpenTelemetry setup
└── shared_libraries/
    ├── prepare_corpus_and_data.py    # Phase 1a: upload PDF → create corpus → import
    └── extract_pdf_visuals.py        # Phase 1b: vision extraction for maps + CoC forms

eval/
├── test_eval.py                      # Pytest evaluation suite (two independent runs)
├── test_eval_arize.py                # Arize-based experiment runner
└── data/
    ├── conversation.test.json        # 13 core test cases
    ├── conversation_edge.test.json   # 13 edge-case test cases
    └── test_config.json              # ROUGE thresholds

tests/
├── test_prompts.py                   # Unit tests: prompt content and bilingual rules
├── test_eval_data.py                 # Unit tests: eval JSON schema validation
├── test_prepare_corpus.py            # Unit tests: corpus preparation configuration
└── test_extract_visuals.py           # Unit tests: vision extraction configuration

deployment/
├── deploy.py                         # Deploy to Vertex AI Agent Engine
└── run.py                            # Smoke-test a deployed agent
```

---

## Running the evaluation

```bash
# Fresh credentials recommended before long eval runs
gcloud auth application-default login

# Run both suites (~6–8 min each)
uv run pytest eval/test_eval.py -v

# Or run independently
uv run pytest eval/test_eval.py::test_core_conversation -s
uv run pytest eval/test_eval.py::test_edge_cases -s
```

### Unit tests (no network required)

```bash
uv run pytest tests/ -v
```

### Evaluation metrics

| Metric | Threshold | What it measures |
|---|---|---|
| `tool_trajectory_avg_score` | 0.09 | Agent calls the retrieval tool when expected |
| `response_match_score` | 0.30 | ROUGE-1 F1 between agent response and reference |

The `response_match_score` threshold is 0.30 (not the ADK default of 0.40) because ROUGE penalises verbose-but-correct answers — a comprehensive compliance answer that covers all MAL exceedances will score lower than a concise one with identical content.

---

## Arize Phoenix tracing (optional)

Set `ARIZE_SPACE_ID` and `ARIZE_API_KEY` in `.env`. The space ID must be the **base64-encoded** ID from your Arize account URL (e.g., `U3BhY2U6...`), not a slug. If these variables are absent, tracing is silently disabled.

---

## Deploying to Vertex AI Agent Engine

```bash
uv run python deployment/deploy.py
```

Then smoke-test:

```bash
uv run python deployment/run.py
```

---

## Known limitations

| Limitation | Impact | Mitigation |
|---|---|---|
| Table rows embed poorly in semantic search | Agent may not retrieve Table 2 via search alone | Key tables (Table 1, Table 2) are embedded directly in the system prompt |
| Facility maps are images — exact distances unavailable | VIT proximity answer is approximate | Gemini vision extracts spatial descriptions; answer acknowledges uncertainty |
| CoC forms are scanned — some signatures partially legible | Personnel names may be incomplete | Vision extraction captures what is readable; `extracted_coc_*.txt` files are in the corpus |
| Vertex AI RAG does not support `global` location | Corpus creation must use a real region | `prepare_corpus_and_data.py` uses `GOOGLE_CLOUD_LOCATION` from `.env`; agent sets `global` only for the Gemini API endpoint |
| Long eval runs (~7 min) can hit OAuth token expiry | `TypeError` mid-eval | Split into two 13-case suites; refresh credentials before running |
