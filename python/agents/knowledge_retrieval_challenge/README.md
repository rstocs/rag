# Knowledge Retrieval System

This repository contains a complete software product designed to parse complex regulatory PDFs (specifically the SpaceX TPDES testing report) and answer questions about it in near-real time with **1.00 Faithfulness** (zero hallucinations).

It fulfills the requirements of the Code Challenge without using external wrapper frameworks like LangChain or LlamaIndex.

## System Architecture

For a deep dive into the design choices, model selection justifications (e.g., Gemini 2.5 Pro vs Flash), and the Dual-Indexing strategy, please read the [ARCHITECTURE.md](ARCHITECTURE.md) design document.

At a high level, this solution uses the **Google GenAI SDK** directly to implement a 5-stage ingestion pipeline:
1. **Intelligent Modality Routing:** Identifies pages as Forms, Tables, Visuals, or Text using fast LLM routing.
2. **Dual-Indexing VLM Extraction:** Uses Gemini 2.5 Pro to extract complex forms/tables into semantic paragraphs, while simultaneously indexing raw PyMuPDF OCR to prevent data loss.
3. **Contextual Enrichment:** Resolves footnotes and lab qualifiers, injecting them directly into tabular text chunks.
4. **Document-Level Synthesis:** Generates a global summary and synthetic Q&A pairs (HyDE) across the entire text.
5. **Hybrid Querying:** Uses FAISS for dense vector retrieval, BM25 for sparse keyword retrieval, and a HuggingFace Cross-Encoder for re-ranking. Retrieved context is passed to Gemini 2.5 Pro under strict guardrails for grounded QA generation.

## Setup

1.  Ensure you have Python 3.10+ installed.
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
    *Note: You may need to install `poppler` on your system for `pdf2image` to work (e.g., `brew install poppler` on macOS).*
3.  Set up your Google API key:
    Create a `.env` file in this directory with:
    ```
    GEMINI_API_KEY=your_api_key_here
    ```

## Usage

### Phase 1: Extraction
Run the ingestion pipeline on the PDF. For a quick demonstration, you can limit the number of pages processed:

```bash
python -m src.main ingest testing-report.pdf --max-pages 20
```

This will create a `vector_store` directory containing the FAISS index, metadata chunks, and extracted images.

### Phase 2: Querying
Ask a question. The system will retrieve the context and generate an answer in near-real time.

```bash
python -m src.main query "What does the report say about mercury levels?"
python -m src.main query "Describe the water cycle---from source to Outfall 001"
python -m src.main query "Which SpaceX employees listed in the document appear in the chain-of-custody records?"
```

## Edge Cases & Guardrails Handled
- **Bilingual Content:** The query expander naturally handles translating Spanish vocabulary.
- **Visual Maps:** Flow diagrams and maps are explicitly classified and routed to the Vision model to extract spatial relationships.
- **API Limits:** Exponential backoff is implemented (via `tenacity`) alongside semaphore concurrency limits to ensure Vertex AI quota safety.
- **Strict Guardrails:** The generator is explicitly prompted to refuse undocumented mathematical conversions and to cleanly abstain if the context is insufficient, ensuring 1.00 Faithfulness.
- **Evaluation Harness:** Includes a full LLM-as-a-judge test suite (`test_final.py`) evaluating Faithfulness, Relevance, and Context Relevance.
