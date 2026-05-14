import logging
import datetime
import time
from src.retrieval.indexer import HybridStore
from src.retrieval.retriever import Retriever
from src.generation.qa_system import QASystem
from src.evaluation.evaluator import evaluate_answer

logging.basicConfig(level=logging.WARNING, format='%(levelname)s - %(message)s')

def run_failed_tests():
    store = HybridStore()
    retriever = Retriever(store)
    qa = QASystem(retriever)
    
    # Retrieval-gap failures targeted by the cross-encoder re-ranker:
    # Q8  (F=0.33, R=0.33, C=0.33) — lab accreditations not retrieved
    # Q16 (F=0.33, R=1.00, C=0.33) — 'J' flag definition page not retrieved
    # Q17 (F=0.33, R=1.00, C=0.67) — VOC table for Outfall 001 not retrieved
    failed_questions = [
        "What laboratory performed the analytical testing and what accreditations does it hold?",
        "What does the 'J' flag mean in the SPL laboratory reports? What about 'ND'?",
        "Were any volatile organic compounds (VOCs) detected above their reporting limits at Outfall 001?",
    ]
    
    iterations = 3
    results = {}
    
    report_file = "test_failed_report.md"
    
    with open(report_file, "w") as f:
        f.write("# Failed Test Cases - Average Score Report\n")
        f.write(f"**Run Time:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**Iterations per question:** {iterations}\n\n")
        f.write("| Q# | Avg Faithfulness | Avg Relevance | Avg Ctx Rel | Question |\n")
        f.write("|---|---|---|---|---|\n")

    print("\n" + "="*60)
    print(f"STARTING AVERAGE EVALUATION ({iterations} runs per question)")
    print("="*60 + "\n")

    for i, q in enumerate(failed_questions, 1):
        print(f"\n--- Question {i} ---")
        print(f"Q: {q}")
        
        total_f = 0
        total_r = 0
        total_c = 0
        
        run_details = []
        
        for run in range(iterations):
            print(f"  Run {run+1}/{iterations}...")
            chunks = retriever.retrieve(q)
            context = "\n".join([c.text for c in chunks])
            
            answer = qa.ask(q)
            eval_result = evaluate_answer(q, answer, context)
            
            f_score = eval_result.get('faithfulness_score', 0)
            r_score = eval_result.get('relevance_score', 0)
            c_score = eval_result.get('context_relevance_score', 0)
            
            total_f += f_score
            total_r += r_score
            total_c += c_score
            
            run_details.append({
                "run": run + 1,
                "answer": answer,
                "f": f_score, "r": r_score, "c": c_score,
                "f_reason": eval_result.get('faithfulness_reason', ''),
                "r_reason": eval_result.get('relevance_reason', '')
            })
            
            # Rate limit pacing
            time.sleep(20)
            
        avg_f = total_f / iterations
        avg_r = total_r / iterations
        avg_c = total_c / iterations
        
        results[q] = {
            "avg_f": avg_f,
            "avg_r": avg_r,
            "avg_c": avg_c,
            "runs": run_details
        }
        
        # Incrementally update the markdown
        with open(report_file, "a") as f:
            f.write(f"| {i} | {avg_f:.2f} | {avg_r:.2f} | {avg_c:.2f} | {q} |\n")
            
        print(f"  Average for Q{i}: F={avg_f:.2f}, R={avg_r:.2f}, C={avg_c:.2f}")

    # Write detailed results at the bottom
    with open(report_file, "a") as f:
        f.write("\n## Detailed Results\n")
        for i, q in enumerate(failed_questions, 1):
            f.write(f"\n### Question {i}: {q}\n")
            f.write(f"**Average Scores:** Faithfulness: {results[q]['avg_f']:.2f} | Relevance: {results[q]['avg_r']:.2f} | Context Relevance: {results[q]['avg_c']:.2f}\n\n")
            for run in results[q]['runs']:
                f.write(f"#### Run {run['run']}\n")
                f.write(f"**Answer:** {run['answer']}\n")
                f.write(f"- **Faithfulness ({run['f']})**: {run['f_reason']}\n")
                f.write(f"- **Relevance ({run['r']})**: {run['r_reason']}\n\n")
                
    print(f"\nFinished! Results saved to {report_file}")

if __name__ == "__main__":
    run_failed_tests()
