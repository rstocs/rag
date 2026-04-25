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

"""RAG-specific evaluation using Vertex AI LLM-as-judge metrics.

Complements the ROUGE-based test_eval.py with five semantic metrics:

  Metric                        Scale   Threshold   What it tests
  ─────────────────────────────────────────────────────────────────
  groundedness                  0 / 1   ≥ 0.75      Response only uses info
                                                     from retrieved context
  coherence                     1 – 5   ≥ 3.5       Logical flow and structure
  fluency                       1 – 5   ≥ 3.5       Grammar and natural phrasing
  question_answering_quality    1 – 5   ≥ 3.0       Overall QA quality
  instruction_following         1 – 5   ≥ 3.0       Question is fully addressed

Groundedness scale (binary):
  1 = Fully grounded — all response content is attributable to context
  0 = Not fully grounded — part of the response uses outside knowledge

Other metric scales (1–5):
  5 = Excellent  4 = Good  3 = Acceptable  2 = Poor  1 = Very poor

Estimated runtime: ~8–15 min depending on GCP quota.
Only test cases with expected tool use (RAG retrieval cases) are evaluated.
Greetings and farewells are skipped since they have no retrieved context.

Usage:
    uv run pytest eval/test_eval_rag_metrics.py -v -s
"""

import asyncio
import json
import os
import pathlib

import dotenv
import pandas as pd
import pytest
import vertexai
from google.adk.runners import InMemoryRunner
from google.genai.types import Part, UserContent
from vertexai.preview import rag
from vertexai.preview.evaluation import EvalTask

from rag.agent import root_agent

dotenv.load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = pathlib.Path(__file__).parent / "data"

# Vertex AI project / location for the LLM judge
_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
# Evaluation API requires a real region — "global" is not supported
_EVAL_LOCATION = "us-central1"

# Metric names (string constants accepted by EvalTask)
METRICS = [
    "groundedness",
    "coherence",
    "fluency",
    "question_answering_quality",
    "instruction_following",
]

# Minimum acceptable mean score per metric across all evaluated cases
THRESHOLDS: dict[str, float] = {
    "groundedness/mean": 0.75,           # binary 0/1 — 75% must be grounded
    "coherence/mean": 3.5,               # 1-5 scale
    "fluency/mean": 3.5,                 # 1-5 scale
    "question_answering_quality/mean": 3.0,  # 1-5 scale
    "instruction_following/mean": 3.0,   # 1-5 scale
}

NUM_AGENT_RUNS = 5  # agent is run this many times per test case; each run
                    # produces an independent row for the judge to score, so
                    # the mean covers both agent non-determinism and judge variance

pytest_plugins = ("pytest_asyncio",)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_rag_cases() -> list[dict]:
    """Load all test cases that require RAG retrieval (skip greetings/farewells)."""
    cases = []
    for fname in ["conversation.test.json", "conversation_edge.test.json"]:
        data = json.loads((DATA_DIR / fname).read_text())
        for case in data:
            if case.get("expected_tool_use"):
                cases.append(case)
    return cases


async def _run_agent_and_capture(
    query: str, max_retries: int = 3
) -> tuple[str, str]:
    """Run the agent for a single query and return (response, grounding_context).

    Context strategy
    ----------------
    Gemini 2 uses native built-in retrieval (no function_response events), so we
    can't observe exactly which chunks the model received.  To give the
    groundedness judge a complete and accurate grounding surface we combine two
    sources:

    1. Full augmentation document (AUGMENTATION_TEXT) — structured natural-language
       descriptions of every table, fact, contact, and location in the corpus.
       This covers the vast majority of what the agent can legitimately say.

    2. Query-specific retrieved chunks — narrative sections (water cycle, spatial
       descriptions, Spanish public notices, etc.) that live in the PDF text rather
       than the augmentation document.

    Together they represent everything the agent had legitimate access to.  A
    response that only uses document facts will be grounded; a hallucinated claim
    will not appear in either source and will correctly fail.

    Retry logic: if the agent returns an empty response (e.g. due to a transient
    429), wait and retry with exponential back-off before recording the row.
    """
    from rag.shared_libraries.table_augmentation import AUGMENTATION_TEXT

    response = ""
    for attempt in range(max_retries):
        try:
            runner = InMemoryRunner(agent=root_agent)
            session = await runner.session_service.create_session(
                app_name=runner.app_name, user_id="rag_eval_user"
            )
            content = UserContent(parts=[Part(text=query)])
            response_parts: list[str] = []

            for event in runner.run(
                user_id=session.user_id,
                session_id=session.id,
                new_message=content,
            ):
                if not event.content or not event.content.parts:
                    continue
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        response_parts.append(part.text)

            response = "\n".join(filter(None, response_parts))
            if response:
                break
        except Exception as e:
            print(f"  Warning: agent run failed (attempt {attempt + 1}): {e}")

        if attempt < max_retries - 1:
            delay = 15 * (attempt + 1)
            print(f"  Retrying in {delay}s ...")
            await asyncio.sleep(delay)

    # Build grounding context: augmentation document + query-specific retrieval.
    # The augmentation document covers all structured facts (tables 1/2/3/17,
    # GPS, contacts, dates, qualifiers, outfall 002 availability).
    # Query retrieval adds narrative corpus text for cases not in augmentation
    # (water cycle, facility maps, Spanish public notices).
    retrieved_text = ""
    rag_corpus = os.getenv("RAG_CORPUS", "")
    if rag_corpus:
        try:
            rag_resp = rag.retrieval_query(
                text=query,
                rag_resources=[rag.RagResource(rag_corpus=rag_corpus)],
                rag_retrieval_config=rag.RagRetrievalConfig(
                    top_k=25,
                    filter=rag.Filter(vector_distance_threshold=0.3),
                ),
            )
            chunks = [ctx.text for ctx in rag_resp.contexts.contexts if ctx.text]
            retrieved_text = "\n\n".join(chunks)
        except Exception as e:
            print(f"  Warning: context retrieval failed ({e})")

    # Query-specific chunks first (most relevant), then the full augmentation
    # document so every structured fact is verifiable.  Cap at 25 000 chars —
    # well within the judge's token budget.
    context = (retrieved_text + "\n\n" + AUGMENTATION_TEXT)[:25000]

    return response, context


async def _build_eval_dataframe() -> pd.DataFrame:
    """Run the agent NUM_AGENT_RUNS times per RAG test case.

    Each run produces one independent row: a fresh agent call yields a
    different response (temperature variance), which the judge scores
    separately.  The EvalTask mean therefore averages over both agent
    non-determinism and judge variance — giving stable metric estimates.

    Total rows = len(RAG cases) × NUM_AGENT_RUNS.

    Column layout required by each metric:

      groundedness               → prompt (question + context), response
      coherence                  → prompt (question), response
      fluency                    → response only
      question_answering_quality → instruction, context, response
      instruction_following      → instruction, response

    All columns are included so one EvalTask covers all five metrics.
    The groundedness prompt embeds the retrieved context so the judge
    can verify the response does not use outside knowledge.
    """
    cases = _load_rag_cases()
    rows = []

    total_runs = 0
    for case in cases:
        query = case["query"]
        for run_idx in range(NUM_AGENT_RUNS):
            print(
                f"  run {run_idx + 1}/{NUM_AGENT_RUNS}: {query[:65]}..."
            )
            if total_runs > 0:
                await asyncio.sleep(5)  # avoid 429 RESOURCE_EXHAUSTED
            total_runs += 1
            response, context = await _run_agent_and_capture(query)

            grounded_prompt = (
                f"Context (retrieved from the testing report corpus):\n"
                f"{context}\n\nQuestion: {query}"
                if context
                else f"Question: {query}"
            )

            rows.append(
                {
                    "prompt": grounded_prompt,
                    "instruction": query,
                    "context": context,
                    "response": response,
                    "reference": case["reference"],
                    "run": run_idx + 1,
                }
            )

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def eval_dataframe():
    """Module-scoped: build the DataFrame once and share across all metric tests.

    Runs the agent NUM_AGENT_RUNS times per RAG case (total rows =
    RAG cases × NUM_AGENT_RUNS) then passes the full DataFrame to each
    metric test so the judge scores every run independently.
    """
    vertexai.init(project=_PROJECT, location=_EVAL_LOCATION)
    return asyncio.run(_build_eval_dataframe())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _run_metric(df: pd.DataFrame, metric: str) -> dict[str, float]:
    """Run a single Vertex AI metric and return the summary metrics dict."""
    task = EvalTask(dataset=df, metrics=[metric])
    result = task.evaluate()
    return result.summary_metrics


def _assert_threshold(summary: dict[str, float], key: str, threshold: float) -> None:
    score = summary.get(key)
    assert score is not None, (
        f"Metric key '{key}' not found in results. "
        f"Available keys: {list(summary.keys())}"
    )
    assert score >= threshold, (
        f"{key} = {score:.3f} is below the required threshold of {threshold}. "
        f"Check eval_dataframe rows for low-scoring responses."
    )


def test_groundedness(eval_dataframe):
    """At least 75% of responses must be fully grounded in retrieved context.

    Groundedness is binary (0 = not grounded, 1 = fully grounded).
    A mean of 0.75 means at most 1 in 4 answers uses outside knowledge.
    For a closed-domain compliance system this should be higher over time.
    """
    summary = _run_metric(eval_dataframe, "groundedness")
    print("\nGroundedness summary:", summary)
    _assert_threshold(summary, "groundedness/mean", THRESHOLDS["groundedness/mean"])


def test_coherence(eval_dataframe):
    """Responses must be logically structured and coherent (mean ≥ 3.5 / 5).

    Scale: 5=seamless flow, 4=strong, 3=mostly understandable,
           2=weak structure, 1=incoherent.
    """
    summary = _run_metric(eval_dataframe, "coherence")
    print("\nCoherence summary:", summary)
    _assert_threshold(summary, "coherence/mean", THRESHOLDS["coherence/mean"])


def test_fluency(eval_dataframe):
    """Responses must use natural, grammatically correct language (mean ≥ 3.5 / 5).

    Scale: 5=completely fluent, 4=mostly fluent, 3=some grammatical issues,
           2=frequent errors, 1=incomprehensible.
    """
    summary = _run_metric(eval_dataframe, "fluency")
    print("\nFluency summary:", summary)
    _assert_threshold(summary, "fluency/mean", THRESHOLDS["fluency/mean"])


def test_question_answering_quality(eval_dataframe):
    """Overall QA quality must be acceptable (mean ≥ 3.0 / 5).

    Combines instruction-following, groundedness, completeness, and fluency.
    Scale: 5=very good, 4=good, 3=ok, 2=bad, 1=very bad.
    """
    summary = _run_metric(eval_dataframe, "question_answering_quality")
    print("\nQA quality summary:", summary)
    _assert_threshold(
        summary,
        "question_answering_quality/mean",
        THRESHOLDS["question_answering_quality/mean"],
    )


def test_instruction_following(eval_dataframe):
    """Responses must address what was asked (mean ≥ 3.0 / 5).

    Scale: 5=complete fulfillment, 4=good, 3=partial, 2=poor, 1=none.
    """
    summary = _run_metric(eval_dataframe, "instruction_following")
    print("\nInstruction-following summary:", summary)
    _assert_threshold(
        summary,
        "instruction_following/mean",
        THRESHOLDS["instruction_following/mean"],
    )
    Scale: 5=complete fulfillment, 4=good, 3=partial, 2=poor, 1=none.
    """
    summary = _run_metric(eval_dataframe, "instruction_following")
    print("\nInstruction-following summary:", summary)
    _assert_threshold(
        summary,
        "instruction_following/mean",
        THRESHOLDS["instruction_following/mean"],
    )
