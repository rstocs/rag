import os
import uuid
import asyncio
import logging
import fitz
from typing import List
from pdf2image import convert_from_path
from google import genai
from PIL import Image

from src.config import MODEL_ID, MAX_CONCURRENT_REQUESTS, VECTOR_STORE_PATH
from src.core.client import client
from src.core.embeddings import embed_text
from src.generation import prompts
from src.retrieval.indexer import HybridStore, Chunk
from src.ingestion import extractors

logger = logging.getLogger(__name__)

async def process_page(img: Image.Image, page_num: int, store: HybridStore, semaphore: asyncio.Semaphore) -> str:
    async with semaphore:
        logger.info(f"Processing Page {page_num}...")
        
        category = await extractors.classify_page(img)
        logger.info(f"  Page {page_num} Category: {category}")
        
        if category == "ADMIN_FORM":
            text = await extractors.process_admin_form(img)
        elif category == "TABLE_DATA":
            text = await extractors.process_table_data(img)
        elif category == "VISUAL_DIAGRAM":
            text = await extractors.process_visual_diagram(img)
            img_path = os.path.join(VECTOR_STORE_PATH, f"page_{page_num}.png")
            img.save(img_path)
        else:
            text = await extractors.process_standard_text(img)
            
        if text:
            # We embed synchronously for now as embedding API might not support aio natively
            # or it's fast enough. But doing it asynchronously is better if supported.
            # Using loop.run_in_executor to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            emb = await loop.run_in_executor(None, embed_text, text)
            
            chunk = Chunk(
                id=str(uuid.uuid4()),
                text=text,
                embedding=emb,
                metadata={"page": page_num, "category": category}
            )
            store.add_chunks([chunk])
            return text
        return ""

async def generate_synthetics(full_text: str, store: HybridStore):
    logger.info("Running document-level synthesis...")
    
    # Run summaries concurrently
    async def get_summary():
        logger.info("  Generating Global Summary...")
        resp = await client.aio.models.generate_content(
            model=MODEL_ID,
            contents=[full_text, prompts.PROMPT_GLOBAL_SUMMARY]
        )
        text = resp.text.strip()
        loop = asyncio.get_event_loop()
        emb = await loop.run_in_executor(None, embed_text, text)
        return Chunk(id="global_summary", text=text, embedding=emb, metadata={"type": "summary"})
        
    async def get_qa():
        logger.info("  Generating Synthetic QA...")
        resp = await client.aio.models.generate_content(
            model=MODEL_ID,
            contents=[full_text, prompts.PROMPT_SYNTHETIC_QA]
        )
        text = resp.text.strip()
        loop = asyncio.get_event_loop()
        emb = await loop.run_in_executor(None, embed_text, text)
        return Chunk(id="synthetic_qa", text=text, embedding=emb, metadata={"type": "synthetic_qa"})

    summary_chunk, qa_chunk = await asyncio.gather(get_summary(), get_qa())
    store.add_chunks([summary_chunk, qa_chunk])

async def async_run_ingestion(pdf_path: str, max_pages: int = 5):
    logger.info(f"Starting hybrid async ingestion for {pdf_path}")
    store = HybridStore()
    
    doc = fitz.open(pdf_path)
    num_pages = min(max_pages, len(doc))
    
    logger.info(f"Processing up to {num_pages} pages...")
    
    # We still need images for the fallback, but we can generate them lazily or all at once.
    # To keep it simple, we generate images for the required pages.
    logger.info("Extracting images for fallback VLM processing...")
    images = convert_from_path(pdf_path, first_page=1, last_page=num_pages)
    
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    tasks = []
    
    full_text_parts = []
    
    for i in range(num_pages):
        page_num = i + 1
        page_text = doc[i].get_text("text").strip()
        
        if len(page_text) > 100:
            # Fast Local Extraction: Skip Gemini!
            logger.info(f"Page {page_num} processed locally via PyMuPDF (Length: {len(page_text)})")
            
            emb = embed_text(page_text)
            chunk = Chunk(
                id=str(uuid.uuid4()),
                text=page_text,
                embedding=emb,
                metadata={"page": page_num, "category": "STANDARD_TEXT", "source": "PyMuPDF"}
            )
            store.add_chunks([chunk])
            full_text_parts.append(page_text)
        else:
            # Fallback to Gemini VLM for images/tables/complex forms
            if i < len(images):
                tasks.append(process_page(images[i], page_num, store, semaphore))
            
    if tasks:
        logger.info(f"Falling back to Gemini VLM for {len(tasks)} complex pages...")
        vlm_results = await asyncio.gather(*tasks)
        for res in vlm_results:
            if res:
                full_text_parts.append(res)
                
    full_text = "\n".join(full_text_parts)
    if full_text:
        await generate_synthetics(full_text, store)
        
    logger.info("Ingestion complete!")

def run_ingestion(pdf_path: str, max_pages: int = 5):
    asyncio.run(async_run_ingestion(pdf_path, max_pages))
