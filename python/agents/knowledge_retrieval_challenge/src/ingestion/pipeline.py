import os
import uuid
import asyncio
import logging
import fitz
from typing import List
from pdf2image import convert_from_path
from google import genai
from PIL import Image

from src.config import GENERATOR_MODEL_ID, MAX_CONCURRENT_REQUESTS, VECTOR_STORE_PATH
from src.core.client import client
from src.core.embeddings import embed_text, embed_image
from src.generation import prompts
from src.retrieval.indexer import HybridStore, Chunk
from src.ingestion import extractors

logger = logging.getLogger(__name__)

def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> List[str]:
    words = text.split()
    chunks = []
    current_chunk = []
    current_length = 0
    
    for word in words:
        word_len = len(word) + 1 # +1 for space
        if current_length + word_len > chunk_size and current_chunk:
            chunks.append(" ".join(current_chunk))
            overlap_length = 0
            overlap_words = []
            for w in reversed(current_chunk):
                if overlap_length + len(w) + 1 <= overlap:
                    overlap_words.insert(0, w)
                    overlap_length += len(w) + 1
                else:
                    break
            current_chunk = overlap_words + [word]
            current_length = overlap_length + word_len
        else:
            current_chunk.append(word)
            current_length += word_len
            
    if current_chunk:
        chunks.append(" ".join(current_chunk))
        
    return chunks if chunks else [text]

async def process_pymupdf_page(page_text: str, page_num: int, store: HybridStore, semaphore: asyncio.Semaphore) -> str:
    """Fast-path raw text extraction to act as a safety net for boilerplate headers/footers."""
    async with semaphore:
        text_chunks = chunk_text(page_text)
        chunk_objects = []
        loop = asyncio.get_event_loop()
        for i, t_chunk in enumerate(text_chunks):
            emb = await loop.run_in_executor(None, embed_text, t_chunk)
            chunk_objects.append(Chunk(
                id=f"{uuid.uuid4()}_local_{i}",
                text=t_chunk,
                embedding=emb,
                metadata={"page": page_num, "category": "RAW_OCR", "source": "PyMuPDF"}
            ))
        store.add_chunks(chunk_objects)
        return page_text

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
            # Image is embedded directly as a vector (multimodal embedding).
            # No need to save the raw file to disk.
        else:
            text = await extractors.process_standard_text(img)
            
        if text:
            loop = asyncio.get_event_loop()
            text_chunks = chunk_text(text)
            
            chunk_objects = []
            for i, t_chunk in enumerate(text_chunks):
                if category == "VISUAL_DIAGRAM" and i == 0:
                    # Use multimodal image embedding → goes to the image FAISS index
                    logger.info(f"  Using multimodal image embedding for page {page_num}")
                    emb = await loop.run_in_executor(None, embed_image, img)
                    image_chunk = Chunk(
                        id=f"{uuid.uuid4()}_img_{i}",
                        text=t_chunk,
                        embedding=emb,
                        metadata={"page": page_num, "category": category}
                    )
                    store.add_image_chunk(image_chunk)
                else:
                    emb = await loop.run_in_executor(None, embed_text, t_chunk)
                    chunk_objects.append(Chunk(
                        id=f"{uuid.uuid4()}_vlm_{i}",
                        text=t_chunk,
                        embedding=emb,
                        metadata={"page": page_num, "category": category}
                    ))
            
            store.add_chunks(chunk_objects)
            await asyncio.sleep(2)  # Hard sleep to avoid Vertex AI TPM limits
            return text
        return ""

async def process_text_page(page_text: str, img: Image.Image, page_num: int, store: HybridStore, semaphore: asyncio.Semaphore) -> str:
    async with semaphore:
        logger.info(f"Classifying Page {page_num} via text...")
        category = await extractors.classify_text(page_text)
        logger.info(f"  Page {page_num} Text Category: {category}")
        
        if category == "TABLE_DATA":
            text = await extractors.process_table_data(img)
        elif category == "ADMIN_FORM":
            text = await extractors.process_admin_form(img)
        elif category == "VISUAL_DIAGRAM":
            # The text is just a jumble of map labels. We must use the Vision model to see the spatial relationships!
            text = await extractors.process_visual_diagram(img)
        else:
            # Skip VLM! Use extremely fast text-to-text formatting.
            text = await extractors.process_standard_text_from_string(page_text)
            
        if text:
            loop = asyncio.get_event_loop()
            text_chunks = chunk_text(text)
            chunk_objects = []
            for i_chunk, t_chunk in enumerate(text_chunks):
                emb = await loop.run_in_executor(None, embed_text, t_chunk)
                chunk_objects.append(Chunk(
                    id=f"{uuid.uuid4()}_text_processed_{i_chunk}",
                    text=t_chunk,
                    embedding=emb,
                    metadata={"page": page_num, "category": category}
                ))
            store.add_chunks(chunk_objects)
            await asyncio.sleep(0.5)
            return text
        return ""

async def generate_synthetics(full_text: str, store: HybridStore):
    logger.info("Running document-level synthesis...")
    
    # Run summaries sequentially with retries to avoid TPM bursts
    from src.ingestion.extractors import async_retry
    
    @async_retry
    async def get_summary():
        logger.info("  Generating Global Summary...")
        resp = await client.aio.models.generate_content(
            model=GENERATOR_MODEL_ID,
            contents=[full_text, prompts.PROMPT_GLOBAL_SUMMARY]
        )
        text = resp.text.strip()
        loop = asyncio.get_event_loop()
        emb = await loop.run_in_executor(None, embed_text, text)
        return Chunk(id="global_summary", text=text, embedding=emb, metadata={"type": "summary"})
        
    @async_retry
    async def get_qa():
        logger.info("  Generating Synthetic QA...")
        resp = await client.aio.models.generate_content(
            model=GENERATOR_MODEL_ID,
            contents=[full_text, prompts.PROMPT_SYNTHETIC_QA]
        )
        text = resp.text.strip()
        loop = asyncio.get_event_loop()
        emb = await loop.run_in_executor(None, embed_text, text)
        return Chunk(id="synthetic_qa", text=text, embedding=emb, metadata={"type": "synthetic_qa"})

    summary_chunk = await get_summary()
    qa_chunk = await get_qa()
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
        
        # 1. Always ingest the raw PyMuPDF text to act as a safety net for boilerplate
        # (like headers, lab names, and dates) that the VLM might skip.
        if len(page_text) > 100:
            tasks.append(process_pymupdf_page(page_text, page_num, store, semaphore))
            
        # 2. Intelligent Routing: Only send to VLM if it's a visual diagram or classified as a table/form.
        if i < len(images):
            if len(page_text) < 100:
                # Sparse text means it's likely a visual diagram or map. Send full image to VLM.
                tasks.append(process_page(images[i], page_num, store, semaphore))
            else:
                # Dense text. Use cheap text classification to route to VLM (tables) or text-to-text (standard).
                tasks.append(process_text_page(page_text, images[i], page_num, store, semaphore))
            
    if tasks:
        logger.info(f"Processing {len(tasks)} pages via Gemini VLM extractors...")
        vlm_results = await asyncio.gather(*tasks)
        for res in vlm_results:
            if res:
                full_text_parts.append(res)
                
    full_text = "\n".join(full_text_parts)
    if full_text:
        # Truncate to ~500k characters (~125k tokens) to completely avoid 
        # Token-Per-Minute quota bursts when generating global synthetics.
        safe_full_text = full_text[:500000]
        await generate_synthetics(safe_full_text, store)
        
    logger.info("Ingestion complete!")

def run_ingestion(pdf_path: str, max_pages: int = 5):
    asyncio.run(async_run_ingestion(pdf_path, max_pages))
