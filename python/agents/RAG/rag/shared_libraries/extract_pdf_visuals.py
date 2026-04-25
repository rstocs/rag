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

"""Phase 1b: Visual content extraction for image-heavy PDF pages.

Regular text extraction yields nothing useful from two categories of pages:

  1. Facility maps (pages 304-305): the site map labels features like the
     Vertical Integration Tower and outfall locations but conveys spatial
     relationships only as an image.  Text extraction returns only legend
     labels with no positional context.

  2. Chain-of-custody forms (pages 220-228 for project 1105141, pages
     283-289 for project 1106094): these are scanned image pages whose
     72-character text content is just a watermark header.  The actual
     personnel names, signatures, and custody transfer details are invisible
     to the text layer.

This module converts those pages to images with pdf2image (poppler), sends
each image to Gemini vision, and writes the extracted descriptions to text
files that are then uploaded to GCS and imported into the RAG corpus as
additional searchable documents.

Usage (run after prepare_corpus_and_data.py):
    python -m rag.shared_libraries.extract_pdf_visuals
"""

import io
import os
import time
import tempfile

import requests as http_requests
import vertexai
from dotenv import load_dotenv, set_key
from google.auth import default
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud import storage
from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOCAL_PDF_PATH = os.path.join(os.path.dirname(__file__), "..", "testing-report.pdf")

# Pages to process (1-indexed, matching PDF page numbers)
PAGE_GROUPS: dict[str, dict] = {
    "facility_maps": {
        "pages": [304, 305],
        "prompt": (
            "You are analysing a facility map from SpaceX's TPDES permit application "
            "for the Starbase Launch Pad Site in Brownsville, Texas (Permit WQ0005462000).\n\n"
            "Extract ALL information visible in this map image:\n"
            "1. List every labelled structure with its exact label text "
            "(e.g. 'Vertical Integration Tower', 'Starship Test Stand A', "
            "'Starship Test Stand B', 'Power and Control Systems Building', "
            "'OLP Fuel Farm', 'Bunker', 'Starhopper', 'New Bunker').\n"
            "2. List every outfall and sampling point with its number "
            "(e.g. 'Outfall/Sampling Point 1', 'Outfall/Sampling Point 2').\n"
            "3. List every retention basin (e.g. 'Retention Basin 1', 'Retention Basin 2').\n"
            "4. Describe clearly which outfall/sampling point is CLOSEST to the "
            "Vertical Integration Tower and explain the spatial reasoning.\n"
            "5. Describe relative positions: what is to the north, south, east, and west "
            "of the Vertical Integration Tower?\n"
            "6. Describe the deluge system dispersal limits if shown.\n"
            "7. List all legend items and map annotations.\n"
            "8. Note road names and property boundary labels.\n\n"
            "Be precise about spatial relationships — this information will be used "
            "to answer questions about which features are nearest to each other."
        ),
        "output_filename": "extracted_facility_maps.txt",
        "gcs_blob": "extracted_facility_maps.txt",
    },
    "coc_1105141": {
        "pages": list(range(220, 229)),  # pages 220-228 inclusive (9 CoC pages)
        "prompt": (
            "You are analysing a Chain-of-Custody (CoC) form from an environmental "
            "laboratory report for SpaceX (Space Exploration Technologies Corp.) "
            "water samples, submitted to SPL Inc. Kilgore laboratory.\n\n"
            "Extract ALL text visible in this form image, paying particular attention to:\n"
            "1. FULL NAMES of every person who relinquished samples "
            "(typically SpaceX / client-side personnel) — include printed name, "
            "signature text if legible, and their role/title if shown.\n"
            "2. FULL NAMES of every person who received samples "
            "(courier or lab personnel) — same details.\n"
            "3. Dates and times of sample collection, relinquishment, and receipt.\n"
            "4. Sample IDs, collection locations, and container descriptions.\n"
            "5. Project number and client name.\n"
            "6. Any seal, lock, or custody transfer documentation.\n\n"
            "List every person named or whose signature appears anywhere on this form. "
            "If a name is partially legible, include what you can read with a note."
        ),
        "output_filename": "extracted_coc_1105141.txt",
        "gcs_blob": "extracted_coc_1105141.txt",
    },
    "coc_1106094": {
        "pages": list(range(283, 290)),  # pages 283-289 inclusive (7 CoC pages)
        "prompt": (
            "You are analysing a Chain-of-Custody (CoC) form from an environmental "
            "laboratory report for SpaceX (Space Exploration Technologies Corp.) "
            "water samples, submitted to SPL Inc. Kilgore laboratory.\n\n"
            "Extract ALL text visible in this form image, paying particular attention to:\n"
            "1. FULL NAMES of every person who relinquished samples "
            "(typically SpaceX / client-side personnel) — include printed name, "
            "signature text if legible, and their role/title if shown.\n"
            "2. FULL NAMES of every person who received samples "
            "(courier or lab personnel) — same details.\n"
            "3. Dates and times of sample collection, relinquishment, and receipt.\n"
            "4. Sample IDs, collection locations, and container descriptions.\n"
            "5. Project number and client name.\n"
            "6. Any seal, lock, or custody transfer documentation.\n\n"
            "List every person named or whose signature appears anywhere on this form. "
            "If a name is partially legible, include what you can read with a note."
        ),
        "output_filename": "extracted_coc_1106094.txt",
        "gcs_blob": "extracted_coc_1106094.txt",
    },
}

# Gemini vision needs a region that supports multimodal models
VISION_LOCATION = "us-central1"

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

cwd_env = os.path.join(os.getcwd(), ".env")
ENV_FILE_PATH = cwd_env if os.path.exists(cwd_env) else os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", ".env")
)
load_dotenv(ENV_FILE_PATH, override=True)

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
if not PROJECT_ID:
    raise ValueError("GOOGLE_CLOUD_PROJECT is not set.")

LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-west1")

_staging_raw = os.getenv("STAGING_BUCKET", "")
GCS_BUCKET_NAME = _staging_raw.removeprefix("gs://").rstrip("/")
if not GCS_BUCKET_NAME:
    raise ValueError("STAGING_BUCKET is not set.")

RAG_CORPUS = os.getenv("RAG_CORPUS")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pdf_page_to_png_bytes(pdf_path: str, page_number: int) -> bytes:
    """Convert a single PDF page (1-indexed) to PNG bytes via pdf2image."""
    try:
        from pdf2image import convert_from_path
    except ImportError:
        raise ImportError(
            "pdf2image is required. Run: uv add pdf2image"
        )

    images = convert_from_path(
        pdf_path,
        first_page=page_number,
        last_page=page_number,
        dpi=200,
    )
    buf = io.BytesIO()
    images[0].save(buf, format="PNG")
    return buf.getvalue()


def _call_gemini_vision(image_bytes: bytes, prompt: str) -> str:
    """Send a PNG image to Gemini and return the text response."""
    client = genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=VISION_LOCATION,
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            prompt,
        ],
    )
    return response.text


def extract_group(group_name: str, group_cfg: dict, pdf_path: str) -> str:
    """Process all pages in a group and return the concatenated extraction."""
    pages = group_cfg["pages"]
    prompt = group_cfg["prompt"]
    parts: list[str] = [
        f"# Extracted content: {group_name}\n"
        f"# Source: testing-report.pdf, pages {pages[0]}–{pages[-1]}\n"
    ]

    for page_num in pages:
        print(f"  Processing page {page_num} ...")
        try:
            png_bytes = _pdf_page_to_png_bytes(pdf_path, page_num)
            text = _call_gemini_vision(png_bytes, prompt)
            parts.append(f"\n## Page {page_num}\n{text}\n")
        except Exception as e:
            print(f"  Warning: page {page_num} failed ({e})")
            parts.append(f"\n## Page {page_num}\n[Extraction failed: {e}]\n")
        # Respect Gemini rate limits
        time.sleep(2)

    return "\n".join(parts)


def upload_text_to_gcs(content: str, bucket_name: str, blob_name: str) -> str:
    """Upload a string as a UTF-8 text file to GCS and return gs:// URI."""
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(content.encode("utf-8"), content_type="text/plain")
    uri = f"gs://{bucket_name}/{blob_name}"
    print(f"  Uploaded → {uri}")
    return uri


def import_gcs_file_to_corpus(corpus_name: str, gcs_uri: str, timeout: int = 300) -> None:
    """Import a single GCS file into the RAG corpus via REST."""
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

    payload = {
        "import_rag_files_config": {
            "gcs_source": {"uris": [gcs_uri]},
        }
    }
    resp = http_requests.post(import_url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()

    op_name = resp.json().get("name")
    if not op_name:
        print(f"  No operation name in response; skipping poll.")
        return

    op_url = f"{base_url}/{op_name}"
    deadline = time.time() + timeout
    poll_interval = 5.0

    while time.time() < deadline:
        time.sleep(poll_interval)
        op_resp = http_requests.get(op_url, headers=headers, timeout=30)
        op_status = op_resp.json()
        if op_status.get("done"):
            if "error" in op_status:
                err = op_status["error"]
                print(f"  Import error: {err.get('message')}")
            else:
                print(f"  Import complete: {gcs_uri}")
            return
        poll_interval = min(poll_interval * 1.5, 30)

    print(f"  Import timed out for {gcs_uri}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def extract_and_import_visuals(
    pdf_path: str | None = None,
    corpus_name: str | None = None,
) -> None:
    """Run visual extraction for all page groups and import into the corpus.

    Can be called from prepare_corpus_and_data.py or run standalone.
    """
    pdf_path = pdf_path or os.path.abspath(LOCAL_PDF_PATH)
    corpus_name = corpus_name or RAG_CORPUS

    if not corpus_name:
        raise ValueError(
            "RAG_CORPUS is not set. Run prepare_corpus_and_data.py first."
        )
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    vertexai.init(project=PROJECT_ID, location=LOCATION)

    for group_name, group_cfg in PAGE_GROUPS.items():
        print(f"\n--- Extracting: {group_name} ---")
        content = extract_group(group_name, group_cfg, pdf_path)

        gcs_uri = upload_text_to_gcs(content, GCS_BUCKET_NAME, group_cfg["gcs_blob"])
        print(f"  Importing into corpus ...")
        import_gcs_file_to_corpus(corpus_name, gcs_uri)

    print("\nVisual extraction complete.")


def main() -> None:
    extract_and_import_visuals()


if __name__ == "__main__":
    main()
