import asyncio
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from google.genai.errors import APIError
from PIL import Image

from src.core.client import client
from src.config import GENERATOR_MODEL_ID, RETRY_ATTEMPTS
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
        model=GENERATOR_MODEL_ID,
        contents=[image, prompt]
    )
    cat = response.text.strip().upper()
    valid = ["ADMIN_FORM", "TABLE_DATA", "VISUAL_DIAGRAM", "STANDARD_TEXT"]
    return cat if cat in valid else "STANDARD_TEXT"

@async_retry
async def classify_text(text: str) -> str:
    """Uses a fast text-only LLM call to classify the page text."""
    prompt = "Read the following page text. Does it appear to be mostly from a structured data table (TABLE_DATA), a complex fillable form (ADMIN_FORM), or standard paragraphs/instructions/lists (STANDARD_TEXT)? Reply with EXACTLY one of those three category names.\n\n" + text[:2000]
    response = await client.aio.models.generate_content(
        model=GENERATOR_MODEL_ID,
        contents=[prompt]
    )
    cat = response.text.strip().upper()
    valid = ["ADMIN_FORM", "TABLE_DATA", "STANDARD_TEXT"]
    return cat if cat in valid else "STANDARD_TEXT"

@async_retry
async def process_admin_form(image: Image.Image) -> str:
    response = await client.aio.models.generate_content(
        model=GENERATOR_MODEL_ID,
        contents=[image, prompts.PROMPT_ADMIN_FORM]
    )
    return response.text.strip()

@async_retry
async def process_table_data(image: Image.Image) -> str:
    response = await client.aio.models.generate_content(
        model=GENERATOR_MODEL_ID,
        contents=[image, prompts.PROMPT_TABLE_DATA]
    )
    return response.text.strip()

@async_retry
async def process_visual_diagram(image: Image.Image) -> str:
    response = await client.aio.models.generate_content(
        model=GENERATOR_MODEL_ID,
        contents=[image, prompts.PROMPT_VISUAL_DIAGRAM]
    )
    return response.text.strip()

@async_retry
async def process_standard_text(image: Image.Image) -> str:
    prompt = (
        "Extract all the text from this page accurately. Maintain paragraph structure.\n\n"
        "IMPORTANT — DEFINITIONS AND QUALIFIERS:\n"
        "If this page contains any definitions, abbreviations, or explanations of technical terms "
        "(e.g., what a 'Q' flag means, what 'TBD' stands for, what a laboratory qualifier indicates), "
        "extract these definitions verbatim and place them prominently at the TOP of your output, "
        "clearly labeled as 'DEFINITION:'. For example:\n"
        "  DEFINITION: 'Q' flag — An analytical result qualifier indicating the value is outside historical parameters.\n"
        "  DEFINITION: 'TBD' — To Be Determined at a later date.\n"
        "After listing any definitions, extract the rest of the page text normally."
    )
    response = await client.aio.models.generate_content(
        model=GENERATOR_MODEL_ID,
        contents=[image, prompt]
    )
    return response.text.strip()

@async_retry
async def process_standard_text_from_string(text: str) -> str:
    prompt = (
        "Reformat the following text. Maintain paragraph structure.\n\n"
        "IMPORTANT — DEFINITIONS AND QUALIFIERS:\n"
        "If this text contains any definitions, abbreviations, or explanations of technical terms "
        "(e.g., what a 'Q' flag means, what 'TBD' stands for, what a laboratory qualifier indicates), "
        "extract these definitions verbatim and place them prominently at the TOP of your output, "
        "clearly labeled as 'DEFINITION:'. For example:\n"
        "  DEFINITION: 'Q' flag — An analytical result qualifier indicating the value is outside historical parameters.\n"
        "  DEFINITION: 'TBD' — To Be Determined at a later date.\n"
        "After listing any definitions, output the rest of the text normally.\n\n"
        f"TEXT:\n{text}"
    )
    response = await client.aio.models.generate_content(
        model=GENERATOR_MODEL_ID,
        contents=[prompt]
    )
    return response.text.strip()
