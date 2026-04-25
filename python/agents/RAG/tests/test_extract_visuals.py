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

"""Unit tests for rag/shared_libraries/extract_pdf_visuals.py.

Verifies page-group configuration and helper behaviour without calling
Gemini, GCS, or any other external service.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-west1")
    monkeypatch.setenv("STAGING_BUCKET", "gs://test-bucket")


# ── PAGE_GROUPS configuration ─────────────────────────────────────────────────

def test_all_expected_groups_present():
    from rag.shared_libraries.extract_pdf_visuals import PAGE_GROUPS
    assert "facility_maps" in PAGE_GROUPS
    assert "coc_1105141" in PAGE_GROUPS
    assert "coc_1106094" in PAGE_GROUPS


def test_each_group_has_required_keys():
    from rag.shared_libraries.extract_pdf_visuals import PAGE_GROUPS
    required = {"pages", "prompt", "output_filename", "gcs_blob"}
    for name, cfg in PAGE_GROUPS.items():
        missing = required - cfg.keys()
        assert not missing, f"PAGE_GROUPS['{name}'] missing keys: {missing}"


def test_pages_are_non_empty_lists_of_ints():
    from rag.shared_libraries.extract_pdf_visuals import PAGE_GROUPS
    for name, cfg in PAGE_GROUPS.items():
        pages = cfg["pages"]
        assert isinstance(pages, list), f"'{name}' pages must be a list"
        assert len(pages) > 0, f"'{name}' pages must not be empty"
        assert all(isinstance(p, int) and p > 0 for p in pages), (
            f"'{name}' pages must be positive integers"
        )


def test_prompts_are_descriptive():
    """Each prompt must be long enough to guide Gemini meaningfully."""
    from rag.shared_libraries.extract_pdf_visuals import PAGE_GROUPS
    for name, cfg in PAGE_GROUPS.items():
        assert len(cfg["prompt"]) > 100, (
            f"'{name}' prompt is too short ({len(cfg['prompt'])} chars)"
        )


def test_gcs_blobs_are_txt_files():
    from rag.shared_libraries.extract_pdf_visuals import PAGE_GROUPS
    for name, cfg in PAGE_GROUPS.items():
        assert cfg["gcs_blob"].endswith(".txt"), (
            f"'{name}' gcs_blob should be a .txt file"
        )


# ── Critical page numbers ─────────────────────────────────────────────────────

def test_site_map_page_305_included():
    """Page 305 is the site map with the Vertical Integration Tower label.

    This is the most important map page for spatial questions.
    """
    from rag.shared_libraries.extract_pdf_visuals import PAGE_GROUPS
    assert 305 in PAGE_GROUPS["facility_maps"]["pages"]


def test_coc_1105141_covers_all_nine_pages():
    """Project 1105141 CoC is 9 pages (220-228). All must be processed."""
    from rag.shared_libraries.extract_pdf_visuals import PAGE_GROUPS
    pages = PAGE_GROUPS["coc_1105141"]["pages"]
    assert 220 in pages
    assert 228 in pages
    assert len(pages) == 9


def test_coc_1106094_starts_at_283():
    """Project 1106094 CoC starts at page 283 (confirmed by text extraction)."""
    from rag.shared_libraries.extract_pdf_visuals import PAGE_GROUPS
    pages = PAGE_GROUPS["coc_1106094"]["pages"]
    assert min(pages) == 283


# ── VISION_LOCATION ───────────────────────────────────────────────────────────

def test_vision_location_is_not_global():
    """'global' does not support Gemini multimodal — must be a real region."""
    from rag.shared_libraries.extract_pdf_visuals import VISION_LOCATION
    assert VISION_LOCATION != "global"
    assert VISION_LOCATION, "VISION_LOCATION must not be empty"


def test_vision_location_looks_like_region():
    from rag.shared_libraries.extract_pdf_visuals import VISION_LOCATION
    # Regions follow the pattern <continent>-<direction><number>
    assert "-" in VISION_LOCATION, (
        f"VISION_LOCATION '{VISION_LOCATION}' does not look like a GCP region"
    )


# ── upload_text_to_gcs ────────────────────────────────────────────────────────

def test_upload_text_returns_gcs_uri():
    from rag.shared_libraries.extract_pdf_visuals import upload_text_to_gcs

    mock_client = MagicMock()
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_client.bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob

    with patch(
        "rag.shared_libraries.extract_pdf_visuals.storage.Client",
        return_value=mock_client,
    ):
        uri = upload_text_to_gcs("some content", "my-bucket", "output.txt")

    assert uri == "gs://my-bucket/output.txt"
    mock_blob.upload_from_string.assert_called_once()


def test_upload_text_encodes_as_utf8():
    """Content must be uploaded as UTF-8 to preserve Spanish characters."""
    from rag.shared_libraries.extract_pdf_visuals import upload_text_to_gcs

    captured = {}

    mock_blob = MagicMock()

    def capture_upload(data, content_type):
        captured["data"] = data
        captured["content_type"] = content_type

    mock_blob.upload_from_string.side_effect = capture_upload

    mock_client = MagicMock()
    mock_bucket = MagicMock()
    mock_client.bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob

    spanish_text = "Emisarios y áreas de contención"
    with patch(
        "rag.shared_libraries.extract_pdf_visuals.storage.Client",
        return_value=mock_client,
    ):
        upload_text_to_gcs(spanish_text, "bucket", "blob.txt")

    assert captured["data"] == spanish_text.encode("utf-8")
    assert captured["content_type"] == "text/plain"
