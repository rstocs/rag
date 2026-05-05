import os
import logging
from PIL import Image
from google import genai
from src.retrieval.retriever import Retriever
from src.config import MODEL_ID, VECTOR_STORE_PATH, MAX_QA_RETRIES, MIN_FAITHFULNESS_SCORE, MIN_RELEVANCE_SCORE, MIN_CONTEXT_RELEVANCE_SCORE
from src.core.client import client
from src.evaluation.evaluator import evaluate_answer
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
            
            if chunk.metadata.get('category') == 'VISUAL_DIAGRAM':
                img_path = os.path.join(VECTOR_STORE_PATH, f"page_{chunk.metadata.get('page')}.png")
                if os.path.exists(img_path):
                    try:
                        img = Image.open(img_path)
                        visual_context.append(img)
                    except Exception as e:
                        logger.error(f"Failed to load image for visual chunk: {e}")
                        
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
            "- Extrapolation Rule: Do not hallucinate ultimate destinations or subsequent steps if the provided context explicitly stops at an intermediate location. Stay strictly within the provided text.\n"
            "- Signature Rule: If handwriting or signatures are messy, cursive, or ambiguous, you MUST state 'Unreadable Signature' rather than attempting to decipher or guess a name. Never report a person's name from a signature unless that name is also typed out in standard text.\n"
            "- Entity Discrimination Rule: If a query asks for details about a specific entity (e.g., an Outfall, Site, or Sample), and the context provides detailed information for a similar entity but not for the requested one, you MUST explicitly state this limitation. Do not merge, transfer, or extrapolate details from one entity to another unless the text explicitly confirms they share identical processes.\n"
            "- Strict Local Grounding Rule: You MUST ONLY use information explicitly found in the 'RETRIEVED CONTEXT' block below. Even if you know the answer from the broader document or previous interactions, you MUST pretend you do not know it if it is not explicitly stated in the provided chunks. Do not add supplementary facts."
        )
        
        prompt = f"RETRIEVED CONTEXT:\n{combined_text_context}\n\nUSER QUESTION: {query}"
        contents = visual_context + [prompt]
        
        # --- Context Relevance Fast-Fail ---
        # Check if the retrieved context is even relevant before wasting generation calls.
        logger.info("Pre-checking context relevance...")
        pre_eval = evaluate_answer(query, "", combined_text_context)
        ctx_score = pre_eval.get("context_relevance_score", 0)
        ctx_reason = pre_eval.get("context_relevance_reason", "")
        if ctx_score < MIN_CONTEXT_RELEVANCE_SCORE:
            logger.warning(f"Context Relevance is 0: {ctx_reason}. Fast-failing to avoid hallucination.")
            return "I don't have enough information in the knowledge base to answer this question accurately."
        logger.info(f"Context Relevance check passed (Score: {ctx_score}).")
        
        answer = ""
        for attempt in range(1, MAX_QA_RETRIES + 1):
            logger.info(f"Generation attempt {attempt}/{MAX_QA_RETRIES}")
            
            response = client.models.generate_content(
                model=MODEL_ID,
                contents=contents,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.0
                )
            )
            answer = response.text.strip()
            
            # Evaluate the answer
            logger.info("Evaluating generated answer...")
            eval_result = evaluate_answer(query, answer, combined_text_context)
            f_score = eval_result.get("faithfulness_score", 0)
            r_score = eval_result.get("relevance_score", 0)
            f_reason = eval_result.get("faithfulness_reason", "")
            r_reason = eval_result.get("relevance_reason", "")
            
            if f_score >= MIN_FAITHFULNESS_SCORE and r_score >= MIN_RELEVANCE_SCORE:
                logger.info(f"Answer passed evaluation (F:{f_score}, R:{r_score}).")
                break
            else:
                logger.warning(f"Answer failed evaluation. Faithfulness: {f_score}, Relevance: {r_score}")
                logger.warning(f"Faithfulness Reason: {f_reason}")
                logger.warning(f"Relevance Reason: {r_reason}")
                if attempt < MAX_QA_RETRIES:
                    logger.info("Attempting self-correction based on feedback...")
                    # Prepare contents for reflection rewrite
                    reflection_prompt = prompts.PROMPT_REFLECTION_FIX.format(
                        query=query,
                        context=combined_text_context,
                        previous_answer=answer,
                        faithfulness_reason=f_reason,
                        relevance_reason=r_reason
                    )
                    contents = visual_context + [reflection_prompt]
                else:
                    logger.warning("Max retries reached. Returning best effort answer.")
        
        return answer
