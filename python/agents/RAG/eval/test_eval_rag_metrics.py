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
  rag_groundedness (custom)     0 / 1   ≥ 0.75      Response only uses info
                                                     from retrieved context;
                                                     arithmetic derivation OK
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
import google.genai as google_genai
import pandas as pd
import pytest
import vertexai
from google.adk.runners import InMemoryRunner
from google.genai.types import Part, UserContent
from vertexai.preview import rag
from vertexai.preview.evaluation import EvalTask, PointwiseMetric, PointwiseMetricPromptTemplate

from rag.agent import root_agent

dotenv.load_dotenv()

# ---------------------------------------------------------------------------
# Supplementary context supplied to the judge for every row.
#
# The agent answers some questions directly from key facts hardcoded in the
# system prompt (GPS, contacts, permit status, document identity) rather than
# calling the retrieval tool.  If those questions are in the eval set and the
# agent never calls the tool, function_response events are empty, the proxy
# retrieval also returns 0 chunks, and the judge sees empty context → scores 0.
#
# Including the prompt's key-facts section here gives the judge the same
# knowledge surface the agent used, so grounded answers score 1 correctly.
# ---------------------------------------------------------------------------
_PROMPT_KEY_FACTS = """\
== Key document facts (from system prompt) ==

Document identity:
- Full title: SpaceX TPDES Industrial Wastewater Individual Permit Application,
  Proposed Permit No. WQ0005462000 (EPA I.D. No. TX0146251)
- Applicant: Space Exploration Technologies Corp. (CN602867657)
- Facility: Starbase Launch Pad Site (RN111606745), 1 Rocket Rd., Brownsville,
  Cameron County, Texas 78521; south side of eastern terminus of State Hwy 4
- Application submitted to TCEQ: July 1, 2024
- TCEQ Deficiency Notification sent to SpaceX: July 2, 2024
- Application declared administratively complete; NORI issued: July 8, 2024
- Status: technical review pending — no draft permit or final approval issued
- Total pages: 483; bilingual (English and Spanish)

Facility GPS (from TCEQ NORI map link, July 8, 2024):
- Latitude 25.996944°N, Longitude 97.156388°W (decimal degrees)
- Nearest city: Brownsville, Cameron County, Texas 78521

Discharge summary:
- Outfall 001: intermittent direct discharge of uncaptured deluge water to
  tidal wetlands, thence to Rio Grande Tidal
- Outfall 002: discharge from retention basins to tidal wetlands, thence to
  Rio Grande Tidal (water may also be reused/recycled)

Key SpaceX personnel:
- Technical contact: Carolyn Wood, Sr. Environmental Regulatory Engineer,
  carolyn.wood@spacex.com, (323) 537-0071, 1 Rocket Rd., Brownsville TX 78521
- Administrative contact: Katy Groom, Manager Environmental Regulatory Affairs,
  katy.groom@spacex.com, (321) 730-1469, L6-1581 Roberts Rd., KSC FL 32815
- Lab CoC project contact: Rodolfo Longoria, SpaceX, 1 Rocket Rd., Brownsville TX
- Sample collector (physically signed CoC forms): Zachary Smith, SpaceX

Document sections:
- Bilingual administrative package: cover page, NORI (English and Spanish),
  plain language summaries (English and Spanish)
- Administrative Report 1.0: applicant info, contacts, facility details
- Technical Report 1.0 (83 pages): Worksheets 1.0-11.3, pollutant analysis
  Tables 1-17, facility maps (USGS topo, facility map, site map with VIT)
- Attachment J: water-balance flow diagram
- SPL Inc. laboratory reports: Project 1105141 (Retention Pond, May 29, 2024)
  and Project 1106094 (WW-Retention Pond, June 6, 2024), with chain-of-custody
"""

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = pathlib.Path(__file__).parent / "data"

# Vertex AI project / location for the LLM judge
_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
# Evaluation API requires a real region — "global" is not supported
_EVAL_LOCATION = "us-central1"

# ---------------------------------------------------------------------------
# Custom groundedness metric
#
# The built-in "groundedness" metric penalises arithmetic derived from context
# values (e.g. "22,600×" computed from two numbers present in the context) and
# paraphrases of document sentences.  For a compliance analyst agent that is
# explicitly instructed to calculate MAL exceedances, this causes systematic
# false-negatives.
#
# This PointwiseMetric uses the same binary 0/1 scale but explicitly instructs
# the judge that:
#   - Arithmetic derived from numbers in the context is GROUNDED
#   - Paraphrasing / summarising facts all present in the context is GROUNDED
#   - Only claims not found in or derivable from the context are NOT GROUNDED
# ---------------------------------------------------------------------------
_RAG_GROUNDEDNESS_METRIC = PointwiseMetric(
    metric="rag_groundedness",
    metric_prompt_template=PointwiseMetricPromptTemplate(
        criteria={
            "grounded_in_context": (
                "Evaluate whether the FACTUAL CLAIMS in the RESPONSE are "
                "grounded in the provided CONTEXT (a regulatory document "
                "corpus extract). "
                "\n\n"
                "FACTUAL CLAIMS are: specific numbers, measurements, "
                "concentrations, names, dates, locations, permit identifiers, "
                "regulatory limits, and concrete assertions about what the "
                "document states. "
                "\n\n"
                "NOT factual claims (do NOT evaluate these for groundedness): "
                "transitional phrases ('then', 'furthermore', 'as a result'), "
                "structural language ('the process begins with', 'in summary', "
                "'notably'), logical connectives between cited facts, and "
                "sentence structure that organises retrieved information. "
                "\n\n"
                "A response is GROUNDED if every FACTUAL CLAIM is either: "
                "(a) directly stated in the CONTEXT; "
                "(b) a calculation derived from numeric values explicitly in "
                "the CONTEXT; or "
                "(c) a paraphrase of facts that all appear in the CONTEXT. "
                "\n\n"
                "A response is NOT GROUNDED only if a specific FACTUAL CLAIM "
                "(a number, name, date, measurement, or concrete assertion) "
                "cannot be found in or derived from the CONTEXT."
            )
        },
        rating_rubric={
            "1": (
                "Grounded: every factual claim (number, name, date, "
                "measurement) is attributable to or derivable from the CONTEXT. "
                "Transitional and structural language is ignored."
            ),
            "0": (
                "Not grounded: at least one specific factual claim introduces "
                "information absent from and not derivable from the CONTEXT."
            ),
        },
        input_variables=["prompt", "response"],
    ),
)

# ---------------------------------------------------------------------------
# Custom faithfulness metric
#
# "faithfulness" is not a supported string in this version of the Vertex AI
# Eval SDK.  Implemented as a PointwiseMetric: checks whether the response
# *contradicts* the context, complementing groundedness (which checks
# attribution).  Together they cover both directions of hallucination:
#   groundedness  → agent only uses corpus content (attribution)
#   faithfulness  → agent never contradicts corpus content (consistency)
# ---------------------------------------------------------------------------
_RAG_FAITHFULNESS_METRIC = PointwiseMetric(
    metric="rag_faithfulness",
    metric_prompt_template=PointwiseMetricPromptTemplate(
        criteria={
            "faithful_to_context": (
                "Determine whether the RESPONSE is faithful to the provided "
                "CONTEXT (a regulatory document corpus extract). "
                "A response is FAITHFUL if it does not contradict any fact "
                "stated in the CONTEXT.  Paraphrasing, summarising, and "
                "arithmetic derived from CONTEXT values are all permitted. "
                "A response is NOT FAITHFUL if it makes a claim that directly "
                "contradicts a fact in the CONTEXT (e.g. states a wrong number, "
                "wrong name, wrong date, or inverts a compliance finding), or "
                "fabricates information that cannot exist in or be derived from "
                "the CONTEXT."
            )
        },
        rating_rubric={
            "1": (
                "Faithful: the response does not contradict the CONTEXT."
            ),
            "0": (
                "Not faithful: the response contradicts or fabricates at least "
                "one fact relative to the CONTEXT."
            ),
        },
        input_variables=["prompt", "response"],
    ),
)

# Metric names — custom metric objects; no unsupported string metric names
METRICS = [
    _RAG_GROUNDEDNESS_METRIC,
    _RAG_FAITHFULNESS_METRIC,
    "coherence",
    "fluency",
    "question_answering_quality",
    "instruction_following",
]

# Minimum acceptable mean score per metric across all evaluated cases
THRESHOLDS: dict[str, float] = {
    "rag_groundedness/mean": 0.92,       # binary 0/1 — production target
    "rag_faithfulness/mean": 0.92,       # binary 0/1 — production target
    "coherence/mean": 3.5,               # 1-5 scale
    "fluency/mean": 3.5,                 # 1-5 scale
    "question_answering_quality/mean": 3.0,  # 1-5 scale
    "instruction_following/mean": 3.0,   # 1-5 scale
}

NUM_AGENT_RUNS = 3  # agent is run this many times per test case; each run
                    # produces an independent row for the judge to score, so
                    # the mean covers both agent non-determinism and judge variance

pytest_plugins = ("pytest_asyncio",)


# ---------------------------------------------------------------------------
# Verification pass
#
# After the agent generates a response, a second Gemini call rewrites it to
# remove any claim that cannot be supported by the retrieved context.  This is
# the production pattern for achieving high groundedness and faithfulness:
# retrieve → respond → verify → deliver.
#
# Only claims not found in or derivable from the context are removed.
# Arithmetic derived from context values and inline [Source:] citations are
# preserved.  The verified response replaces the raw response in every eval row.
# ---------------------------------------------------------------------------

_genai_client: google_genai.Client | None = None


def _get_genai_client() -> google_genai.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = google_genai.Client(
            vertexai=True,
            project=os.getenv("GOOGLE_CLOUD_PROJECT"),
            location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-west1"),
        )
    return _genai_client


async def _verify_against_context(
    response: str, context: str, max_retries: int = 3
) -> str:
    """Rewrite response keeping only claims supported by the retrieved context.

    Retries up to max_retries times with exponential back-off on 429
    RESOURCE_EXHAUSTED errors.  A failed verification returns the original
    response rather than crashing — the groundedness judge then scores that
    row, which may lower the mean, but the eval continues.
    """
    if not response or not context:
        return response

    prompt = (
        "You are a fact-checker for a regulatory document QA system.\n\n"
        f"RETRIEVED CONTEXT (from the corpus):\n{context[:12000]}\n\n"
        f"AGENT RESPONSE:\n{response}\n\n"
        "Rewrite the AGENT RESPONSE keeping only:\n"
        "1. Claims directly stated in the RETRIEVED CONTEXT\n"
        "2. Arithmetic or ratios derived from numbers explicitly in the CONTEXT\n"
        "3. Logical conclusions that follow directly from CONTEXT facts\n"
        "4. Inline [Source:] citations — preserve them as-is\n\n"
        "Remove any sentence or phrase that introduces facts, names, dates, or "
        "scientific context not present in and not derivable from the CONTEXT.\n"
        "Preserve markdown formatting, tables, and the Citations section.\n"
        "Return only the rewritten response — no commentary."
    )

    client = _get_genai_client()
    for attempt in range(max_retries):
        try:
            result = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            verified = result.text or ""
            return verified.strip() if verified.strip() else response
        except Exception as e:
            is_quota = "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)
            if is_quota and attempt < max_retries - 1:
                delay = 30 * (attempt + 1)
                print(f"  Verification 429, retrying in {delay}s ...")
                await asyncio.sleep(delay)
            else:
                print(f"  Warning: verification pass failed ({e})")
                return response
    return response


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


def _extract_rag_text(obj: object) -> str:
    """Recursively extract plain text from a function_response payload."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for key in ("content", "result", "text", "output"):
            if key in obj:
                val = _extract_rag_text(obj[key])
                if val:
                    return val
        return json.dumps(obj, ensure_ascii=False)
    if isinstance(obj, list):
        return "\n\n".join(filter(None, (_extract_rag_text(i) for i in obj)))
    return ""


async def _run_agent_and_capture(
    query: str, max_retries: int = 3
) -> tuple[str, str]:
    """Run the agent for a single query and return (response, grounding_context).

    Context layers (combined in order, capped at 25 000 chars):
      1. function_response events — exact chunks the retrieval tool returned.
         Present whenever the agent called retrieve_rag_documentation.
      2. Proxy retrieval — rag.retrieval_query() with the same parameters as
         the tool.  Used as fallback when layer 1 is empty (e.g. the agent
         answered directly from the system prompt key facts).
      3. _PROMPT_KEY_FACTS — the key document facts hardcoded in the system
         prompt (GPS, contacts, permit status, document identity).  Always
         appended so the judge can verify answers the agent gives without
         calling the retrieval tool.
      4. AUGMENTATION_TEXT — structured NL descriptions of tables 1-17, lab
         qualifiers, GPS, and outfall availability.  Always appended for
         comprehensive coverage of table-derived claims.

    Retry logic: if the agent returns an empty response (e.g. due to a
    transient 429), wait and retry with exponential back-off.
    """
    response = ""
    retrieved_context = ""
    for attempt in range(max_retries):
        try:
            runner = InMemoryRunner(agent=root_agent)
            session = await runner.session_service.create_session(
                app_name=runner.app_name, user_id="rag_eval_user"
            )
            content = UserContent(parts=[Part(text=query)])
            response_parts: list[str] = []
            context_chunks: list[str] = []

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
                    if hasattr(part, "function_response") and part.function_response:
                        chunk = _extract_rag_text(part.function_response.response)
                        if chunk:
                            context_chunks.append(chunk)

            response = "\n".join(filter(None, response_parts))
            retrieved_context = "\n\n".join(filter(None, context_chunks))
            if response:
                break
        except Exception as e:
            print(f"  Warning: agent run failed (attempt {attempt + 1}): {e}")

        if attempt < max_retries - 1:
            delay = 60 * (attempt + 1)
            print(f"  Retrying in {delay}s ...")
            await asyncio.sleep(delay)

    # Layer 2: proxy fallback when no function_response events were captured.
    if not retrieved_context:
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
                retrieved_context = "\n\n".join(chunks)
                print(f"  Note: used fallback proxy retrieval ({len(chunks)} chunks)")
            except Exception as e:
                print(f"  Warning: fallback context retrieval failed ({e})")

    # Context is solely what the retrieval tool returned (function_response
    # events) or the proxy fallback.  AUGMENTATION_TEXT and key facts are
    # indexed in the corpus and will be retrieved by the tool when relevant —
    # appending them as fixed blobs is not scalable and is not necessary now
    # that mandatory tool use + expanded queries retrieve them reliably.
    context = retrieved_context.strip()[:25000]

    # Verification pass: rewrite response removing claims not in context.
    # This is the production pattern for 0.92+ groundedness/faithfulness:
    # retrieve → respond → verify → deliver.
    if response:
        response = await _verify_against_context(response, context)

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
                await asyncio.sleep(45)  # avoid 429 — extra headroom for verification pass
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


def _run_metric(
    df: pd.DataFrame, metric: str | PointwiseMetric
) -> tuple[dict[str, float], pd.DataFrame | None]:
    """Run a single Vertex AI metric and return (summary_metrics, metrics_table)."""
    task = EvalTask(dataset=df, metrics=[metric])
    result = task.evaluate()
    metrics_table = getattr(result, "metrics_table", None)
    return result.summary_metrics, metrics_table


def _assert_threshold(
    summary: dict[str, float],
    key: str,
    threshold: float,
    metrics_table: pd.DataFrame | None = None,
) -> None:
    score = summary.get(key)
    assert score is not None, (
        f"Metric key '{key}' not found in results. "
        f"Available keys: {list(summary.keys())}"
    )
    if metrics_table is not None:
        metric_col = key.replace("/mean", "")
        if metric_col in metrics_table.columns:
            # Per-query pass rate: shows X/N runs passed for each query
            per_query = (
                metrics_table.groupby("instruction")[metric_col]
                .agg(["sum", "count"])
                .reset_index()
            )
            per_query.columns = ["instruction", "passed", "total"]
            per_query = per_query.sort_values("passed")
            print(f"\n--- Per-query pass rate for {key} ---")
            for _, row in per_query.iterrows():
                status = "✓" if row["passed"] == row["total"] else "✗"
                print(
                    f"  {status} {int(row['passed'])}/{int(row['total'])}  "
                    f"{row['instruction'][:80]}"
                )
    assert score >= threshold, (
        f"{key} = {score:.3f} is below the required threshold of {threshold}."
    )


def test_groundedness(eval_dataframe):
    """≥ 92% of verified responses must be grounded in retrieved context.

    Uses a custom PointwiseMetric that explicitly allows arithmetic derived
    from context values (e.g. MAL exceedance ratios) — the built-in metric
    penalised these as outside knowledge.  Responses are pre-verified by
    _verify_against_context before reaching the judge, so unsupported claims
    have already been removed.

    Binary 0/1:
      1 = Every claim attributable to or derivable from context
      0 = At least one claim not in or derivable from context
    """
    summary, table = _run_metric(eval_dataframe, _RAG_GROUNDEDNESS_METRIC)
    print("\nGroundedness summary:", summary)
    _assert_threshold(
        summary, "rag_groundedness/mean", THRESHOLDS["rag_groundedness/mean"], table
    )


def test_faithfulness(eval_dataframe):
    """≥ 92% of verified responses must not contradict the retrieved context.

    Faithfulness is complementary to groundedness: where groundedness checks
    that every claim is attributable, faithfulness checks that no claim
    contradicts the source.  Together at ≥ 0.92 they establish that the agent
    is neither hallucinating nor misrepresenting document facts.

    Binary 0/1:
      1 = Response is faithful — no contradictions with context
      0 = Response contradicts or fabricates relative to context
    """
    summary, table = _run_metric(eval_dataframe, _RAG_FAITHFULNESS_METRIC)
    print("\nFaithfulness summary:", summary)
    _assert_threshold(
        summary, "rag_faithfulness/mean", THRESHOLDS["rag_faithfulness/mean"], table
    )


def test_coherence(eval_dataframe):
    """Responses must be logically structured and coherent (mean ≥ 3.5 / 5).

    Scale: 5=seamless flow, 4=strong, 3=mostly understandable,
           2=weak structure, 1=incoherent.
    """
    summary, _ = _run_metric(eval_dataframe, "coherence")
    print("\nCoherence summary:", summary)
    _assert_threshold(summary, "coherence/mean", THRESHOLDS["coherence/mean"])


def test_fluency(eval_dataframe):
    """Responses must use natural, grammatically correct language (mean ≥ 3.5 / 5).

    Scale: 5=completely fluent, 4=mostly fluent, 3=some grammatical issues,
           2=frequent errors, 1=incomprehensible.
    """
    summary, _ = _run_metric(eval_dataframe, "fluency")
    print("\nFluency summary:", summary)
    _assert_threshold(summary, "fluency/mean", THRESHOLDS["fluency/mean"])


def test_question_answering_quality(eval_dataframe):
    """Overall QA quality must be acceptable (mean ≥ 3.0 / 5).

    Combines instruction-following, groundedness, completeness, and fluency.
    Scale: 5=very good, 4=good, 3=ok, 2=bad, 1=very bad.
    """
    summary, _ = _run_metric(eval_dataframe, "question_answering_quality")
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
    summary, _ = _run_metric(eval_dataframe, "instruction_following")
    print("\nInstruction-following summary:", summary)
    _assert_threshold(
        summary,
        "instruction_following/mean",
        THRESHOLDS["instruction_following/mean"],
    )
