import json
import os
import faiss
import numpy as np
import pickle
import logging
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from rank_bm25 import BM25Okapi

from src.config import VECTOR_STORE_PATH, EMBEDDING_DIMENSION, IMAGE_EMBEDDING_DIMENSION

logger = logging.getLogger(__name__)

class Chunk(BaseModel):
    id: str
    text: str
    metadata: Dict[str, Any] = {}
    embedding: Optional[List[float]] = None

class HybridStore:
    def __init__(self, index_path: str = VECTOR_STORE_PATH):
        self.index_path = index_path
        os.makedirs(index_path, exist_ok=True)

        # Index 1: text-embedding-004 (768-dim) — all text chunks
        self.faiss_index = None
        self.text_chunk_ids: List[str] = []   # ordered list mapping FAISS row → chunk id

        # Index 2: multimodalembedding@001 image vectors (1408-dim) — VISUAL_DIAGRAM chunks only
        self.image_faiss_index = None
        self.image_chunk_ids: List[str] = []  # ordered list mapping image FAISS row → chunk id

        # Shared chunk store (text lives here for both indexes)
        self.chunks: Dict[str, Chunk] = {}

        # BM25 lexical index
        self.bm25: Optional[BM25Okapi] = None
        self.tokenized_corpus: List[List[str]] = []

        self.load()

    def _tokenize(self, text: str) -> List[str]:
        return text.lower().split()

    def add_chunks(self, chunks: List[Chunk]):
        """Add text-embedded chunks to the text FAISS index and BM25."""
        if not chunks:
            return

        embeddings = []
        for chunk in chunks:
            if chunk.embedding is not None:
                embeddings.append(chunk.embedding)
                self.chunks[chunk.id] = chunk
                self.text_chunk_ids.append(chunk.id)
                self.tokenized_corpus.append(self._tokenize(chunk.text))

        if not embeddings:
            return

        emb_array = np.array(embeddings).astype('float32')
        if self.faiss_index is None:
            self.faiss_index = faiss.IndexFlatIP(EMBEDDING_DIMENSION)

        self.faiss_index.add(emb_array)
        self.bm25 = BM25Okapi(self.tokenized_corpus)
        self.save()

    def add_image_chunk(self, chunk: Chunk):
        """Add an image-embedded chunk to the separate image FAISS index."""
        if chunk.embedding is None:
            return

        self.chunks[chunk.id] = chunk
        self.image_chunk_ids.append(chunk.id)

        emb_array = np.array([chunk.embedding]).astype('float32')
        if self.image_faiss_index is None:
            self.image_faiss_index = faiss.IndexFlatIP(IMAGE_EMBEDDING_DIMENSION)

        self.image_faiss_index.add(emb_array)

        # Also add to BM25 so text content is still keyword-searchable
        self.tokenized_corpus.append(self._tokenize(chunk.text))
        self.bm25 = BM25Okapi(self.tokenized_corpus)
        self.save()

    def search_vector(self, query_embedding: List[float], top_k: int = 5) -> List[tuple]:
        """Search the text FAISS index."""
        if self.faiss_index is None or not self.text_chunk_ids:
            return []

        q_emb = np.array([query_embedding]).astype('float32')
        distances, indices = self.faiss_index.search(q_emb, top_k)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if 0 <= idx < len(self.text_chunk_ids):
                chunk_id = self.text_chunk_ids[idx]
                if chunk_id in self.chunks:
                    results.append((self.chunks[chunk_id], float(dist)))
        return results

    def search_image_vector(self, query_image_embedding: List[float], top_k: int = 5) -> List[tuple]:
        """Search the image FAISS index using a multimodal text query embedding."""
        if self.image_faiss_index is None or not self.image_chunk_ids:
            return []

        q_emb = np.array([query_image_embedding]).astype('float32')
        distances, indices = self.image_faiss_index.search(q_emb, top_k)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if 0 <= idx < len(self.image_chunk_ids):
                chunk_id = self.image_chunk_ids[idx]
                if chunk_id in self.chunks:
                    results.append((self.chunks[chunk_id], float(dist)))
        return results

    def search_bm25(self, query: str, top_k: int = 5) -> List[tuple]:
        """Keyword search across all chunks."""
        if self.bm25 is None or not self.chunks:
            return []

        tokenized_query = self._tokenize(query)
        scores = self.bm25.get_scores(tokenized_query)
        top_indices = np.argsort(scores)[::-1][:top_k]

        all_chunk_ids = self.text_chunk_ids + self.image_chunk_ids
        results = []
        for idx in top_indices:
            if scores[idx] > 0 and idx < len(all_chunk_ids):
                chunk_id = all_chunk_ids[idx]
                if chunk_id in self.chunks:
                    results.append((self.chunks[chunk_id], float(scores[idx])))
        return results

    def save(self):
        if self.faiss_index is not None:
            faiss.write_index(self.faiss_index, os.path.join(self.index_path, "index.faiss"))
        if self.image_faiss_index is not None:
            faiss.write_index(self.image_faiss_index, os.path.join(self.index_path, "image_index.faiss"))

        with open(os.path.join(self.index_path, "chunks.json"), "w") as f:
            json.dump({k: v.model_dump() for k, v in self.chunks.items()}, f)
        with open(os.path.join(self.index_path, "chunk_ids.pkl"), "wb") as f:
            pickle.dump({"text": self.text_chunk_ids, "image": self.image_chunk_ids}, f)

        if self.bm25 is not None:
            with open(os.path.join(self.index_path, "bm25.pkl"), "wb") as f:
                pickle.dump(self.bm25, f)
            with open(os.path.join(self.index_path, "corpus.pkl"), "wb") as f:
                pickle.dump(self.tokenized_corpus, f)

    def load(self):
        try:
            text_index_file = os.path.join(self.index_path, "index.faiss")
            image_index_file = os.path.join(self.index_path, "image_index.faiss")
            chunks_file = os.path.join(self.index_path, "chunks.json")
            ids_file = os.path.join(self.index_path, "chunk_ids.pkl")
            bm25_file = os.path.join(self.index_path, "bm25.pkl")
            corpus_file = os.path.join(self.index_path, "corpus.pkl")

            if os.path.exists(text_index_file):
                self.faiss_index = faiss.read_index(text_index_file)
            else:
                logger.warning(f"Text FAISS index not found at {text_index_file}")

            if os.path.exists(image_index_file):
                self.image_faiss_index = faiss.read_index(image_index_file)
            else:
                logger.warning(f"Image FAISS index not found at {image_index_file} (expected if no visual pages yet)")

            if os.path.exists(chunks_file):
                with open(chunks_file, "r") as f:
                    data = json.load(f)
                    self.chunks = {k: Chunk(**v) for k, v in data.items()}
            else:
                logger.warning(f"Chunks file not found at {chunks_file}")

            if os.path.exists(ids_file):
                with open(ids_file, "rb") as f:
                    ids = pickle.load(f)
                    self.text_chunk_ids = ids.get("text", [])
                    self.image_chunk_ids = ids.get("image", [])

            if os.path.exists(bm25_file) and os.path.exists(corpus_file):
                with open(bm25_file, "rb") as f:
                    self.bm25 = pickle.load(f)
                with open(corpus_file, "rb") as f:
                    self.tokenized_corpus = pickle.load(f)
            else:
                logger.warning("BM25 index or corpus not found")

        except Exception as e:
            logger.error(f"Error loading indexes: {e}")
