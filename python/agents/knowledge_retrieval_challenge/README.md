# Knowledge Retrieval System

This repository contains a complete software product designed to parse complex regulatory PDFs (specifically the SpaceX TPDES testing report) and answer questions about it in near-real time. 

It fulfills the requirements of the Code Challenge without using external wrapper frameworks like LangChain or LlamaIndex.

## Architecture: The "Smart Ingestion" Pipeline

Standard text extraction fails on complex regulatory documents because of disjointed form fields, dense tables, opaque visuals (maps/flow diagrams), and implicit cross-references. 

This solution uses the **Google GenAI SDK** directly to implement a 5-stage ingestion pipeline:
1. **Modality Routing:** Identifies pages as Forms, Tables, Visuals, or Text.
2. **VLM Extraction:** Uses Gemini 1.5 Pro Vision to extract forms into semantic paragraphs and tables into standalone row-level summaries.
3. **Contextual Enrichment:** Resolves footnotes and lab qualifiers, injecting them directly into the tabular text chunks.
4. **Document-Level Synthesis:** Generates a global summary and synthetic Q&A pairs (to resolve implicit cross-references) across the entire text.
5. **Hybrid Querying:** Uses FAISS for local, lightweight vector retrieval, and expands user queries into bilingual synonyms to ensure high recall. Retrieved text and raw images are passed to Gemini 2.5 Flash for grounded QA generation.

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
python main.py ingest testing-report.pdf --max-pages 20
```

This will create a `vector_store` directory containing the FAISS index, metadata chunks, and extracted images.

### Phase 2: Querying
Ask a question. The system will retrieve the context and generate an answer in near-real time.

```bash
python main.py query "What does the report say about mercury levels?"
python main.py query "Describe the water cycle---from source to Outfall 001"
```

## Edge Cases Handled
- **Bilingual Content:** The query expander naturally handles translating Spanish vocabulary.
- **Visual Maps:** Flow diagrams and maps are saved as raw images and passed to the LLM at query time.
- **API Limits:** Exponential backoff is implemented (via the Google GenAI SDK's native retry logic) for quota safety.
