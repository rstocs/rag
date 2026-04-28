import json
import logging
from typing import List, Dict
from src.core.client import client
from src.core.embeddings import embed_text
from src.retrieval.indexer import HybridStore, Chunk
from src.config import MODEL_ID, TOP_K_RETRIEVAL, RRF_K
from src.generation import prompts

logger = logging.getLogger(__name__)

class Retriever:
    def __init__(self, store: HybridStore):
        self.store = store

    def expand_query(self, query: str) -> str:
        """Expands the query with synonyms and Spanish translation."""
        try:
            prompt = prompts.PROMPT_QUERY_EXPANSION.format(query=query)
            from google import genai
            response = client.models.generate_content(
                model=MODEL_ID,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            text = response.text.strip()
            data = json.loads(text)
            return data.get("expanded_query", query)
        except Exception as e:
            logger.warning(f"Query expansion failed, using original query. Error: {e}")
            return query

    def retrieve(self, query: str, top_k: int = TOP_K_RETRIEVAL) -> List[Chunk]:
        logger.info(f"Original Query: {query}")
        
        expanded_query = self.expand_query(query)
        logger.info(f"Expanded Query: {expanded_query}")
        
        # 1. Vector Search
        query_emb = embed_text(expanded_query)
        vector_results = self.store.search_vector(query_emb, top_k=top_k * 2)
        
        # 2. Keyword Search (BM25)
        bm25_results = self.store.search_bm25(expanded_query, top_k=top_k * 2)
        
        # 3. Reciprocal Rank Fusion (RRF)
        # Combine results using RRF: score = 1 / (rank + k)
        rrf_scores: Dict[str, float] = {}
        chunk_map: Dict[str, Chunk] = {}
        
        for rank, (chunk, _) in enumerate(vector_results):
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + 1.0 / (rank + 1 + RRF_K)
            chunk_map[chunk.id] = chunk
            
        for rank, (chunk, _) in enumerate(bm25_results):
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + 1.0 / (rank + 1 + RRF_K)
            chunk_map[chunk.id] = chunk
            
        # Sort by RRF score
        sorted_chunks = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        
        # Return top_k
        top_chunks = [chunk_map[chunk_id] for chunk_id, score in sorted_chunks[:top_k]]
        return top_chunks
