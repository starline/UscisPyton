# UscisPython

![UscisPython preview](assets/preview.jpg)

Small Python toolkit for working with **USCIS Administrative Appeals Office (AAO) non-precedent decisions**: bulk-download decision PDFs from the public listing, then summarize each case (beneficiary role and denial rationale) using the OpenAI API.

## Features

- **PDF download** — Crawls the [AAO non-precedent decisions](https://www.uscis.gov/administrative-appeals/aao-decisions/aao-non-precedent-decisions) list, follows Drupal pagination (`rel="next"`), downloads linked `.pdf` files, and logs successful URLs.
- **Denial summary** — Reads text from downloaded PDFs (embedded text only; no OCR), sends an excerpt to OpenAI Chat Completions, and writes a UTF-8 TSV with filename, beneficiary profession/title/field (in Russian), and a detailed denial/evidence summary (in Russian).

## Requirements

- Python 3.10+ (uses `str | None` style hints in the summary script)
- Dependencies: see [`requirements.txt`](requirements.txt)

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Unix:    source .venv/bin/activate
pip install -r requirements.txt
```

For the summary step, copy [`.env.example`](.env.example) to `.env` and set `OPENAI_API_KEY`. Optionally set `OPENAI_MODEL` (default in code: `gpt-4o-mini`).

## Usage

The repo includes a small launcher that runs scripts from the `scripts/` folder:

```bash
python run.py --list
python run.py uscis_pdf_download
python run.py uscis_pdf_download --year 2025 --topic 18
python run.py uscis_pdf_denial_summary
```

You can also run scripts directly:

```bash
python scripts/uscis_pdf_download.py --year 2025 --topic 19
python scripts/uscis_pdf_denial_summary.py --help
```

### `uscis_pdf_download`

- **Input:** Optional `--year` (defaults to the current calendar year). Optional `--topic` (defaults to `18` — I-140 Advanced Degree / Exceptional Ability / NIW). Use `--topic All` for every topic.
- **Output:**
  - PDFs → `files/uscis_pdfs/pdfs/<topic>/<year>/` (e.g. `pdfs/18/2025/`)
  - Successful PDF URLs (rewritten each run) → `files/uscis_pdfs/pdf_urls.txt`

#### Topics (`--topic`)

Codes from the [AAO non-precedent decisions](https://www.uscis.gov/administrative-appeals/aao-decisions/aao-non-precedent-decisions) topic filter (may change on the USCIS site):

| Code | Topic |
|------|-------|
| `All` | All topics |
| `1` | 8 CFR 214.2(h)(6)(v)(H) - Invalidation of Temporary Labor Certificates Issued by Governor of Guam |
| `2` | I-129 - Petition for a Nonimmigrant Treaty Investor (E-2) |
| `3` | I-129 - Petition for a Nonimmigrant Worker (Athlete, Artist, or Entertainer – P) |
| `4` | I-129 - Petition for a Nonimmigrant Worker (Extraordinary Ability – O) |
| `5` | I-129 - Petition for a Nonimmigrant Worker (Free Trade Agreement / NAFTA Professional – TN) |
| `6` | I-129 - Petition for a Nonimmigrant Worker (International Exchange or Cultural Worker – Q) |
| `7` | I-129 - Petition for a Nonimmigrant Worker (Intracompany Transferee – L-1) |
| `8` | I-129 - Petition for a Nonimmigrant Worker (Religious Worker – R-1) |
| `9` | I-129 - Petition for a Nonimmigrant Worker (Temporary Worker in a Specialty Occupation or Fashion Model – H-1B) |
| `10` | I-129 - Petition for a Nonimmigrant Worker (Temporary Worker Performing Agricultural Labor or Services – H-2A) |
| `11` | I-129 - Petition for a Nonimmigrant Worker (Temporary Worker Performing Non-Agricultural Labor or Services – H-2B) |
| `12` | I-129 - Petition for a Nonimmigrant Worker (Trainee – H-3) |
| `13` | I-129CW - Petition for a CNMI-Only Nonimmigrant Transitional Worker |
| `14` | I-129F - Petition for Alien Fiancé(e) |
| `15` | I-130 - Petition for Alien Relative (Adam Walsh Act Only) |
| `16` | I-131 - Application for Issuance of Reentry Permit |
| `17` | I-131 - Application for Refugee Travel Document |
| `18` | I-140 - Immigrant Petition for Alien Worker (Advanced Degree, Exceptional Ability, National Interest Waiver) |
| `19` | I-140 - Immigrant Petition for Alien Worker (Extraordinary Ability) |
| `20` | I-140 - Immigrant Petition for Alien Worker (Multinational Managers/Executives) |
| `21` | I-140 - Immigrant Petition for Alien Worker (Outstanding Professors/Researchers) |
| `22` | I-140 - Immigrant Petition for Alien Worker (Professionals and Other Workers) |
| `23` | I-140 - Immigrant Petition for Alien Worker (Skilled Workers filed after 1-6-2010) |
| `24` | I-212 - Application for Permission to Reapply for Admission into the United States After Deportation or Removal |
| `25` | I-352 - Breach of Delivery Bond |
| `26` | I-352 - Breach of Maintenance of Status Bond |
| `27` | I-352 - Breach of Voluntary Departure Bond |
| `28` | I-360 - Petition for Abused Parent of U.S. Citizen |
| `29` | I-360 - Petition for Battered or Abused Spouse or Child under VAWA |
| `30` | I-360 - Petition for Special Immigrant Armed Forces Member |
| `31` | I-360 - Petition for Special Immigrant Employee of an International Organization |
| `32` | I-360 - Petition for Special Immigrant Employee of Panama Canal Company, U.S. Government in Canal Zone, or Canal Zone Government |
| `33` | I-360 - Petition for Special Immigrant Employee or Former Employee of U.S. Government Abroad |
| `34` | I-360 - Petition for Special Immigrant Juvenile |
| `35` | I-360 - Petition for Special Immigrant Physician |
| `36` | I-360 - Petition for Special Immigrant Religious Worker |
| `37` | I-360 - Petition for Special Immigrant Status as an Afghan or Iraqi Translator |
| `38` | I-360 - Petition for Special Immigrant Status as an Iraqi Employee of the U.S. Government |
| `39` | I-360 - Petition to Classify Amerasian as Child of U.S. Citizen |
| `40` | I-485 - Application for Adjustment of Status of Alien in T Nonimmigrant Status |
| `41` | I-485 - Application for Adjustment of Status of Alien in U Nonimmigrant Status |
| `42` | I-485 - Application for Adjustment of Status of Certain Cuban and Haitian Nationals |
| `43` | I-485 - Application for Adjustment of Status of Diplomat |
| `44` | I-485 - Application for Adjustment of Status of Indochinese Refugees |
| `45` | I-485 - Application to Register Permanent Residence or Adjust Status |
| `46` | I-485 - Certification of Adjustment of Status for Cuban Nationals |
| `47` | I-485 - Denial of Adjustment for Not Establishing Bona Fide Marriage Exemption |
| `48` | I-526 - Immigrant Petition by Alien Entrepreneur |
| `49` | I-600 - Petition to Classify Orphan as an Immediate Relative |
| `50` | I-600A - Application for Advance Processing of an Orphan Petition |
| `51` | I-601 - Application for Waiver of Grounds of Inadmissibility (Criminal and Related) |
| `52` | I-601 - Application for Waiver of Grounds of Inadmissibility (Exception) |
| `53` | I-601 - Application for Waiver of Grounds of Inadmissibility (Fraud or Misrepresentation) |
| `54` | I-601 - Application for Waiver of Grounds of Inadmissibility (Health-Related) |
| `55` | I-601 - Application for Waiver of Grounds of Inadmissibility (Unlawful Presence) |
| `56` | I-612 - Application for Waiver of the Foreign Residence Requirement |
| `57` | I-687 - Legalization: Application for Temporary Resident Status |
| `58` | I-687 - Legalization: Termination of Temporary Resident Status |
| `59` | I-690 - Legalization and Special Agricultural Workers: Application for Waiver of Grounds of Inadmissibility |
| `60` | I-698/I-485 - Legalization: Application to Adjust Status from Temporary to Permanent Resident |
| `61` | I-700 - Special Agricultural Workers: Application for Adjustment to Permanent Resident Status |
| `62` | I-700 - Special Agricultural Workers: Application for Temporary Resident Status |
| `63` | I-700 - Special Agricultural Workers: Termination of Temporary Resident Status |
| `64` | I-800 - Petition to Classify Convention Adoptee as an Immediate Relative |
| `65` | I-800A - Application for Determination of Suitability to Adopt a Child from a Convention Country |
| `66` | I-821 - Application for Temporary Protected Status |
| `67` | I-829 - Petition by Entrepreneur to Remove Conditions |
| `68` | I-905 - Application for Authorization to Issue Certification for Health Care Workers |
| `69` | I-914 - Application for T Nonimmigrant Status |
| `70` | I-918 - Petition for U Nonimmigrant Status |
| `71` | I-924 - Application For Regional Center Under the Immigrant Investor Pilot Program |
| `72` | I-929 - Petition for Qualifying Family Member of a U-1 Nonimmigrant |
| `73` | N-470 - Application to Preserve Residence for Naturalization Purposes |
| `74` | N-565 - Application for Replacement Naturalization/Citizenship Document |
| `75` | N-565 - Application for Special Certificate of Naturalization |
| `76` | N-600 - Application for Certificate of Citizenship |
| `77` | Section 101 - Petition for Approval of School for Attendance by Nonimmigrant Students |
| `78` | Section 205 - Revocation of Approval of Immigrant Visa Petition |
| `79` | Section 205 - Revocation of Approval of Nonimmigrant Visa Petition |
| `80` | Section 323 - Application for Certificate of Naturalization of Adopted Children |
| `81` | Section 340 - Revocation of Naturalization |
| `82` | Section 342 - Administrative Cancellation of Certificates, Documents, or Records |

### `uscis_pdf_denial_summary`

- **Input:** `*.pdf` under `files/uscis_pdfs/pdfs/` by default (including folders like `18/2025/`; override with `--dir`).
- **Output:** TSV `files/uscis_pdfs/summary_denials.txt` (tab-separated: `filename`, profession summary, denial summary), unless you pass `--output`.
- **Options:** `--dir`, `--output`, `--model` (or use `OPENAI_MODEL` in the environment).

**Note:** Only the first ~12,000 characters of extracted text per PDF are sent to the model. Scanned PDFs without a text layer will produce empty extraction and a placeholder explanation.

## Project layout

| Path | Purpose |
|------|---------|
| `run.py` | Launcher: `python run.py <script_name> [args...]` |
| `scripts/uscis_pdf_download.py` | Fetch AAO non-precedent PDFs |
| `scripts/uscis_pdf_denial_summary.py` | OpenAI-based summaries |
| `files/uscis_pdfs/` | Downloaded PDFs under `pdfs/<topic>/<year>/`, URL log, and summary TSV |
| `requirements.txt` | Python dependencies |
| `.env` | Local secrets (not committed); see `.env.example` |

## Legal and ethical use

This project automates access to **publicly published** USCIS decision pages. Respect [USCIS terms of use](https://www.uscis.gov/website-policies), avoid aggressive scraping, and ensure your use of downloaded materials and API-generated summaries complies with applicable law and professional rules. API output may be inaccurate; verify against the original PDFs for any important decision.

## License

Not specified in this repository; add a `LICENSE` file if you need one.
