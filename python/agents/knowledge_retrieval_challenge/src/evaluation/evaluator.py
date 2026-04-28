import json
import logging
from src.core.client import client
from src.config import MODEL_ID
from src.evaluation.prompts import PROMPT_EVALUATE_ANSWER

logger = logging.getLogger(__name__)

def evaluate_answer(question: str, answer: str, context: str) -> dict:
    """
    Uses LLM-as-a-judge to evaluate an answer based on:
    1. Faithfulness (Groundedness in context)
    2. Answer Relevance (Does it answer the question?)
    """
    prompt = PROMPT_EVALUATE_ANSWER.format(question=question, context=context, answer=answer)
    
    try:
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=prompt
        )
        
        text = response.text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.endswith("```"):
            text = text[:-3]
            
        data = json.loads(text)
        return {
            "faithfulness_score": data.get("faithfulness_score", 0),
            "faithfulness_reason": data.get("faithfulness_reason", "Missing reason"),
            "relevance_score": data.get("relevance_score", 0),
            "relevance_reason": data.get("relevance_reason", "Missing reason"),
            "context_relevance_score": data.get("context_relevance_score", 0),
            "context_relevance_reason": data.get("context_relevance_reason", "Missing reason")
        }
    except Exception as e:
        logger.error(f"Failed to parse evaluation response: {e}")
        return {
            "faithfulness_score": 0,
            "faithfulness_reason": f"Evaluation failed: {e}",
            "relevance_score": 0,
            "relevance_reason": f"Evaluation failed: {e}",
            "context_relevance_score": 0,
            "context_relevance_reason": f"Evaluation failed: {e}"
        }
