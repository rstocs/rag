import json
import logging
from typing import List, Dict
from src.core.client import client
from src.core.embeddings import embed_text, embed_text_for_image_search
from src.core.reranker import rerank
from src.retrieval.indexer import HybridStore, Chunk
from src.config import GENERATOR_MODEL_ID, TOP_K_RETRIEVAL, RRF_K
from src.generation import prompts

logger = logging.getLogger(__name__)

# First-pass multiplier: retrieve this many times top_k before re-ranking.
# A wider first-pass gives the cross-encoder more candidates to promote.
_FIRST_PASS_MULTIPLIER = 10


class Retriever:
    def __init__(self, store: HybridStore):
        self.store = store

    def expand_query(self, query: str) -> str:
        """Expands the query with synonyms and Spanish translation."""
        try:
            prompt = prompts.PROMPT_QUERY_EXPANSION.format(query=query)
            from google import genai
            response = client.models.generate_content(
                model=GENERATOR_MODEL_ID,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0
                )
            )
            text = response.text.strip()
            data = json.loads(text)
            return data.get("expanded_query", query)
        except Exception as e:
            logger.warning(f"Query expansion failed, using original query. Error: {e}")
            return query

    def generate_hypothetical_document(self, query: str) -> str:
        """HyDE: Generate a short hypothetical answer passage and embed it.

        By embedding a plausible answer instead of the question, we search in
        the 'answer space' of the index, dramatically improving recall for
        rare definitional or procedural queries (e.g., 'What does J flag mean?').
        """
        try:
            hyde_prompt = (
                "You are an expert in environmental regulatory permit applications. "
                "Write a concise 2-3 sentence passage from a regulatory document that would "
                "directly answer the following question. Be specific, technical, and use "
                "domain vocabulary as it would appear in an official TPDES permit application.\n\n"
                f"Question: {query}"
            )
            from google import genai
            response = client.models.generate_content(
                model=GENERATOR_MODEL_ID,
                contents=hyde_prompt,
                config=genai.types.GenerateContentConfig(
                    temperature=0.0
                )
            )
            hypothetical = response.text.strip()
            logger.info(f"HyDE document: {hypothetical[:120]}...")
            return hypothetical
        except Exception as e:
            logger.warning(f"HyDE generation failed, falling back to original query: {e}")
            return query

    def retrieve(self, query: str, top_k: int = TOP_K_RETRIEVAL) -> List[Chunk]:
        logger.info(f"Original Query: {query}")

        expanded_query = self.expand_query(query)
        logger.info(f"Expanded Query: {expanded_query}")

        # HyDE: embed a hypothetical answer document for better semantic matching
        hypothetical_doc = self.generate_hypothetical_document(query)

        first_pass_k = top_k * _FIRST_PASS_MULTIPLIER

        # 1. Text vector search — embed hypothetical document (HyDE) for precision
        hyde_emb = embed_text(hypothetical_doc)
        vector_results = self.store.search_vector(hyde_emb, top_k=first_pass_k)

        # 1b. Also run with expanded query to broaden recall; merge results
        expanded_emb = embed_text(expanded_query)
        expanded_vector_results = self.store.search_vector(expanded_emb, top_k=first_pass_k)

        # 2. Image vector search (multimodal text query → 1408-dim → image FAISS index)
        image_query_emb = embed_text_for_image_search(expanded_query)
        image_results = self.store.search_image_vector(image_query_emb, top_k=first_pass_k)

        # 3. Keyword search (BM25)
        bm25_results = self.store.search_bm25(expanded_query, top_k=first_pass_k)

        # 4. Reciprocal Rank Fusion across all result sets
        rrf_scores: Dict[str, float] = {}
        chunk_map: Dict[str, Chunk] = {}

        for rank, (chunk, _) in enumerate(vector_results):
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + 1.0 / (rank + 1 + RRF_K)
            chunk_map[chunk.id] = chunk

        for rank, (chunk, _) in enumerate(expanded_vector_results):
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + 1.0 / (rank + 1 + RRF_K)
            chunk_map[chunk.id] = chunk

        for rank, (chunk, _) in enumerate(image_results):
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + 1.0 / (rank + 1 + RRF_K)
            chunk_map[chunk.id] = chunk

        for rank, (chunk, _) in enumerate(bm25_results):
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + 1.0 / (rank + 1 + RRF_K)
            chunk_map[chunk.id] = chunk

        # 5. Take the top first_pass_k candidates from RRF fusion
        sorted_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)
        candidates = [chunk_map[cid] for cid in sorted_ids[:first_pass_k]]
        logger.info(f"First-pass RRF: {len(candidates)} candidates")

        # 6. Cross-encoder re-ranking — uses original (unexpanded) query for precision
        reranked = rerank(query, candidates, top_k=top_k)
        return reranked
