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

import asyncio
import os
import uuid

import google.auth
from dotenv import load_dotenv
from google.adk.agents import Agent
from openinference.instrumentation import using_session
from rank_bm25 import BM25Okapi
from vertexai.preview import rag

from rag.tracing import instrument_adk_with_arize

from .prompts import return_instructions_root

load_dotenv(override=True)

_, project_id = google.auth.default()
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-west1")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

# observability: hooks up Arize Phoenix tracing
_ = instrument_adk_with_arize()


# ---------------------------------------------------------------------------
# Retrieval helpers
# ---------------------------------------------------------------------------

def _bm25_scores(query: str, chunks: list[str]) -> list[float]:
    """BM25 keyword relevance scores for each chunk."""
    tokenized = [c.lower().split() for c in chunks]
    bm25 = BM25Okapi(tokenized)
    return bm25.get_scores(query.lower().split()).tolist()


def _vertex_rerank(
    query: str,
    chunks: list[str],
    project: str,
    top_n: int = 10,
) -> list[str]:
    """Rerank chunks with Vertex AI Discovery Engine Ranking API.

    Falls back to the input order if the API is unavailable or returns an
    error, so the retrieval pipeline degrades gracefully.
    """
    try:
        from google.cloud.discoveryengine_v1 import (
            RankRequest,
            RankServiceClient,
            RankingRecord,
        )

        client = RankServiceClient()
        ranking_config = (
            f"projects/{project}/locations/global"
            "/rankingConfigs/default_ranking_config"
        )
        records = [
            RankingRecord(id=str(i), content=chunk[:512])
            for i, chunk in enumerate(chunks)
        ]
        request = RankRequest(
            ranking_config=ranking_config,
            model="semantic-ranker-512@latest",
            query=query[:512],
            records=records,
        )
        response = client.rank(request=request)
        id_to_chunk = {str(i): chunk for i, chunk in enumerate(chunks)}
        reranked = [
            id_to_chunk[r.id]
            for r in response.records[:top_n]
            if r.id in id_to_chunk
        ]
        return reranked if reranked else chunks[:top_n]
    except Exception as e:
        print(f"  Reranking skipped ({type(e).__name__}: {e})")
        return chunks[:top_n]


# ---------------------------------------------------------------------------
# Retrieval tool — vector search → BM25 hybrid re-score → semantic rerank
# ---------------------------------------------------------------------------

async def retrieve_rag_documentation(query: str) -> str:
    """Retrieve information from the SpaceX TPDES permit application and
    testing report corpus.

    For pollutant concentration questions, query specifically for
    'Worksheet 2.0 Table 1 Table 2 Outfall 001 grab sample concentration'
    to retrieve the primary discharge measurement data.
    For facility layout questions, query for 'site map facility map outfall
    sampling point Vertical Integration Tower'.
    For personnel questions, query for 'chain of custody SpaceX employee
    sample submitter contact'.
    For process questions, query for 'water balance flow diagram deluge
    retention basin outfall'.

    Retrieval pipeline:
      1. Vector search (top-50, threshold 0.5) — wide net for recall
      2. BM25 hybrid re-score — keyword relevance layered on vector results
      3. Vertex AI semantic reranking — final top-10 by cross-encoder score
    """
    corpus = os.environ.get("RAG_CORPUS", "")
    if not corpus:
        return "RAG corpus not configured."

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")

    # Step 1: Vector retrieval — enough candidates for BM25 + reranker pipeline
    rag_resp = rag.retrieval_query(
        text=query,
        rag_resources=[rag.RagResource(rag_corpus=corpus)],
        rag_retrieval_config=rag.RagRetrievalConfig(
            top_k=30,
            filter=rag.Filter(vector_distance_threshold=0.4),
        ),
    )
    chunks = [ctx.text for ctx in rag_resp.contexts.contexts if ctx.text]

    if not chunks:
        return "No relevant information found in the corpus for this query."

    # Step 2: BM25 hybrid re-score using Reciprocal Rank Fusion.
    # Combines vector retrieval rank (position) with BM25 keyword score so
    # that chunks matching both semantically and by exact keyword float up.
    bm25 = _bm25_scores(query, chunks)
    max_bm25 = max(bm25) if max(bm25) > 0 else 1.0
    norm_bm25 = [s / max_bm25 for s in bm25]

    rrf_k = 60  # standard RRF smoothing constant
    combined = [
        (1.0 / (rrf_k + rank)) + bm25_score
        for rank, bm25_score in enumerate(norm_bm25)
    ]
    candidates = [
        chunk
        for _, chunk in sorted(
            zip(combined, chunks), key=lambda x: x[0], reverse=True
        )
    ][:25]

    # Step 3: Vertex AI semantic reranking — ~300 ms, returns top-10
    reranked = await asyncio.to_thread(
        _vertex_rerank, query, candidates, project, 10
    )

    return "\n\n---\n\n".join(reranked)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

tools: list = []
if os.environ.get("RAG_CORPUS"):
    tools.append(retrieve_rag_documentation)

with using_session(session_id=uuid.uuid4()):
    root_agent = Agent(
        model="gemini-2.5-flash",
        name="ask_rag_agent",
        instruction=return_instructions_root(),
        tools=tools,
    )
