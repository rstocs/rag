# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Structured augmentation document for the SpaceX TPDES corpus.

Table rows, GPS coordinates, and other numeric/structured data embed poorly
in vector search when ingested directly from the PDF layout parser.  This
module generates a supplementary plain-text document that represents all key
tables and facts in natural language sentences — giving the vector store
semantically rich passages that match user queries reliably.

The document is uploaded to GCS and imported into the existing RAG corpus
once, alongside the main PDF.  Subsequent calls detect the existing file and
skip the import.
"""

import os
import time
import tempfile

import requests as http_requests
from google.auth import default
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud import storage
from vertexai.preview import rag

# GCS blob name for the augmentation document
AUGMENTATION_BLOB_NAME = "key_facts_and_data_tables.txt"
AUGMENTATION_DISPLAY_NAME = "key_facts_and_data_tables.txt"

# ---------------------------------------------------------------------------
# Document content
# ---------------------------------------------------------------------------

AUGMENTATION_TEXT = """\
SUPPLEMENTARY KEY FACTS AND DATA TABLES
SpaceX TPDES Permit Application — Starbase Launch Pad Site (Permit WQ0005462000)

This document supplements testing-report.pdf with structured natural-language
representations of tabular data and key document facts, enabling reliable
semantic retrieval.  All values are sourced from the permit application package
submitted to TCEQ on July 1, 2024 (483 pages, bilingual English/Spanish).

====== SECTION 1: FACILITY OVERVIEW AND PERMIT DETAILS ======

The SpaceX TPDES Industrial Wastewater Individual Permit Application covers
Proposed Permit No. WQ0005462000 (EPA I.D. No. TX0146251), submitted by Space
Exploration Technologies Corp. (CN602867657).  The facility is the Starbase
Launch Pad Site (RN111606745), located at 1 Rocket Road, Brownsville, Cameron
County, Texas 78521 — on the south side of the eastern terminus of State
Highway 4.  The application document is 483 pages and is bilingual (English
and Spanish).

Application type: New permit.  This is NOT a renewal or amendment.
Administrative Report 1.0, Item 1.e confirms "New" is checked; Renewal and
Amendment are unchecked.  Application fee paid: $350 (consistent with a new
minor-facility permit not subject to EPA categorical effluent guidelines).

====== SECTION 2: PERMIT STATUS AND TIMELINE ======

Key dates for the SpaceX TPDES permit application:
- July 1, 2024: SpaceX submitted the application to TCEQ.
- July 2, 2024: TCEQ issued a Deficiency Notification requesting additional
  information before the application could be declared administratively
  complete.
- July 8, 2024: Application declared administratively complete.  TCEQ issued
  the Notice of Receipt of Application and Intent to Obtain Water Quality
  Permit (NORI).  The Executive Director would conduct a technical review.

Current status as of document date: technical review pending.  No draft permit
or final permit approval has been issued.  After technical review the Executive
Director may prepare a draft permit and a preliminary decision.  The document
does not record a final TCEQ permit approval.

====== SECTION 3: GPS COORDINATES AND FACILITY LOCATION ======

Facility GPS coordinates from the TCEQ Notice of Receipt of Application (NORI
map link), issued July 8, 2024:
  Latitude:  25.996944 degrees North (25.996944°N)
  Longitude: 97.156388 degrees West  (97.156388°W)  — decimal degrees

The Starbase Launch Pad Site is located near Brownsville, Cameron County,
Texas 78521.  The facility is on the south side of the eastern terminus of
State Highway 4.

====== SECTION 4: KEY SPACEX PERSONNEL AND CONTACTS ======

Technical contact at SpaceX for this permit application:
  Name:    Carolyn Wood
  Title:   Senior Environmental Regulatory Engineer
  Email:   carolyn.wood@spacex.com
  Phone:   (323) 537-0071
  Address: 1 Rocket Rd., Brownsville, TX 78521
  Source:  Administrative Report 1.0, Item 5

Administrative contact at SpaceX for this permit application:
  Name:    Katy Groom
  Title:   Manager, Environmental Regulatory Affairs
  Email:   katy.groom@spacex.com
  Phone:   (321) 730-1469
  Address: L6-1581 Roberts Rd., Kennedy Space Center, FL 32815
  Source:  Administrative Report 1.0, Item 6

Chain-of-custody (CoC) project contact (samples submitted to SPL Kilgore):
  Name:    Rodolfo Longoria
  Company: Space Exploration Technologies Corp. (SpaceX)
  Address: 1 Rocket Rd., Brownsville, TX
  Note:    Listed as project contact for both Project 1105141 (May 2024) and
           Project 1106094 (June 2024).

Sample collector who relinquished samples (signed CoC forms):
  Name:    Zachary Smith (signature: Z. Smith)
  Company: SpaceX
  Source:  SPL Kilgore CoC forms, Projects 1105141 and 1106094

====== SECTION 5: TABLE 1 — CONVENTIONAL PARAMETERS, OUTFALL 001 ======

Source: Worksheet 2.0, Table 1 for Outfall No. 001, Technical Report 1.0,
page 20.  Grab samples collected May 29 – June 6, 2024.  All concentrations
in mg/L unless otherwise noted.

Biochemical Oxygen Demand (BOD, 5-day):
  Sample 1 = 8.49 mg/L;  Sample 2 = 3.56 mg/L.

Carbonaceous BOD (CBOD, 5-day):
  Sample 1 = 2.08 mg/L;  Sample 2 < 2.00 mg/L (non-detect, below reporting
  limit).

Chemical oxygen demand (COD):
  Sample 1 < 20.0 mg/L (non-detect);  Sample 2 < 20.0 mg/L (non-detect).

Total organic carbon (TOC):
  Sample 1 = 3.53 mg/L;  Sample 2 = 3.61 mg/L.

Dissolved oxygen (DO):
  Sample 1 = NA (not applicable);  Sample 2 = 7.1 mg/L.

Ammonia nitrogen (NH3-N):
  Sample 1 = 0.121 mg/L;  Sample 2 = 0.211 mg/L.

Total suspended solids (TSS):
  Sample 1 = 7.50 mg/L;  Sample 2 = 7.10 mg/L.

Nitrate nitrogen (NO3-N):
  Sample 1 = 1.20 mg/L;  Sample 2 = 1.20 mg/L.

Total organic nitrogen:
  Sample 1 < 0.050 mg/L (non-detect);  Sample 2 = 0.161 mg/L.

Total phosphorus:
  Sample 1 = 0.0241 mg/L;  Sample 2 = 0.017 mg/L.

Oil and grease:
  Sample 1 = 3.60 mg/L;  Sample 2 < 4.60 mg/L (non-detect).

Total residual chlorine (TRC):
  Sample 1 = 0.20 mg/L;  Sample 2 = Negative (not detected).

Total dissolved solids (TDS):
  Sample 1 = 950 mg/L;  Sample 2 = 800 mg/L.

Sulfate:
  Sample 1 = 282 mg/L;  Sample 2 = 281 mg/L.

Chloride:
  Sample 1 = 182 mg/L;  Sample 2 = 197 mg/L.

Fluoride:
  Sample 1 = 0.970 mg/L;  Sample 2 = 1.24 mg/L.
  Note: Table 1 does not assign a Maximum Analytical Level (MAL) for fluoride.
  MALs appear only in Table 2 (metals/trace parameters).  Fluoride is a
  conventional parameter in Table 1 without a corresponding MAL column.

Total alkalinity (as CaCO3):
  Sample 1 = 136 mg/L;  Sample 2 = 106 mg/L.

Temperature:
  Sample 1 = 28.1 degrees Celsius;  Sample 2 = 38 degrees Celsius.

pH:
  Sample 1 = 6.97 standard units;  Sample 2 = 8.6 standard units.

====== SECTION 6: TABLE 2 — METALS AND TRACE PARAMETERS, OUTFALL 001 ======

Source: Worksheet 2.0, Table 2 for Outfall No. 001, Technical Report 1.0,
page 21.  Grab samples collected May–June 2024.  All concentrations in
micrograms per liter (µg/L) unless noted.  MAL = Maximum Analytical Level.

Mercury, total — THE POLLUTANT WITH THE GREATEST MAL EXCEEDANCE:
  Sample 1 = 113 µg/L;  Sample 2 = 0.139 µg/L
  MAL = 0.005 µg/L (fresh water) / 0.0005 µg/L (marine)
  Sample 1 at 113 µg/L exceeds the fresh-water MAL of 0.005 µg/L by
  approximately 22,600 times (22,600×).  Sample 2 exceeds the 0.0005 µg/L
  marine MAL by approximately 278×.
  Converted to mg/L: Sample 1 = 0.113 mg/L;  Sample 2 = 0.000139 mg/L.
  MAL in mg/L: 0.000005 mg/L (fresh) / 0.0000005 mg/L (marine).
  Mercury has the highest exceedance ratio of any pollutant in Table 2.

Zinc, total — SECOND-LARGEST MAL EXCEEDANCE:
  Sample 1 = 1420 µg/L;  Sample 2 = 4.3 µg/L;  MAL = 5.0 µg/L.
  Sample 1 exceeds the MAL by approximately 284× (284 times).
  Sample 2 is within the MAL.

Barium, total — THIRD-LARGEST MAL EXCEEDANCE:
  Sample 1 = 94.3 µg/L;  Sample 2 = 85 µg/L;  MAL = 3 µg/L.
  Sample 1 exceeds by ~31.4×;  Sample 2 exceeds by ~28.3×.

Aluminum, total:
  Sample 1 = 70.2 µg/L;  Sample 2 = 61.5 µg/L;  MAL = 2.5 µg/L.
  Sample 1 exceeds by ~28.1×;  Sample 2 exceeds by ~24.6×.

Chromium, hexavalent (Cr6+):
  Sample 1 < 3.00 µg/L (below reporting limit);  Sample 2 = 25.9 µg/L;
  MAL = 3 µg/L.  Sample 2 exceeds by approximately 8.63×.

Copper, total:
  Sample 1 = 9.49 µg/L;  Sample 2 = 0.0747 µg/L;  MAL = 2 µg/L.
  Sample 1 exceeds by approximately 4.75×.

Nickel, total:
  Sample 1 = 6.26 µg/L;  Sample 2 = 0.0224 µg/L;  MAL = 2 µg/L.
  Sample 1 exceeds by approximately 3.13×.

Arsenic, total:
  Sample 1 = 1.88 µg/L;  Sample 2 = 0.0169 µg/L;  MAL = 0.5 µg/L.
  Sample 1 exceeds by approximately 3.76×.

Thallium, total:
  Sample 1 = 0 µg/L (non-detect);  Sample 2 = 0.616 µg/L;  MAL = 0.5 µg/L.
  Sample 2 exceeds by approximately 1.23×.

Selenium, total:
  Sample 1 = 2.86 µg/L;  Sample 2 = 0 µg/L (non-detect);  MAL = 5 µg/L.
  Neither sample exceeds the MAL.

Antimony, total:
  Sample 1 = 1.89 µg/L;  Sample 2 = 1.12 µg/L;  MAL = 5 µg/L.
  Neither sample exceeds the MAL.

Chromium, total:
  Sample 1 = 1.55 µg/L;  Sample 2 = 0.282 µg/L;  MAL = 3 µg/L.
  Neither sample exceeds the MAL.

Cadmium, total:
  Sample 1 = 0.107 µg/L;  Sample 2 = 0 µg/L (non-detect);  MAL = 1 µg/L.
  Neither sample exceeds the MAL.

Cyanide, available:
  Sample 1 = 0 µg/L (non-detect);  Sample 2 = 1.02 µg/L;
  MAL = 2 µg/L (fresh water) / 10 µg/L (marine).
  Neither sample exceeds the MAL.

Lead, total:
  Sample 1 = 0 µg/L (non-detect);  Sample 2 = 0 µg/L (non-detect);
  MAL = 0.5 µg/L.  Neither sample exceeds the MAL.

Silver, total:
  Sample 1 = 0 µg/L (non-detect);  Sample 2 = 0 µg/L (non-detect);
  MAL = 0.5 µg/L.  Neither sample exceeds the MAL.

Beryllium, total:
  Sample 1 = 0 µg/L (non-detect);  Sample 2 = 0 µg/L (non-detect);
  MAL = 0.5 µg/L.  Neither sample exceeds the MAL.

====== SECTION 7: TABLE 3 — VOLATILE ORGANIC COMPOUNDS, OUTFALL 001 ======

Source: Worksheet 2.0, Table 3 for Outfall No. 001, Technical Report 1.0,
pages 22–23.

No volatile organic compounds (VOCs) were detected above their reporting
limits at Outfall 001.  All tested VOCs — including benzene, chloroform,
toluene, ethylbenzene, xylenes, and other compounds — returned non-detect
(ND) results below 1.00–10.0 µg/L.  None exceeded their Maximum Analytical
Levels (MALs).  This is consistent with the facility's use of source water
for the deluge system, which does not involve VOC-generating processes.

====== SECTION 8: TABLE 17 — STORMWATER METALS, OUTFALL 001 ======

Source: Worksheet 8.0, Table 17 for Outfall No. 001, Technical Report 1.0,
page 61.  Stormwater grab samples collected November–December 2023.  All
concentrations in mg/L.  MAL = Maximum Analytical Level.

Arsenic, total:
  Maximum grab sample = 0.0107 mg/L;  MAL = 0.0005 mg/L.
  Exceeds MAL by approximately 21.4×.

Barium, total:
  Maximum grab sample = 0.102 mg/L;  MAL = 0.003 mg/L.
  Exceeds MAL by approximately 34×.

Cadmium, total:
  Maximum grab sample = 0.00241 mg/L;  MAL = 0.001 mg/L.
  Exceeds MAL by approximately 2.41×.

Chromium, total:
  Maximum grab sample = 0.0613 mg/L;  MAL = 0.003 mg/L.
  Exceeds MAL by approximately 20.4×.

Copper, total:
  Maximum grab sample = 0.0101 mg/L;  MAL = 0.002 mg/L.
  Exceeds MAL by approximately 5.05×.

Lead, total:
  Maximum grab sample = 0.00308 mg/L;  MAL = 0.0005 mg/L.
  Exceeds MAL by approximately 6.16×.

Mercury, total:
  Maximum grab sample = < 0.113 mg/L (not detected above reporting limit of
  0.113 mg/L);  MAL = 0.000005 mg/L.  Mercury exceeds the MAL if present at
  any detectable concentration.

Nickel, total:
  Maximum grab sample = 0.00599 mg/L;  MAL = 0.002 mg/L.
  Exceeds MAL by approximately 2.995×.

Selenium, total:
  Maximum grab sample = 0.00298 mg/L;  MAL = 0.005 mg/L.
  Selenium is the ONLY metal in Table 17 that does NOT exceed its MAL.

====== SECTION 9: LABORATORY QUALIFIERS AND ACCREDITATION ======

Analytical testing was performed by SPL, Inc. — Kilgore laboratory, located
at 2600 Dudley Rd., Kilgore, Texas 75662.  Regional office: 2401 Village Dr.,
Suite C, Brownsville, TX 78521.

Accreditation: SPL Kilgore holds International, Federal, and state
accreditations unless otherwise noted.  Results within the lab's NELAC
(National Environmental Laboratory Accreditation Conference) scope are marked
(N)ELAC.  Parameters outside the NELAC scope are marked 'z'.

SPL laboratory result qualifiers:

'J' flag (J qualifier): The analyte was detected below the quantitation limit.
  The result is an estimated concentration between the Method Detection Limit
  (MDL) and the Reporting Limit (RL).  J-qualified values should be used with
  caution because they are estimated, not precisely quantified.

'ND' (Not Detected): The analyte was not measured at or above the Reporting
  Limit (RL).  RL is the minimum quantitation level accounting for the
  Instrument Detection Limit (IDL), Method Detection Limit (MDL), and
  Practical Quantitation Limit (PQL), including any dilutions or
  concentrations applied during sample preparation.

Source: SPL Kilgore Project Reports 1105141 and 1106094 — qualifier footnotes.

====== SECTION 10: OUTFALL 002 DATA AVAILABILITY ======

Worksheet 2.0 provides Tables 1 and 2 only for Outfall 001.  There are no
separate Table 1 or Table 2 data entries for Outfall 002.  Outfall 002
discharges from the retention pond.  The SPL laboratory reports identify the
sampled location as "RETENTION POND" (Project 1105141) and "WW - Retention
Pond" (Project 1106094); these results appear in the lab reports rather than
as a dedicated Worksheet 2.0 table for Outfall 002.

====== END OF SUPPLEMENTARY DOCUMENT ======
"""


# ---------------------------------------------------------------------------
# Upload and import helpers
# ---------------------------------------------------------------------------


def _augmentation_already_imported(corpus_name: str) -> bool:
    """Return True if the augmentation file is already in the corpus."""
    try:
        for f in rag.list_files(corpus_name=corpus_name):
            if f.display_name == AUGMENTATION_DISPLAY_NAME:
                return True
    except Exception:
        pass
    return False


def upload_and_import_augmentation(
    corpus_name: str,
    bucket_name: str,
    project_id: str,
    location: str,
    timeout: int = 300,
) -> None:
    """Upload the augmentation text to GCS and import it into the RAG corpus.

    Idempotent: if the file is already present in the corpus, this is a no-op.
    """
    if _augmentation_already_imported(corpus_name):
        print(f"Augmentation file '{AUGMENTATION_DISPLAY_NAME}' already in corpus — skipping.")
        return

    # Write text to a temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(AUGMENTATION_TEXT)
        tmp_path = tmp.name

    try:
        # Upload to GCS
        client = storage.Client(project=project_id)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(AUGMENTATION_BLOB_NAME)
        blob.upload_from_filename(tmp_path, content_type="text/plain")
        gcs_uri = f"gs://{bucket_name}/{AUGMENTATION_BLOB_NAME}"
        print(f"Uploaded augmentation document → {gcs_uri}")

        # Import into corpus via REST (consistent with prepare_corpus_and_data.py)
        credentials, _ = default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(GoogleAuthRequest())
        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        }

        parts = corpus_name.split("/")
        loc = parts[3] if len(parts) >= 4 else location
        base_url = f"https://{loc}-aiplatform.googleapis.com/v1beta1"
        import_url = f"{base_url}/{corpus_name}/ragFiles:import"

        payload = {
            "import_rag_files_config": {
                "gcs_source": {"uris": [gcs_uri]},
            }
        }

        resp = http_requests.post(
            import_url, json=payload, headers=headers, timeout=30
        )
        resp.raise_for_status()

        op_data = resp.json()
        op_name = op_data.get("name")
        if not op_name:
            print(f"No operation name in import response: {op_data}")
            return

        print(f"Import operation started: {op_name}")
        op_url = f"{base_url}/{op_name}"
        deadline = time.time() + timeout
        poll_interval = 5.0

        while time.time() < deadline:
            time.sleep(poll_interval)
            op_resp = http_requests.get(op_url, headers=headers, timeout=30)
            op_resp.raise_for_status()
            status = op_resp.json()
            if status.get("done"):
                if "error" in status:
                    err = status["error"]
                    print(f"Augmentation import error: {err}")
                else:
                    print("Augmentation document imported successfully.")
                return
            poll_interval = min(poll_interval * 1.5, 30)

        print(f"Timed out waiting for augmentation import after {timeout}s.")

    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Standalone entry point — add augmentation to an already-created corpus
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import vertexai
    from dotenv import load_dotenv

    _cwd_env = os.path.join(os.getcwd(), ".env")
    _env_path = _cwd_env if os.path.exists(_cwd_env) else os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    )
    load_dotenv(_env_path, override=True)

    _project = os.getenv("GOOGLE_CLOUD_PROJECT")
    _location = os.getenv("GOOGLE_CLOUD_LOCATION")
    _corpus = os.getenv("RAG_CORPUS")
    _bucket_raw = os.getenv("STAGING_BUCKET", "")
    _bucket = _bucket_raw.removeprefix("gs://").rstrip("/")

    if not all([_project, _location, _corpus, _bucket]):
        raise SystemExit(
            "Missing required env vars: GOOGLE_CLOUD_PROJECT, "
            "GOOGLE_CLOUD_LOCATION, RAG_CORPUS, STAGING_BUCKET"
        )

    vertexai.init(project=_project, location=_location)
    upload_and_import_augmentation(
        corpus_name=_corpus,
        bucket_name=_bucket,
        project_id=_project,
        location=_location,
    )
    print("Done.")
