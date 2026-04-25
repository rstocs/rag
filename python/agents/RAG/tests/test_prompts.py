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

"""Unit tests for rag/prompts.py.

These tests verify that the system prompt contains the critical facts and
behavioural rules that the agent depends on.  They run without any network
calls or credentials.
"""

import pytest

from rag.prompts import return_instructions_root


@pytest.fixture(scope="module")
def prompt():
    return return_instructions_root()


def test_returns_non_empty_string(prompt):
    assert isinstance(prompt, str)
    assert len(prompt) > 500


# ── Bilingual language rule ───────────────────────────────────────────────────

def test_critical_language_rule_present(prompt):
    """The CRITICAL LANGUAGE RULE must exist to prevent Spanish carryover.

    Without this rule, a Spanish question earlier in the conversation causes
    subsequent English questions to be answered in Spanish.
    """
    assert "CRITICAL" in prompt


def test_language_per_turn_instruction(prompt):
    """The rule must explicitly say each turn resets language independently."""
    assert "independently" in prompt or "each turn" in prompt.lower()


def test_corpus_language_not_source(prompt):
    """Retrieved Spanish chunks must not influence the response language."""
    assert "corpus" in prompt.lower()


# ── Compliance context rule ───────────────────────────────────────────────────

def test_compliance_context_rule_present(prompt):
    """Agent must be instructed to state MAL exceedance, not just list values."""
    assert "MAL" in prompt
    assert "exceed" in prompt.lower()


# ── QC data rule ─────────────────────────────────────────────────────────────

def test_qc_deprioritisation_rule(prompt):
    """Agent must not include lab QC rows (ICL, ICV, etc.) unless asked.

    Without this rule the agent surfaces calibration data instead of
    the actual discharge measurements.
    """
    assert "QC" in prompt or "quality control" in prompt.lower()


# ── Document scope ────────────────────────────────────────────────────────────

def test_permit_number_present(prompt):
    """Prompt must name the document so the agent stays in scope."""
    assert "WQ0005462000" in prompt


def test_spacex_named(prompt):
    """Prompt must name the applicant."""
    assert "SpaceX" in prompt or "Space Exploration" in prompt


# ── Key document facts ────────────────────────────────────────────────────────

def test_gps_coordinates_present(prompt):
    """GPS coordinates must be in the prompt — they live inside a URL and
    won't be retrieved semantically from the corpus."""
    assert "25.996944" in prompt
    assert "97.156388" in prompt


def test_submission_date_present(prompt):
    """Application submission date must be in the prompt for summary/status answers."""
    assert "July 1, 2024" in prompt or "July 1" in prompt


def test_carolyn_wood_present(prompt):
    """Technical contact must be named in prompt for reliable contact answers."""
    assert "Carolyn Wood" in prompt


def test_katy_groom_present(prompt):
    """Administrative contact must be named in prompt."""
    assert "Katy Groom" in prompt


def test_zachary_smith_present(prompt):
    """CoC sample collector (found via vision extraction) must be in prompt."""
    assert "Zachary Smith" in prompt


def test_both_outfalls_described(prompt):
    """Both Outfall 001 and 002 discharge routes must be described."""
    assert "Outfall 001" in prompt
    assert "Outfall 002" in prompt


def test_document_page_count_present(prompt):
    """483-page scope helps the agent give correct summaries."""
    assert "483" in prompt
