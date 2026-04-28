import argparse
import time
import sys
import logging

from src.ingestion.pipeline import run_ingestion
from src.retrieval.indexer import HybridStore
from src.retrieval.retriever import Retriever
from src.generation.qa_system import QASystem
from src.evaluation.evaluator import evaluate_answer

logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Enterprise RAG System for Regulatory PDFs")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Ingestion command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest a PDF file")
    ingest_parser.add_argument("pdf_path", help="Path to the PDF file")
    ingest_parser.add_argument("--max-pages", type=int, default=10, help="Maximum number of pages to process")

    # Query command
    query_parser = subparsers.add_parser("query", help="Ask a question")
    query_parser.add_argument("question", type=str, help="The question to ask")
    
    # Evaluate command
    evaluate_parser = subparsers.add_parser("evaluate", help="Ask a question and evaluate the answer")
    evaluate_parser.add_argument("question", type=str, help="The question to evaluate")

    args = parser.parse_args()

    if args.command == "ingest":
        start_time = time.time()
        run_ingestion(args.pdf_path, max_pages=args.max_pages)
        logger.info(f"Ingestion completed in {time.time() - start_time:.2f} seconds.")
        
    elif args.command == "query":
        store = HybridStore()
        retriever = Retriever(store)
        qa = QASystem(retriever)
        
        start_time = time.time()
        logger.info("Searching and analyzing...")
        answer = qa.ask(args.question)
        logger.info(f"--- Answer (Time: {time.time() - start_time:.2f}s) ---")
        logger.info(f"\n{answer}")
        
        
    elif args.command == "evaluate":
        store = HybridStore()
        retriever = Retriever(store)
        qa = QASystem(retriever)
        
        start_time = time.time()
        logger.info("Searching, analyzing, and evaluating...")
        
        # We need the context used by QA to pass to evaluator. 
        chunks = retriever.retrieve(args.question)
        context = "\n".join([c.text for c in chunks])
        
        answer = qa.ask(args.question)
        logger.info(f"--- Answer (Time: {time.time() - start_time:.2f}s) ---")
        logger.info(f"\n{answer}")
        
        logger.info("--- Evaluation ---")
        eval_result = evaluate_answer(args.question, answer, context)
        logger.info(f"Faithfulness Score: {eval_result.get('faithfulness_score')} - {eval_result.get('faithfulness_reason')}")
        logger.info(f"Relevance Score: {eval_result.get('relevance_score')} - {eval_result.get('relevance_reason')}")
        
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
