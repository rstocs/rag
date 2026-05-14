import os
import logging
from PIL import Image
from google import genai
from src.retrieval.retriever import Retriever
from src.config import GENERATOR_PROVIDER, GENERATOR_MODEL_ID, VECTOR_STORE_PATH, MAX_QA_RETRIES, MIN_FAITHFULNESS_SCORE, MIN_RELEVANCE_SCORE, MIN_CONTEXT_RELEVANCE_SCORE
from src.core.client import client
from src.core.llm_gate import generate_content_with_gate
from src.generation import prompts

logger = logging.getLogger(__name__)

class QASystem:
    def __init__(self, retriever: Retriever):
        self.retriever = retriever

    def ask(self, query: str) -> str:
        """Answers a query using the retrieved context with self-correction."""
        logger.info(f"Answering query: '{query}'")
        chunks = self.retriever.retrieve(query)
        
        if not chunks:
            logger.warning("No context retrieved.")
            return "I don't have enough context in the knowledge base to answer this question."
            
        text_context = []
        visual_context = []
        
        for i, chunk in enumerate(chunks):
            source_info = f"[Source: Page {chunk.metadata.get('page', 'Unknown')}, Category: {chunk.metadata.get('category', 'Unknown')}]"
            if chunk.metadata.get('type') == 'summary':
                source_info = "[Source: Global Document Summary]"
            elif chunk.metadata.get('type') == 'synthetic_qa':
                source_info = "[Source: Synthesized Knowledge]"
                
            text_context.append(f"{source_info}\n{chunk.text}")
                        
        combined_text_context = "\n\n---\n\n".join(text_context)
        
        system_prompt = (
            "You are an expert regulatory compliance analyst for SpaceX. "
            "Your task is to answer the user's question accurately based ONLY on the provided context. "
            "The context may include raw text, extracted tabular data, global summaries, and images (such as maps or flow diagrams).\n"
            "If the answer cannot be determined from the context, explicitly state that you don't know.\n"
            "IMPORTANT CITATION RULES:\n"
            "1. EVERY citation must be in the format '[Source: Page X]' using ONLY the number from the header of the context chunk.\n"
            "2. IGNORE any page numbers you see printed within the document text (e.g., footers, headers, or text like 'Page 15'). They are often incorrect or refer to different sub-documents.\n"
            "3. If you cite a page, and the header says '[Source: Page 20]', you MUST write '[Source: Page 20]' even if the text on that page says 'Page 78'.\n"
            "STRICT FACTUAL CONSTRAINTS:\n"
            "- Affiliation Rule: Do not infer affiliations, corporate relationships, or employment status for individuals unless explicitly stated in the text.\n"
            "- Data Boundary Rule: When extracting tabular or listed data, strictly adhere to the requested boundaries. Do not conflate, merge, or accidentally include data from adjacent but distinct tables or sections.\n"
            "- Reasoning Rule: For questions requiring mathematical calculations, unit conversions, or comparisons (e.g., 'Which is the greatest?'), you MUST explicitly list every candidate value and its source before providing the final answer.\n"
            "- Scientific Limits Rule: Any value preceded by a '<' symbol (e.g., '< 5.0') is a non-detect limit, NOT a detected concentration. Do not treat a '<' value as an actual pollutant concentration, and NEVER use it to calculate an exceedance factor. Ignore all '<' values when looking for the 'greatest' exceedance.\n"
            "- Numerical Comparison Rule: When asked which value is 'greatest', 'highest', or 'largest', you MUST enumerate EVERY candidate value from EVERY table in the context before concluding. Never stop at the first plausible answer. If two tables cover the same pollutant, compare across both.\n"
            "- Extrapolation Rule: Do not hallucinate ultimate destinations or subsequent steps if the provided context explicitly stops at an intermediate location. Stay strictly within the provided text.\n"
            "- Signature Rule: NEVER attempt to read or interpret handwritten signatures or cursive text. If a name appears ONLY as a handwritten signature (not also typed in printed text on the same page), you MUST write 'Unreadable Signature'. A common mistake is misreading cursive letters — for example, reading 'Smith' as 'Smirk', or guessing 'Carolyn Wood' from an ambiguous scrawl. Do not do this. Only report a person's name if it is clearly machine-printed or typed in that same context chunk.\n"
            "- Entity Discrimination Rule: If a query asks for details about a specific entity (e.g., an Outfall, Site, or Sample), and the context provides detailed information for a similar entity but not for the requested one, you MUST explicitly state this limitation. Do not merge, transfer, or extrapolate details from one entity to another unless the text explicitly confirms they share identical processes.\n"
            "- Strict Local Grounding Rule: You MUST ONLY use information explicitly found in the 'RETRIEVED CONTEXT' block below. Even if you know the answer from the broader document or previous interactions, you MUST pretend you do not know it if it is not explicitly stated in the provided chunks. Do not add supplementary facts."
        )
        
        prompt = f"RETRIEVED CONTEXT:\n{combined_text_context}\n\nUSER QUESTION: {query}"
        contents = visual_context + [prompt]
        
        # --- Single-Shot Generation ---
        # We rely strictly on the raw capability of the Generator. 
        # No inline LLM-as-a-judge guiding or fast-failing.
        logger.info("Generating final answer...")
        answer = generate_content_with_gate(
            provider=GENERATOR_PROVIDER,
            model_id=GENERATOR_MODEL_ID,
            text_prompt=prompt,
            images=visual_context,
            system_instruction=system_prompt,
            temperature=0.0
        )
        
        return answer
