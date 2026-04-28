from typing import List
from src.core.client import client
from src.config import EMBEDDING_MODEL

def embed_text(text: str) -> List[float]:
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
    )
    return response.embeddings[0].values
