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

CRITICAL — TYPED TEXT ONLY:
Extract ONLY text that is machine-printed, typed, or clearly legible as pre-printed form text.
If a field contains ONLY a handwritten signature or cursive writing, output the literal string "UNREADABLE SIGNATURE" for that field.
Do NOT attempt to interpret, phonetically decode, or guess any handwritten name.
Even if you believe you can read a signature, do not output a name. Output "UNREADABLE SIGNATURE".
"""

PROMPT_TABLE_DATA = """
You are an expert environmental data analyst reading a pollutant worksheet or laboratory report.

STEP 1 — IDENTIFY THE TABLE:
Before extracting any data, scan the page header and identify:
(a) The table name or number (e.g., "Table A", "Table 99").
(b) The entity this table belongs to (e.g., "Location No.: X", "Sector 7", "N/A").
State this identification at the very top of your output: e.g., "The following data is from [TABLE NAME] for [ENTITY]."
Repeat this entity label at the beginning of EVERY sentence you generate.

STEP 2 — EXTRACT ROWS:
Extract the tabular data into self-contained, standalone natural language sentences.
For every row in the table, generate a sentence that includes the column headers.
Example: "Table 99 — Location X: The pollutant Lead has a sample result of Not Detected above 0.05 mg/L (Reporting Limit)."

STEP 3 — QUALIFIERS AND DEFINITIONS:
Scan the page for any footnotes, legend boxes, or lab qualifiers (e.g., 'Q' flags, 'TBD' definitions).
If any qualifier is defined on this page, extract its FULL definition as a standalone sentence.
Example: "Laboratory Qualifier 'Q': A 'Q' flag indicates an estimated result where the analyte was outside calibration."
If a data row uses a qualifier, append its meaning inline to that row's sentence.

STEP 4 — TRANSLATE SYMBOLS:
Translate all mathematical symbols or shorthand into descriptive text:
'<' → "Not Detected above [value]"
'ND' → "Not Detected above the Reporting Limit"
'>' → "Greater than [value]"
"""

PROMPT_VISUAL_DIAGRAM = """
You are an expert engineer analyzing facility maps, site diagrams, or process flow charts.

If this image is a SITE MAP or FACILITY MAP:
1. List every labeled structure, outfall, sampling point, building, basin, or feature visible on the map.
2. SPATIAL RELATIONSHIPS (most important): For each pair of features that are spatially adjacent or notably close, write an explicit sentence describing their relative positions using cardinal directions (north, south, east, west) and approximate proximity. For example:
   - "Outfall/Sampling Point 1 is located approximately [X distance] to the southwest of the Vertical Integration Tower."
   - "The Launch Pad is directly north of Starship Test Stand B."
   - "Retention Basin 1 is southeast of the Power and Control Systems Building."
3. Identify which outfall or sampling point appears spatially CLOSEST to the Vertical Integration Tower, and state this explicitly.

If this image is a PROCESS FLOW DIAGRAM:
Describe the entire process flow sequentially from source to destination. Explicitly trace all flow paths and clearly identify specific node labels, discharge points, or IDs without making assumptions about ambiguous connections.

If this image is a CHAIN-OF-CUSTODY FORM:
Extract every PRINTED (typed) name, date, and project number visible.
For any signature field that contains only handwritten/cursive content, write "UNREADABLE SIGNATURE".
Do NOT guess or phonetically decode any handwritten name.

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
