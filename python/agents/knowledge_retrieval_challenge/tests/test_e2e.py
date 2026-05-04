import logging
import datetime
import time
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
        "List the Table 1 values for all pollutants at Outfall 001.",
        "¿Qué contaminantes se esperan en las descargas de la instalación según el resumen en lenguaje sencillo?",
        "What laboratory performed the analytical testing and what accreditations does it hold?",
        "Who signed or appears in the chain-of-custody forms as having relinquished or received the samples?",
        "Based on the site map, which outfall sampling point is spatially closest to the Vertical Integration Tower and what other structures are nearby?",
        "What stormwater pollutants were detected at Outfall 001 and how do they compare to their MALs?",
        "What is SpaceX's current stock price?",
        "Is this a new permit application or a renewal?",
        "What is the mercury concentration expressed in mg/L rather than µg/L?",
        "Which single pollutant in the grab samples exceeded its MAL by the greatest factor?",
        "What does the 'J' flag mean in the SPL laboratory reports? What about 'ND'?",
        "Were any volatile organic compounds (VOCs) detected above their reporting limits at Outfall 001?",
        "Does the document provide pollutant sample data for Outfall 002 in Table 1 or Table 2?",
        "Has this permit been approved by TCEQ?",
        "Who should I contact at SpaceX if I have technical or administrative questions about this permit?",
        "What is the fluoride concentration in Sample 2 at Outfall 001, and does it have an associated MAL?",
        "¿Cuál es el proceso para que el público comente sobre esta solicitud de permiso?",
        "What are the GPS coordinates of the Starbase facility?"
    ]
    
    passed = 0
    total = len(questions)
    
    results_data = []
    
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
        c_score = eval_result.get('context_relevance_score', 0)
        
        # For our tests, passing requires Faithfulness and Answer Relevance to be 1. 
        # (Context Relevance might be 0 if the data isn't in the index, but we handle that via fast-fail).
        is_pass = f_score >= 1 and r_score >= 1
        
        if is_pass:
            print("✅ TEST PASSED")
            passed += 1
        else:
            print("❌ TEST FAILED")
            print(f"Faithfulness: {f_score} - {eval_result.get('faithfulness_reason')}")
            print(f"Relevance: {r_score} - {eval_result.get('relevance_reason')}")
            
        results_data.append({
            "question": q,
            "answer": answer,
            "f_score": f_score,
            "f_reason": eval_result.get('faithfulness_reason', ''),
            "r_score": r_score,
            "r_reason": eval_result.get('relevance_reason', ''),
            "c_score": c_score,
            "c_reason": eval_result.get('context_relevance_reason', ''),
            "is_pass": is_pass
        })
        
        # Generate Markdown Report incrementally
        report_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        report_lines = [
            f"# Automated E2E Evaluation Report",
            f"**Run Time:** {report_time}\n",
            f"## Summary: {passed}/{len(results_data)} Passed (Currently Running...)\n",
            "| Q# | Status | Faithfulness | Ans. Relevance | Ctx. Relevance | Question |",
            "|---|---|---|---|---|---|",
        ]
        
        for j, res in enumerate(results_data):
            status_icon = "✅" if res['is_pass'] else "❌"
            report_lines.append(f"| {j+1} | {status_icon} | {res['f_score']} | {res['r_score']} | {res['c_score']} | {res['question']} |")
            
        report_lines.append("\n## Detailed Results\n")
        
        for j, res in enumerate(results_data):
            status_icon = "✅ PASSED" if res['is_pass'] else "❌ FAILED"
            report_lines.extend([
                f"### Question {j+1}: {res['question']}",
                f"**Status:** {status_icon}\n",
                f"**Generated Answer:**\n> {res['answer']}\n",
                "**Metrics:**",
                f"- **Faithfulness ({res['f_score']})**: {res['f_reason']}",
                f"- **Answer Relevance ({res['r_score']})**: {res['r_reason']}",
                f"- **Context Relevance ({res['c_score']})**: {res['c_reason']}",
                "\n---\n"
            ])
            
        with open("test_report.md", "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines))
            
        print(f"Test report incrementally saved to test_report.md")
        
        # Pace API requests to avoid 429 Too Many Requests on free tier
        time.sleep(20)
            
    print("\n" + "="*50)
    print(f"EVALUATION SUMMARY: {passed}/{total} Passed")
    print("="*50 + "\n")

if __name__ == "__main__":
    run_tests()
