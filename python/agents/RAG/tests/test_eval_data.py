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

"""Unit tests for eval/data/*.json.

Validates schema, thresholds, and content conventions before running the
full agent eval (which takes ~7 minutes and requires live credentials).
Catches data-format regressions without any network calls.
"""

import json
import pathlib

import pytest

DATA_DIR = pathlib.Path(__file__).parent.parent / "eval" / "data"
EVAL_FILES = [
    DATA_DIR / "conversation.test.json",
    DATA_DIR / "conversation_edge.test.json",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def load(path: pathlib.Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)


# ── File existence ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", EVAL_FILES)
def test_file_exists(path):
    assert path.exists(), f"Missing eval file: {path}"


# ── Top-level schema ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", EVAL_FILES)
def test_is_list(path):
    data = load(path)
    assert isinstance(data, list), f"{path.name} must be a JSON array"


@pytest.mark.parametrize("path", EVAL_FILES)
def test_minimum_case_count(path):
    """Each file must have at least 10 cases to be meaningful."""
    data = load(path)
    assert len(data) >= 10, f"{path.name} has only {len(data)} cases"


# ── Required fields ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", EVAL_FILES)
def test_required_fields(path):
    data = load(path)
    for i, case in enumerate(data):
        assert "query" in case, f"{path.name}[{i}] missing 'query'"
        assert "expected_tool_use" in case, f"{path.name}[{i}] missing 'expected_tool_use'"
        assert "reference" in case, f"{path.name}[{i}] missing 'reference'"


@pytest.mark.parametrize("path", EVAL_FILES)
def test_no_empty_queries(path):
    data = load(path)
    for i, case in enumerate(data):
        assert case["query"].strip(), f"{path.name}[{i}] has empty query"


@pytest.mark.parametrize("path", EVAL_FILES)
def test_no_empty_references(path):
    data = load(path)
    for i, case in enumerate(data):
        assert case["reference"].strip(), f"{path.name}[{i}] has empty reference"


# ── Tool use schema ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", EVAL_FILES)
def test_expected_tool_use_is_list(path):
    data = load(path)
    for i, case in enumerate(data):
        assert isinstance(case["expected_tool_use"], list), (
            f"{path.name}[{i}] expected_tool_use must be a list"
        )


@pytest.mark.parametrize("path", EVAL_FILES)
def test_tool_use_entries_have_correct_schema(path):
    data = load(path)
    for i, case in enumerate(data):
        for j, tool in enumerate(case["expected_tool_use"]):
            assert "tool_name" in tool, f"{path.name}[{i}] tool[{j}] missing 'tool_name'"
            assert "tool_input" in tool, f"{path.name}[{i}] tool[{j}] missing 'tool_input'"
            assert "query" in tool["tool_input"], (
                f"{path.name}[{i}] tool[{j}] tool_input missing 'query'"
            )


@pytest.mark.parametrize("path", EVAL_FILES)
def test_tool_name_is_correct(path):
    """All tool calls must reference the retrieve_rag_documentation tool."""
    data = load(path)
    for i, case in enumerate(data):
        for j, tool in enumerate(case["expected_tool_use"]):
            assert tool["tool_name"] == "retrieve_rag_documentation", (
                f"{path.name}[{i}] tool[{j}] has unexpected tool_name: "
                f"'{tool['tool_name']}'"
            )


# ── Content checks ────────────────────────────────────────────────────────────

def test_core_file_has_greeting():
    """Core conversation must start with a greeting (no tool use)."""
    data = load(DATA_DIR / "conversation.test.json")
    first = data[0]
    assert first["expected_tool_use"] == []


def test_core_file_has_farewell():
    """Core conversation must end with a farewell (no tool use)."""
    data = load(DATA_DIR / "conversation.test.json")
    last = data[-1]
    assert last["expected_tool_use"] == []


def test_core_file_has_spanish_case():
    """Core conversation must include a Spanish-language test case."""
    data = load(DATA_DIR / "conversation.test.json")
    spanish_cases = [c for c in data if any(
        ord(ch) > 127 for ch in c["query"]
    )]
    assert spanish_cases, "No Spanish query found in conversation.test.json"


def test_edge_file_has_off_topic_case():
    """Edge cases must include an off-topic question to verify scope refusal."""
    data = load(DATA_DIR / "conversation_edge.test.json")
    off_topic = [c for c in data if c["expected_tool_use"] == [] and
                 any(kw in c["query"].lower() for kw in ["stock", "price", "weather"])]
    assert off_topic, "No off-topic refusal case found in conversation_edge.test.json"


# ── Threshold config ──────────────────────────────────────────────────────────

def test_config_file_exists():
    assert (DATA_DIR / "test_config.json").exists()


def test_config_has_response_match_threshold():
    with open(DATA_DIR / "test_config.json") as f:
        config = json.load(f)
    assert "criteria" in config
    assert "response_match_score" in config["criteria"]


def test_response_match_threshold_is_reasonable():
    """Threshold must be <= 0.4 (0.4 is too strict for verbose compliance answers)."""
    with open(DATA_DIR / "test_config.json") as f:
        config = json.load(f)
    threshold = config["criteria"]["response_match_score"]
    assert 0.1 <= threshold <= 0.4, (
        f"response_match_score threshold {threshold} is outside expected range [0.1, 0.4]"
    )
