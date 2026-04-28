import logging
from src.retrieval.indexer import HybridStore
from src.retrieval.retriever import Retriever
from src.generation.qa_system import QASystem
from src.evaluation.evaluator import evaluate_answer

logging.basicConfig(level=logging.WARNING, format='%(levelname)s - %(message)s')

def run_tests():
    store = HybridStore()
    retriever = Retriever(store)
    qa = QASystem(retriever)
    
    questions = [
        "What does the report say about mercury levels?",
        "Which outfall sampling point is closest to the Vertical Integration Tower?",
        "Describe the water cycle---from source to Outfall 001---as detailed in the PDF.",
        "Which SpaceX employees listed in the document appear in the chain-of-custody records?",
        "Provide a high-level summary of the document.",
        "List the Table 1 values for all pollutants at Outfall 001."
    ]
    
    passed = 0
    total = len(questions)
    
    print("\n" + "="*50)
    print("STARTING AUTOMATED E2E EVALUATION")
    print("="*50)
    
    for i, q in enumerate(questions):
        print(f"\n--- Question {i+1} ---")
        print(f"Q: {q}")
        
        # We need the context used by QA to pass to evaluator.
        chunks = retriever.retrieve(q)
        context = "\n".join([c.text for c in chunks])
        
        answer = qa.ask(q)
        print(f"\nA: {answer}\n")
        
        eval_result = evaluate_answer(q, answer, context)
        f_score = eval_result.get('faithfulness_score', 0)
        r_score = eval_result.get('relevance_score', 0)
        
        if f_score >= 1 and r_score >= 1:
            print("✅ TEST PASSED")
            passed += 1
        else:
            print("❌ TEST FAILED")
            print(f"Faithfulness: {f_score} - {eval_result.get('faithfulness_reason')}")
            print(f"Relevance: {r_score} - {eval_result.get('relevance_reason')}")
            
    print("\n" + "="*50)
    print(f"EVALUATION SUMMARY: {passed}/{total} Passed")
    print("="*50 + "\n")

if __name__ == "__main__":
    run_tests()
