import io
import logging
from typing import List
from PIL import Image

# --- Text embeddings: text-embedding-004 via GenAI SDK (no char limit) ---
from src.core.client import client as genai_client
from src.config import EMBEDDING_MODEL

# --- Image embeddings: multimodalembedding@001 via Vertex AI SDK ---
import vertexai
from vertexai.vision_models import MultiModalEmbeddingModel, Image as VertexImage
from src.config import GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, IMAGE_EMBEDDING_DIMENSION

logger = logging.getLogger(__name__)

vertexai.init(project=GOOGLE_CLOUD_PROJECT, location=GOOGLE_CLOUD_LOCATION)
_mm_model = MultiModalEmbeddingModel.from_pretrained("multimodalembedding@001")


from tenacity import retry, wait_exponential, stop_after_attempt
from src.config import RETRY_ATTEMPTS

@retry(wait=wait_exponential(multiplier=1, min=4, max=60), stop=stop_after_attempt(RETRY_ATTEMPTS), reraise=True)
def embed_text(text: str) -> List[float]:
    """Embed text using text-embedding-004 (768-dim, no character limit)."""
    response = genai_client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
    )
    return response.embeddings[0].values


@retry(wait=wait_exponential(multiplier=1, min=4, max=60), stop=stop_after_attempt(RETRY_ATTEMPTS), reraise=True)
def embed_image(image: Image.Image) -> List[float]:
    """Embed a PIL Image using multimodalembedding@001 (1408-dim image vector)."""
    buffered = io.BytesIO()
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        image = image.convert("RGB")
    image.save(buffered, format="PNG")

    vertex_image = VertexImage(image_bytes=buffered.getvalue())
    result = _mm_model.get_embeddings(
        image=vertex_image,
        dimension=IMAGE_EMBEDDING_DIMENSION,
    )
    return result.image_embedding


@retry(wait=wait_exponential(multiplier=1, min=4, max=60), stop=stop_after_attempt(RETRY_ATTEMPTS), reraise=True)
def embed_text_for_image_search(text: str) -> List[float]:
    """Embed text using the multimodal model to produce a 1408-dim vector
    that can be compared against image embeddings in the image FAISS index.
    """
    truncated = text[:1024]  # model hard limit on text-only queries
    result = _mm_model.get_embeddings(
        contextual_text=truncated,
        dimension=IMAGE_EMBEDDING_DIMENSION,
    )
    return result.text_embedding
