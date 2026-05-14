import logging
from typing import List, Tuple
from sentence_transformers import CrossEncoder

from src.retrieval.indexer import Chunk

logger = logging.getLogger(__name__)

# MS-MARCO MiniLM cross-encoder — industry standard for passage re-ranking.
# Downloads ~80MB on first use; cached locally afterwards.
_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_reranker: CrossEncoder = None


def _get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        logger.info(f"Loading cross-encoder re-ranker: {_MODEL_NAME}")
        _reranker = CrossEncoder(_MODEL_NAME, max_length=512)
    return _reranker


def rerank(query: str, chunks: List[Chunk], top_k: int) -> List[Chunk]:
    """Re-rank candidate chunks using a cross-encoder.

    The cross-encoder scores each (query, passage) pair jointly — far more
    accurate than bi-encoder cosine similarity for rare/specific queries.

    Args:
        query:   The original user query.
        chunks:  Candidate chunks from the first-pass retriever (bi-encoder + BM25).
        top_k:   Number of top chunks to return after re-ranking.

    Returns:
        Re-ranked list of up to top_k chunks, best first.
    """
    if not chunks:
        return []

    reranker = _get_reranker()

    # Build (query, passage) pairs for the cross-encoder
    pairs = [(query, chunk.text) for chunk in chunks]

    # Score all pairs — returns a flat list of floats
    scores: List[float] = reranker.predict(pairs).tolist()

    # Sort by score descending
    ranked: List[Tuple[float, Chunk]] = sorted(
        zip(scores, chunks), key=lambda x: x[0], reverse=True
    )

    top = [chunk for _, chunk in ranked[:top_k]]
    logger.info(
        f"Re-ranker: {len(chunks)} candidates → top {len(top)} "
        f"(best score: {ranked[0][0]:.3f}, worst kept: {ranked[len(top)-1][0]:.3f})"
    )
    return top
