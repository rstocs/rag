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

"""Unit tests for rag/shared_libraries/prepare_corpus_and_data.py.

Verifies configuration constants and module-level logic without making any
network calls.  Uses monkeypatching to avoid hitting GCS or Vertex AI.
"""

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _patch_external_deps(monkeypatch):
    """Prevent any real network calls during import and test execution."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-west1")
    monkeypatch.setenv("STAGING_BUCKET", "gs://test-bucket")


# ── Configuration constants ───────────────────────────────────────────────────

def test_corpus_display_name_references_testing_report():
    from rag.shared_libraries.prepare_corpus_and_data import CORPUS_DISPLAY_NAME
    name_lower = CORPUS_DISPLAY_NAME.lower()
    assert "testing" in name_lower or "tpdes" in name_lower or "spacex" in name_lower, (
        f"CORPUS_DISPLAY_NAME '{CORPUS_DISPLAY_NAME}' should reference the testing report"
    )


def test_corpus_description_is_meaningful():
    from rag.shared_libraries.prepare_corpus_and_data import CORPUS_DESCRIPTION
    assert len(CORPUS_DESCRIPTION) > 50, "CORPUS_DESCRIPTION is too short"
    assert "SpaceX" in CORPUS_DESCRIPTION or "TPDES" in CORPUS_DESCRIPTION


def test_gcs_blob_name_is_pdf():
    from rag.shared_libraries.prepare_corpus_and_data import GCS_BLOB_NAME
    assert GCS_BLOB_NAME.endswith(".pdf")
    assert "testing" in GCS_BLOB_NAME.lower()


def test_local_pdf_path_points_to_testing_report():
    from rag.shared_libraries.prepare_corpus_and_data import LOCAL_PDF_PATH
    assert "testing-report.pdf" in LOCAL_PDF_PATH


def test_env_file_path_ends_with_env():
    from rag.shared_libraries.prepare_corpus_and_data import ENV_FILE_PATH
    assert ENV_FILE_PATH.endswith(".env")


# ── upload_pdf_to_gcs ─────────────────────────────────────────────────────────

def test_upload_raises_file_not_found_for_missing_pdf(tmp_path):
    from rag.shared_libraries.prepare_corpus_and_data import upload_pdf_to_gcs
    missing = str(tmp_path / "does_not_exist.pdf")
    with pytest.raises(FileNotFoundError, match="PDF not found"):
        upload_pdf_to_gcs(missing, "some-bucket", "some-blob")


def test_upload_returns_gcs_uri(tmp_path):
    """upload_pdf_to_gcs should return a gs:// URI when the file exists."""
    from rag.shared_libraries.prepare_corpus_and_data import upload_pdf_to_gcs

    # Create a minimal dummy PDF
    dummy_pdf = tmp_path / "test.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 dummy")

    mock_client = MagicMock()
    mock_bucket = MagicMock()
    mock_bucket.exists.return_value = True
    mock_blob = MagicMock()
    mock_client.bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob

    with patch(
        "rag.shared_libraries.prepare_corpus_and_data.storage.Client",
        return_value=mock_client,
    ):
        uri = upload_pdf_to_gcs(str(dummy_pdf), "my-bucket", "output.pdf")

    assert uri == "gs://my-bucket/output.pdf"
    mock_blob.upload_from_filename.assert_called_once()


# ── update_env_file ───────────────────────────────────────────────────────────

def test_update_env_file_writes_rag_corpus(tmp_path):
    from rag.shared_libraries.prepare_corpus_and_data import update_env_file

    env_path = tmp_path / ".env"
    env_path.write_text("GOOGLE_CLOUD_PROJECT=test\n")

    corpus_name = "projects/123/locations/us-west1/ragCorpora/456"
    update_env_file(corpus_name, str(env_path))

    content = env_path.read_text()
    assert "RAG_CORPUS" in content
    assert corpus_name in content


# ── import_pdf_to_corpus ──────────────────────────────────────────────────────

def test_import_returns_none_on_missing_op_name(monkeypatch):
    """If the REST API returns no operation name, import should return None."""
    from rag.shared_libraries.prepare_corpus_and_data import import_pdf_to_corpus

    mock_resp = MagicMock()
    mock_resp.json.return_value = {}  # no 'name' key
    mock_resp.raise_for_status.return_value = None

    with patch(
        "rag.shared_libraries.prepare_corpus_and_data.http_requests.post",
        return_value=mock_resp,
    ):
        result = import_pdf_to_corpus(
            corpus_name="projects/123/locations/us-west1/ragCorpora/456",
            gcs_uri="gs://bucket/file.pdf",
            display_name="file.pdf",
        )

    assert result is None
