import asyncio
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from google.genai.errors import APIError
from PIL import Image

from src.core.client import client
from src.config import MODEL_ID, RETRY_ATTEMPTS
from src.generation import prompts

# Create an async retry decorator for Google GenAI API calls
async_retry = retry(
    wait=wait_exponential(multiplier=1, min=4, max=60),
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    retry=retry_if_exception_type(Exception), # Catch generic exceptions as GenAI throws various types
    reraise=True
)

@async_retry
async def classify_page(image: Image.Image) -> str:
    """Uses a fast VLM call to classify the page into a modality."""
    prompt = "Classify this page as exactly one of: ADMIN_FORM, TABLE_DATA, VISUAL_DIAGRAM, STANDARD_TEXT. Reply with only the category name."
    # Using async call
    response = await client.aio.models.generate_content(
        model=MODEL_ID,
        contents=[image, prompt]
    )
    cat = response.text.strip().upper()
    valid = ["ADMIN_FORM", "TABLE_DATA", "VISUAL_DIAGRAM", "STANDARD_TEXT"]
    return cat if cat in valid else "STANDARD_TEXT"

@async_retry
async def process_admin_form(image: Image.Image) -> str:
    response = await client.aio.models.generate_content(
        model=MODEL_ID,
        contents=[image, prompts.PROMPT_ADMIN_FORM]
    )
    return response.text.strip()

@async_retry
async def process_table_data(image: Image.Image) -> str:
    response = await client.aio.models.generate_content(
        model=MODEL_ID,
        contents=[image, prompts.PROMPT_TABLE_DATA]
    )
    return response.text.strip()

@async_retry
async def process_visual_diagram(image: Image.Image) -> str:
    response = await client.aio.models.generate_content(
        model=MODEL_ID,
        contents=[image, prompts.PROMPT_VISUAL_DIAGRAM]
    )
    return response.text.strip()

@async_retry
async def process_standard_text(image: Image.Image) -> str:
    prompt = "Extract all the text from this page accurately. Maintain paragraph structure."
    response = await client.aio.models.generate_content(
        model=MODEL_ID,
        contents=[image, prompt]
    )
    return response.text.strip()
