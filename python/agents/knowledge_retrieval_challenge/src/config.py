import os
import logging
from dotenv import load_dotenv

load_dotenv()

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# --- Model Configurations ---
GENERATOR_PROVIDER = os.getenv("GENERATOR_PROVIDER", "google")
GENERATOR_MODEL_ID = os.getenv("GENERATOR_MODEL_ID", "gemini-2.5-pro")

EVALUATOR_PROVIDER = os.getenv("EVALUATOR_PROVIDER", "anthropic")
EVALUATOR_MODEL_ID = os.getenv("EVALUATOR_MODEL_ID", "claude-sonnet-4-6")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# --- Embedding Configurations ---
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-004")
EMBEDDING_DIMENSION = 768                 # text-embedding-004 dimension
MULTIMODAL_EMBEDDING_MODEL = os.getenv("MULTIMODAL_EMBEDDING_MODEL", "multimodalembedding@001")
IMAGE_EMBEDDING_DIMENSION = 1408          # multimodalembedding@001 image dimension

# --- Google Cloud ---
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

# --- Paths ---
VECTOR_STORE_PATH = os.getenv("VECTOR_STORE_PATH", "vector_store")
os.makedirs(VECTOR_STORE_PATH, exist_ok=True)

# --- Ingestion Configurations ---
MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", "5"))
RETRY_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "10"))

# --- Retrieval Configurations ---
TOP_K_RETRIEVAL = int(os.getenv("TOP_K_RETRIEVAL", "10"))
RRF_K = int(os.getenv("RRF_K", "60"))

# --- QA & Evaluation Configurations ---
MAX_QA_RETRIES = int(os.getenv("MAX_QA_RETRIES", "3"))
MIN_FAITHFULNESS_SCORE = int(os.getenv("MIN_FAITHFULNESS_SCORE", "1"))
MIN_RELEVANCE_SCORE = int(os.getenv("MIN_RELEVANCE_SCORE", "1"))
MIN_CONTEXT_RELEVANCE_SCORE = int(os.getenv("MIN_CONTEXT_RELEVANCE_SCORE", "1"))
