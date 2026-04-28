PROMPT_EVALUATE_ANSWER = """
You are an expert evaluator for a Question-Answering system.
Evaluate the following Answer based on the Provided Context and the User Question.

1. Faithfulness: Is the Answer entirely supported by the Context? (Score 0 or 1)
2. Relevance: Does the Answer directly address the User Question? (Score 0 or 1)
3. Context Relevance: Does the Provided Context contain the information necessary to answer the User Question? (Score 0 or 1)

Provide a brief reason for each score. Output ONLY valid JSON in the following format:
{{
    "faithfulness_score": 1,
    "faithfulness_reason": "...",
    "relevance_score": 1,
    "relevance_reason": "...",
    "context_relevance_score": 1,
    "context_relevance_reason": "..."
}}

USER QUESTION: {question}
PROVIDED CONTEXT: {context}
ANSWER TO EVALUATE: {answer}
"""
