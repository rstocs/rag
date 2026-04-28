# Prompts used in the Smart Ingestion Pipeline for the SpaceX TPDES Report

PROMPT_ADMIN_FORM = """
You are an expert regulatory document analyst.
Extract all administrative details from this form page into a structured, dense natural language paragraph.
Specifically look for and explicitly state:
- Permit numbers, EPA I.D. numbers, and application types (e.g., "New", "Renewal")
- Applicant names and facility names
- GPS coordinates (Latitude and Longitude)
- Contact names, titles, email addresses, and phone numbers.
Ensure you connect labels to their values (e.g., "The administrative contact is [Name], whose email is [Email]").
Do not output raw JSON; output clear, factual sentences.
"""

PROMPT_TABLE_DATA = """
You are an expert environmental data analyst reading a pollutant worksheet or laboratory report.
Extract the tabular data on this page into self-contained, standalone natural language sentences.
For every row in the table, generate a sentence that includes the column headers. 
For example: "At Outfall 001, the pollutant Mercury has a concentration of 113 µg/L and a Maximum Analytical Level of 0.2 µg/L."
CRITICAL INSTRUCTION: Scan the page for any footnotes or lab qualifiers (e.g., 'J' flags, 'ND' definitions). 
If any data row uses a qualifier, you MUST append its definition inline to the sentence for that row.
"""

PROMPT_VISUAL_DIAGRAM = """
You are an expert engineer analyzing facility maps, site diagrams, or process flow charts.
Describe this image in extreme detail.
If it is a map: List every labeled facility, outfall, sampling point, and building (like the Vertical Integration Tower). Explicitly describe spatial relationships (e.g., "Outfall 001 is located north of the Vertical Integration Tower").
If it is a flow diagram: Describe the entire process flow sequentially from source to destination.
If it is a chain-of-custody form: Extract every printed name, signature, date, and project number visible.
Output dense, factual paragraphs.
"""

PROMPT_GLOBAL_SUMMARY = """
You are a senior regulatory compliance officer. 
I am providing you with the full extracted text of a Texas Pollutant Discharge Elimination System (TPDES) permit application.
Write a comprehensive, 2-page high-level summary of this document. 
Include:
1. The applicant and facility name.
2. The core purpose of the application (e.g., discharging deluge water).
3. A summary of the outfalls (e.g., Outfall 001 and 002) and their discharge pathways.
4. Key findings from the pollutant testing, noting any significant exceedances.
Make sure the summary is detailed and uses formal regulatory vocabulary.
"""

PROMPT_SYNTHETIC_QA = """
You are an expert QA generator. 
Based on the provided extracted text from a permit application, identify any anomalous data, cross-references, or implicit explanations that a user might ask about.
For example, if data is missing for a specific outfall (like Outfall 002), find the explanation in the text and generate a Q&A pair explaining it.
Generate 5-10 highly specific Q&A pairs. 
Format:
Q: [Question]
A: [Detailed Answer based strictly on the text]
"""

PROMPT_QUERY_EXPANSION = """
You are an expert search query expander for a bilingual (English/Spanish) vector database containing regulatory permit applications.
Given the following user query, output a JSON object with a single key "expanded_query" that contains:
1. The original query.
2. Synonyms for key terms (e.g., "water cycle" -> "water balance flow diagram").
3. The Spanish translation of the key terms (e.g., "pollutants" -> "contaminantes").
Output ONLY the raw JSON string without markdown blocks.
Example Output:
{{"expanded_query": "water cycle water balance flow diagram ciclo del agua flujo"}}

User Query: {query}
"""

PROMPT_REFLECTION_FIX = """
You previously generated an answer to a user question based on the provided context. 
However, an automated evaluation determined that your answer was flawed.

EVALUATION FEEDBACK:
Faithfulness Reason: {faithfulness_reason}
Relevance Reason: {relevance_reason}

USER QUESTION: {query}
PROVIDED CONTEXT: 
{context}

PREVIOUS FLAWED ANSWER: 
{previous_answer}

Based on the EVALUATION FEEDBACK, please rewrite your answer to fix these flaws. 
Rely ONLY on the provided context. If the context does not contain the answer, state that you don't know.
"""
