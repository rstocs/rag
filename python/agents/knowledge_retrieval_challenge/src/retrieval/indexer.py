import json
import os
import faiss
import numpy as np
import pickle
import logging
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from rank_bm25 import BM25Okapi

from src.config import VECTOR_STORE_PATH

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
        self.faiss_index = None
        self.chunks: Dict[str, Chunk] = {}
        self.dimension = 768 # text-embedding-004 dimension
        
        self.bm25: Optional[BM25Okapi] = None
        self.tokenized_corpus: List[List[str]] = []
        
        self.load()

    def _tokenize(self, text: str) -> List[str]:
        # Simple whitespace tokenization, lowercase
        return text.lower().split()

    def add_chunks(self, chunks: List[Chunk]):
        if not chunks:
            return
            
        embeddings = []
        for chunk in chunks:
            if chunk.embedding is not None:
                embeddings.append(chunk.embedding)
                self.chunks[chunk.id] = chunk
                self.tokenized_corpus.append(self._tokenize(chunk.text))
                
        if not embeddings:
            return
            
        # Add to FAISS
        emb_array = np.array(embeddings).astype('float32')
        if self.faiss_index is None:
            self.dimension = emb_array.shape[1]
            self.faiss_index = faiss.IndexFlatIP(self.dimension)
            
        self.faiss_index.add(emb_array)
        
        # Add to BM25
        self.bm25 = BM25Okapi(self.tokenized_corpus)
        
        self.save()

    def search_vector(self, query_embedding: List[float], top_k: int = 5) -> List[tuple[Chunk, float]]:
        if self.faiss_index is None or not self.chunks:
            return []
            
        q_emb = np.array([query_embedding]).astype('float32')
        distances, indices = self.faiss_index.search(q_emb, top_k)
        
        results = []
        chunk_list = list(self.chunks.values())
        for dist, idx in zip(distances[0], indices[0]):
            if idx >= 0 and idx < len(chunk_list):
                results.append((chunk_list[idx], float(dist)))
        return results

    def search_bm25(self, query: str, top_k: int = 5) -> List[tuple[Chunk, float]]:
        if self.bm25 is None or not self.chunks:
            return []
            
        tokenized_query = self._tokenize(query)
        scores = self.bm25.get_scores(tokenized_query)
        
        # Get top k indices
        top_indices = np.argsort(scores)[::-1][:top_k]
        
        results = []
        chunk_list = list(self.chunks.values())
        for idx in top_indices:
            if scores[idx] > 0:
                results.append((chunk_list[idx], float(scores[idx])))
        return results

    def save(self):
        if self.faiss_index is not None:
            faiss.write_index(self.faiss_index, os.path.join(self.index_path, "index.faiss"))
        with open(os.path.join(self.index_path, "chunks.json"), "w") as f:
            json.dump({k: v.model_dump() for k, v in self.chunks.items()}, f)
            
        if self.bm25 is not None:
            with open(os.path.join(self.index_path, "bm25.pkl"), "wb") as f:
                pickle.dump(self.bm25, f)
            with open(os.path.join(self.index_path, "corpus.pkl"), "wb") as f:
                pickle.dump(self.tokenized_corpus, f)

    def load(self):
        index_file = os.path.join(self.index_path, "index.faiss")
        chunks_file = os.path.join(self.index_path, "chunks.json")
        bm25_file = os.path.join(self.index_path, "bm25.pkl")
        corpus_file = os.path.join(self.index_path, "corpus.pkl")
        
        try:
            if os.path.exists(index_file):
                self.faiss_index = faiss.read_index(index_file)
            else:
                logger.warning(f"FAISS index not found at {index_file}")
                
            if os.path.exists(chunks_file):
                with open(chunks_file, "r") as f:
                    data = json.load(f)
                    self.chunks = {k: Chunk(**v) for k, v in data.items()}
            else:
                logger.warning(f"Chunks file not found at {chunks_file}")
                    
            if os.path.exists(bm25_file) and os.path.exists(corpus_file):
                with open(bm25_file, "rb") as f:
                    self.bm25 = pickle.load(f)
                with open(corpus_file, "rb") as f:
                    self.tokenized_corpus = pickle.load(f)
            else:
                logger.warning("BM25 index or corpus not found")
        except Exception as e:
            logger.error(f"Error loading indexes: {e}")
