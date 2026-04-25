# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Prepares the Vertex AI RAG corpus for the SpaceX TPDES testing report.

Two-phase workflow
------------------
Phase 1 – Extraction (run once):
    python -m rag.shared_libraries.prepare_corpus_and_data

    Uploads testing-report.pdf to GCS, creates (or reuses) a RAG corpus,
    imports the PDF with layout-aware parsing so tables and images are
    captured, then writes RAG_CORPUS to .env.

Phase 2 – Querying:
    Start the agent normally. It reads RAG_CORPUS from .env and uses the
    VertexAiRagRetrieval tool to answer questions in near-real time.
"""

import os
import time

import requests as http_requests
import vertexai
from dotenv import load_dotenv, set_key
from google.api_core.exceptions import ResourceExhausted
from google.auth import default
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud import storage
from vertexai.preview import rag

# ---------------------------------------------------------------------------
# Configuration — edit these constants to point at a different document
# ---------------------------------------------------------------------------

# Path to the PDF, relative to the project root (where you run uv run …)
LOCAL_PDF_PATH = os.path.join(
    os.path.dirname(__file__), "..", "testing-report.pdf"
)

# Display name used to find or create the RAG corpus (idempotent)
CORPUS_DISPLAY_NAME = "SpaceX_TPDES_Testing_Report_corpus"
CORPUS_DESCRIPTION = (
    "SpaceX TPDES permit application and environmental testing report "
    "for the Starbase Launch Pad Site, Brownsville TX (Permit WQ0005462000). "
    "Bilingual (English / Spanish). Includes pollutant tables, facility maps, "
    "water-balance diagrams, and SPL lab chain-of-custody records."
)

# GCS blob name for the uploaded PDF
GCS_BLOB_NAME = "testing-report.pdf"

# ---------------------------------------------------------------------------
# Bootstrap — load .env and resolve project / location
# ---------------------------------------------------------------------------

cwd_env = os.path.join(os.getcwd(), ".env")
ENV_FILE_PATH = cwd_env if os.path.exists(cwd_env) else os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", ".env")
)
# override=True ensures values from .env win over anything agent.py may have
# already written to os.environ (e.g. GOOGLE_CLOUD_LOCATION = "global").
load_dotenv(ENV_FILE_PATH, override=True)

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
if not PROJECT_ID:
    raise ValueError(
        "GOOGLE_CLOUD_PROJECT is not set. Add it to your .env file."
    )

LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION")
if not LOCATION:
    raise ValueError(
        "GOOGLE_CLOUD_LOCATION is not set. Add it to your .env file."
    )

# Derive the GCS bucket from STAGING_BUCKET (strip gs:// prefix)
_staging_raw = os.getenv("STAGING_BUCKET", "")
GCS_BUCKET_NAME = _staging_raw.removeprefix("gs://").rstrip("/")
if not GCS_BUCKET_NAME:
    raise ValueError(
        "STAGING_BUCKET is not set. Add it to your .env file (e.g. gs://my-bucket)."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def initialize_vertex_ai() -> None:
    credentials, _ = default()
    vertexai.init(project=PROJECT_ID, location=LOCATION, credentials=credentials)


def upload_pdf_to_gcs(local_path: str, bucket_name: str, blob_name: str) -> str:
    """Uploads a local file to GCS and returns the gs:// URI."""
    abs_path = os.path.abspath(local_path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(
            f"PDF not found at {abs_path}. "
            "Make sure testing-report.pdf is in the rag/ directory."
        )

    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(bucket_name)

    # Create the bucket if it doesn't exist yet
    if not bucket.exists():
        print(f"Bucket gs://{bucket_name} not found — creating it...")
        bucket.create(location=LOCATION)
        print(f"Bucket gs://{bucket_name} created.")

    blob = bucket.blob(blob_name)
    size_mb = os.path.getsize(abs_path) / 1_048_576
    print(f"Uploading {abs_path} ({size_mb:.1f} MB) → gs://{bucket_name}/{blob_name} ...")
    blob.upload_from_filename(abs_path, timeout=600)
    uri = f"gs://{bucket_name}/{blob_name}"
    print(f"Upload complete: {uri}")
    return uri


def create_or_get_corpus() -> rag.RagCorpus:
    """Returns an existing corpus with CORPUS_DISPLAY_NAME or creates one."""
    embedding_model_config = rag.EmbeddingModelConfig(
        publisher_model="publishers/google/models/text-embedding-004"
    )
    for existing in rag.list_corpora():
        if existing.display_name == CORPUS_DISPLAY_NAME:
            print(f"Found existing corpus: '{CORPUS_DISPLAY_NAME}'")
            return existing

    corpus = rag.create_corpus(
        display_name=CORPUS_DISPLAY_NAME,
        description=CORPUS_DESCRIPTION,
        embedding_model_config=embedding_model_config,
    )
    print(f"Created corpus: '{CORPUS_DISPLAY_NAME}'")
    return corpus


def import_pdf_to_corpus(
    corpus_name: str, gcs_uri: str, display_name: str, timeout: int = 900
) -> dict | None:
    """Imports a PDF from GCS into the corpus via REST with layout parsing.

    Layout parsing enables Vertex AI RAG to extract tables, images, and
    document structure — important for pollutant tables and facility maps.
    Falls back to standard parsing if layout parsing is unavailable.

    Uses the REST API directly because the Python SDK's rag.import_files()
    can return INTERNAL errors on some project configurations.
    """
    print(f"Importing '{display_name}' from {gcs_uri} with layout parsing ...")

    credentials, _ = default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(GoogleAuthRequest())
    headers = {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
    }

    parts = corpus_name.split("/")
    location = parts[3] if len(parts) >= 4 else LOCATION
    base_url = f"https://{location}-aiplatform.googleapis.com/v1beta1"
    import_url = f"{base_url}/{corpus_name}/ragFiles:import"

    # Request layout-aware parsing: extracts table structure, figures, and
    # text flow rather than treating the PDF as a flat stream of characters.
    payload = {
        "import_rag_files_config": {
            "gcs_source": {"uris": [gcs_uri]},
            "rag_file_parsing_config": {
                "layout_parser": {}
            },
        }
    }

    try:
        resp = http_requests.post(
            import_url, json=payload, headers=headers, timeout=30
        )
        resp.raise_for_status()
    except http_requests.HTTPError as e:
        # layout_parser may not be enabled for this project/region — retry
        # without it so the import still succeeds.
        if resp.status_code in (400, 404):
            print(
                "  layout_parser not available for this project/region; "
                "retrying with standard parsing ..."
            )
            payload["import_rag_files_config"].pop("rag_file_parsing_config", None)
            resp = http_requests.post(
                import_url, json=payload, headers=headers, timeout=30
            )
            resp.raise_for_status()
        else:
            raise

    op_data = resp.json()
    op_name = op_data.get("name")
    if not op_name:
        print(f"Error: no operation name in response: {op_data}")
        return None

    print(f"Import operation started: {op_name}")
    print(f"Polling for completion (up to {timeout}s) ...")

    op_url = f"{base_url}/{op_name}"
    deadline = time.time() + timeout
    poll_interval = 10.0

    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            op_resp = http_requests.get(op_url, headers=headers, timeout=30)
            op_resp.raise_for_status()
        except Exception as e:
            print(f"  Poll request failed ({e}); retrying ...")
            continue

        op_status = op_resp.json()
        if op_status.get("done"):
            if "error" in op_status:
                err = op_status["error"]
                print(
                    f"Import error: code={err.get('code')} "
                    f"message={err.get('message')}"
                )
                return None
            count = (
                op_status.get("response", {}).get("importedRagFilesCount", "?")
            )
            print(f"Import complete. Files ingested: {count}")
            return op_status

        update_time = (
            op_status.get("metadata", {})
            .get("genericMetadata", {})
            .get("updateTime", "queued")
        )
        print(f"  Still processing ... (last updated: {update_time})")
        poll_interval = min(poll_interval * 1.5, 60)

    print(f"Timed out after {timeout}s. Operation: {op_name}")
    return None


def update_env_file(corpus_name: str, env_file_path: str) -> None:
    try:
        set_key(env_file_path, "RAG_CORPUS", corpus_name)
        print(f"RAG_CORPUS written to {env_file_path}")
    except Exception as e:
        print(f"Warning: could not update .env: {e}")


def list_corpus_files(corpus_name: str) -> None:
    files = list(rag.list_files(corpus_name=corpus_name))
    print(f"Files in corpus ({len(files)} total):")
    for f in files:
        print(f"  • {f.display_name} — {f.name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    initialize_vertex_ai()

    # Phase 1a: upload PDF to GCS
    gcs_uri = upload_pdf_to_gcs(LOCAL_PDF_PATH, GCS_BUCKET_NAME, GCS_BLOB_NAME)

    # Phase 1b: create or reuse corpus
    corpus = create_or_get_corpus()

    # Persist corpus name before import so a re-run skips re-creation
    update_env_file(corpus.name, ENV_FILE_PATH)

    # Phase 1c: import with layout parsing (handles tables + images)
    import_pdf_to_corpus(
        corpus_name=corpus.name,
        gcs_uri=gcs_uri,
        display_name=GCS_BLOB_NAME,
    )

    list_corpus_files(corpus.name)

    # Phase 1d: import structured augmentation document (tables as natural language)
    # Table rows don't embed semantically from the PDF layout parser.  This step
    # adds a supplementary document with natural-language descriptions of each
    # table row, enabling reliable vector search for numeric/tabular queries.
    print("\nImporting structured augmentation document (tables + key facts) ...")
    try:
        from rag.shared_libraries.table_augmentation import upload_and_import_augmentation
        upload_and_import_augmentation(
            corpus_name=corpus.name,
            bucket_name=GCS_BUCKET_NAME,
            project_id=PROJECT_ID,
            location=LOCATION,
        )
    except Exception as e:
        print(f"Warning: augmentation import failed ({e}). "
              "Run manually: python -m rag.shared_libraries.table_augmentation")

    # Phase 1e: extract and import image-heavy pages (maps + CoC forms)
    # These pages yield no useful text via standard extraction; vision
    # processing gives the agent spatial and personnel data it would otherwise lack.
    print("\nRunning visual extraction for maps and chain-of-custody pages ...")
    try:
        from rag.shared_libraries.extract_pdf_visuals import extract_and_import_visuals
        extract_and_import_visuals(
            pdf_path=os.path.abspath(LOCAL_PDF_PATH),
            corpus_name=corpus.name,
        )
    except Exception as e:
        print(f"Warning: visual extraction failed ({e}). "
              "Run 'python -m rag.shared_libraries.extract_pdf_visuals' manually.")

    print("\nSetup complete. Start the agent with: uv run adk web")


if __name__ == "__main__":
    main()
