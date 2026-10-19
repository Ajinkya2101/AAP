# ACSE LLM Application: MVP Technology Stack

| Item | Value |
|---|---|
| Document status | Draft 0.1 |
| Date | 2026-09-23 |
| Language standard | ASD-STE100 Simplified Technical English |
| Related documents | MVP Definition and Goals (MVP.md) |

## 1. Purpose of This Document

This document gives the technology stack for the MVP of the ACSE LLM application. It gives the component, the selected product and the reason for each layer.

The MVP functions come from MVP.md. The references F-xx, P-xx and O-xx in this document refer to items in MVP.md.

## 2. Selection Rules

The team used these rules to select each component:

1. The component must operate on local hardware. No invoice data can go to an external service (P-01).
2. The component must have a license that permits commercial use. Section 14 gives the licenses.
3. The team must be able to replace the Oracle EBS adapter with an Oracle Fusion adapter in Phase 2 (G-04).
4. The MVP must use the smallest number of components that is possible.
5. Application code, not the LLM, must do all calculations and validation (P-03).

## 3. Architecture Overview

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │ Review UI (React + PDF.js)                                           │
 └───────────────┬──────────────────────────────────────────────────────┘
                 │ HTTPS / JSON
 ┌───────────────▼──────────────────────────────────────────────────────┐
 │ API service (FastAPI)                                                │
 └───────────────┬──────────────────────────────────────────────────────┘
                 │ job queue (Procrastinate on PostgreSQL)
 ┌───────────────▼──────────────────────────────────────────────────────┐
 │ Worker                                                               │
 │  1. File type detection                                              │
 │  2. Parsers: PDF / image / Excel / CSV / email                       │
 │  3. Text-layer quality check → input route (F-02)                    │
 │  4. LLM extraction ──────────────► vLLM + Qwen2.5-VL-7B (local GPU)  │
 │  5. Normalization (dates, amounts, currency)                         │
 │  6. Value maps (tables → fuzzy candidates → LLM selection)           │
 │  7. Validation rules V-01 … V-07                                     │
 └───────────────┬──────────────────────────────────────────────────────┘
                 │ after user approval
 ┌───────────────▼──────────────────────────────────────────────────────┐
 │ Integration layer: Oracle adapter (EBS in MVP, Fusion in Phase 2)    │
 └───────────────┬──────────────────────────────────────────────────────┘
                 ▼
          Oracle EBS test instance

 PostgreSQL: application data, value maps, Oracle reference data copy,
             audit log, job queue
 File store:  original invoice files and page images
```

## 4. Hardware and Operating System

| Component | Selection | Reason |
|---|---|---|
| Development and MVP host | Laptop: Intel Core Ultra 9 275HX, 24 cores, 32 GB RAM, NVIDIA RTX 5090 Laptop GPU (24 GB VRAM) | This host has sufficient GPU memory for one 7B vision-language model with FP8 weights. |
| Host operating system | Windows 11 | This is the current operating system. |
| Linux environment | WSL2 with Ubuntu 24.04 LTS | vLLM does not operate on Windows directly. Most GPU libraries operate best on Linux. |
| WSL2 memory limit | 24 GB in `.wslconfig` | The default limit is 50% of RAM (16 GB). That is not sufficient for vLLM and the other services. |
| Container runtime | Docker Desktop with the WSL2 back end and NVIDIA GPU support | One Docker Compose file starts all services. |
| NVIDIA driver | Windows driver only (current: 616.56). Do not install a Linux driver in WSL2. | WSL2 uses the Windows driver. |
| CUDA | 12.8 or later | The RTX 5090 (Blackwell, sm_120) needs CUDA 12.8 or later. |

**Production note:** The laptop is sufficient for the MVP. For continuous production operation, the team must move the services to a server or a desktop GPU. The stack does not change.

## 5. LLM Layer

| Component | Selection | Reason |
|---|---|---|
| Model | **Qwen2.5-VL-7B-Instruct** | It reads text and page images. It has an Apache 2.0 license. It fits in 24 GB VRAM with FP8 weights. |
| Alternative model to evaluate | Qwen3-VL-8B-Instruct | The team can compare it on the test set in Phase 0. The team must examine its license first. |
| Inference server | **vLLM** (OpenAI-compatible API), latest release with sm_120 support. Pin the exact version in Phase 0. | High throughput, FP8 weights and structured output. |
| Weight format | FP8 (dynamic, in vLLM) | It uses approximately half the memory of BF16. The accuracy loss is small. The team must measure it on the test set. |
| Structured output | vLLM structured output with the **xgrammar** back end. The JSON schema comes from the Pydantic models. | The LLM can only make output that agrees with the JSON schema (P-04, M-03). |
| Server settings (start values) | `--max-model-len 16384`, `--gpu-memory-utilization 0.85`, `--limit-mm-per-prompt image=4` | One model uses the GPU. The other memory is for the KV cache and multi-page invoices. |
| Image size limit | `max_pixels` of approximately 1280 × 28 × 28 for each page | This limits each page to approximately 1,280 image tokens. It keeps memory use and time per invoice low. |
| Model files | Stored locally. Downloaded one time from Hugging Face. | After the download, the application does not need internet access (P-01). |

**One model only in the MVP.** The same model does the extraction (F-03) and the value selection (F-04). A second model is not necessary for the MVP. This decreases memory use and the number of components.

**No fine-tuning in the MVP.** The team will fine-tune only if the MVP results show that it is necessary. Section 13 gives the stack for that stage.

## 6. Application Layer

| Component | Selection | Reason |
|---|---|---|
| Language | Python 3.12 | Most document and LLM libraries use Python. |
| Package and environment manager | uv | Fast. It makes a lock file for repeatable builds. |
| API framework | FastAPI | It uses Pydantic models directly. It makes an OpenAPI specification automatically. |
| Data models and JSON schema | **Pydantic v2** | One set of models is the single source of the invoice schema. The same models make the JSON schema for the LLM and examine the output. |
| LLM client | `openai` Python SDK, connected to the local vLLM endpoint | vLLM has an OpenAI-compatible API. No data goes to OpenAI. |
| Job queue | **Procrastinate** (PostgreSQL-based) | A queue without Redis. It uses the PostgreSQL database that the MVP already has. |
| Database access | SQLAlchemy 2 and Alembic (schema migrations) | Standard Python tools for PostgreSQL. |
| Configuration | pydantic-settings, `.env` files | Settings are type-checked. Secrets stay out of the source code. |
| Logs | structlog (JSON logs) | Each log line contains the invoice ID. Log lines are easy to search. |

## 7. Document Intake and Parser Layer

| Input | Selection | Reason |
|---|---|---|
| File type detection | `filetype` (magic bytes) | The application finds the real file type. It does not trust the file extension. |
| PDF text with coordinates | **pdfplumber** | It gives each word with its location on the page. The review screen uses the locations (F-08, P-06). |
| PDF to page image | **pypdfium2**, 200 DPI | Fast and correct page images. It has a permissive license. |
| Images (PNG, JPG, TIFF) | Pillow; OpenCV (headless) for rotation and deskew | Pillow reads multi-page TIFF files. OpenCV corrects rotated scans. |
| Excel (XLSX) | openpyxl, pandas | The application converts each sheet into a Markdown table for the LLM. |
| Excel (XLS, old format) | xlrd | Some suppliers still send XLS files. |
| CSV | pandas, charset-normalizer | charset-normalizer finds the character set before the application reads the file. |
| Email (EML) | Python `email` standard library | It reads the email body and the attachments. |
| Email (MSG, Outlook) | extract-msg | It reads Outlook files. **Examine its license before commercial use** (Section 14). |
| HTML email body | BeautifulSoup, html2text | Converts the email body into plain text for the LLM. |
| Text-layer quality check (F-02) | Application code | Rules: minimum characters for each page, no `(cid:NNN)` glyphs, correct word ratio, text area that covers the page. |
| Layout and table structure (optional) | Docling | The team can compare it with pdfplumber on complex tables. It is not necessary for the first MVP build. |

**Do not use PyMuPDF.** It is a good library, but its license is AGPL or commercial. The AGPL license is a risk for an ACSE product.

**Email files.** The application processes the email body and each attachment. If an email contains more than one invoice, the application sends it to review (MVP.md, Section 11).

## 8. Business Logic Layer

| Function | Selection | Reason |
|---|---|---|
| Amounts | Python `Decimal` | Do not use float numbers for money. Float numbers can cause errors in the last decimal place. |
| Dates | dateparser, with the supplier country as a hint | It reads text dates such as "5 Sept 2026". The application flags dates that the application can read in two ways (for example 03/04/2026). |
| Currency symbols | Value map table (for example `$` → `USD`) and Babel for ISO 4217 codes | One symbol can refer to more than one currency. The default currency of the supplier decides. |
| Value map, step 1 | Exact match in a PostgreSQL table | Deterministic. The functional team controls the tables (D-02). |
| Value map, step 2 | **RapidFuzz** fuzzy match that gives the top 5 candidates | It finds supplier names and terms that are almost the same. Example: "ABC Limited" and "ABC Ltd". |
| Value map, step 3 | LLM selects one candidate or "no match" (structured output) | The LLM can select only from the candidate list. It cannot invent an Oracle value. |
| Validation (F-07) | Python rule module with one function for each rule (V-01 … V-07) | The rules are deterministic and easy to test. The LLM does not do validation (P-03). |

## 9. Integration Layer (Oracle)

The integration layer is a separate Python package with one adapter interface. The MVP has one adapter for Oracle EBS. Phase 2 adds an adapter for Oracle Fusion. The other layers do not change.

| Component | Selection | Reason |
|---|---|---|
| Oracle database driver | **python-oracledb** (thin mode) | Thin mode does not need the Oracle Instant Client. |
| Reference data copy (D-05) | A scheduled job that reads `AP_SUPPLIERS`, `AP_SUPPLIER_SITES_ALL`, `AP_TERMS_TL`, `FND_CURRENCIES` and `HR_OPERATING_UNITS` into PostgreSQL | The application reads the local copy. It does not send a query to Oracle for each invoice. |
| Duplicate check (V-06) | A read-only query on `AP_INVOICES_ALL` (`INVOICE_NUM` and `VENDOR_ID`) at the time of submission | The data must be current at that time. |
| Invoice creation, option A | Insert into `AP_INVOICES_INTERFACE` and `AP_INVOICE_LINES_INTERFACE`. Then start the Payables Open Interface Import program (`APXIIMPT`) with `FND_REQUEST.SUBMIT_REQUEST`. | The standard Oracle method. It uses Oracle validation. |
| Invoice creation, option B | A REST service from Oracle E-Business Suite Integrated SOA Gateway that wraps the Open Interface | The application calls a REST API. It does not write to Oracle tables directly. |
| Result | Read the Open Interface status and `AP_INTERFACE_REJECTIONS` | The review screen shows the Oracle invoice ID or the rejection reason (F-10). |
| Phase 2 adapter | Oracle Fusion REST API, invoices resource | The same JSON output. Only the adapter changes. |

**Decision necessary:** The integration team must select option A or option B (open item O-03).

**Oracle accounts:** Use two Oracle database accounts with minimum permissions:
- A read-only account for the reference data and the duplicate check.
- An account that can only write to the interface tables and start the import program.

## 10. Data Storage Layer

| Component | Selection | Reason |
|---|---|---|
| Database | **PostgreSQL 16** | One database for application data, value maps, the Oracle reference data copy, the audit log and the job queue. |
| JSON outputs | `JSONB` columns | The application can store each version of the JSON output and search it. |
| Audit log (F-10) | An append-only table. Each row: invoice ID, step, input, output, user, time. It also records the LLM prompt and response. | It records each step and each user change. The records cannot be changed. |
| File store | Local file system volume. Each file name is the SHA-256 hash of the file. | Simple. The hash also finds duplicate uploads. |
| Backup | `pg_dump` every day and a copy of the file store volume | Sufficient for the MVP. |

## 11. User Interface Layer

| Component | Selection | Reason |
|---|---|---|
| Framework | React with TypeScript, built with Vite | The review screen needs a custom document viewer. Streamlit cannot do this well. |
| Document viewer | PDF.js (react-pdf) with a box overlay | It shows the source location of each value (F-08, P-06). |
| Source locations | Text-layer route: word coordinates from pdfplumber. Image route: box coordinates from Qwen2.5-VL. | Qwen2.5-VL can give the location of each value on the page image. |
| Components | shadcn/ui and Tailwind CSS | Standard, accessible components. |
| Server data | TanStack Query | It controls API calls, cache and refresh. |
| Forms | React Hook Form with Zod | Zod examines user corrections in the browser. The server also examines them. |
| Authentication | FastAPI with local users, Argon2 passwords and three roles (clerk, supervisor, administrator) | Sufficient for the MVP. Company single sign-on (OIDC) is for a later stage. |
| Web server | Nginx (serves the UI files and sends API calls to FastAPI) | One address for the users. |

## 12. Quality, Test and Operation

| Area | Selection | Reason |
|---|---|---|
| Unit and integration tests | pytest | Standard for Python. |
| Code quality | Ruff (lint and format), mypy, pre-commit | Finds errors before each commit. |
| UI tests | Vitest; Playwright for end-to-end tests | Standard for React. |
| Evaluation harness (Section 13 in MVP.md) | Python scripts with pytest. The results are stored in PostgreSQL. | It measures each field for each input route and each supplier. The team runs it after each change to the model, the prompts or the value maps. |
| Test set labels | Label Studio (Community Edition) | The team uses it to make the correct values for the test set. |
| Test set storage | A separate directory with access control. The directory is not in the Git repository. | The test set contains real supplier data. |
| Source control | Git | Standard. |
| Deployment | Docker Compose. Services: `postgres`, `vllm`, `api`, `worker`, `ui`. | One command starts the full MVP. |
| LLM monitor | vLLM `/metrics` endpoint and the audit log | Sufficient for the MVP. Langfuse is for a later stage. It needs more services and more memory. |

### 12.1 Memory Budget on the MVP Host

| Service | GPU memory | RAM (in WSL2) |
|---|---|---|
| vLLM with Qwen2.5-VL-7B (FP8) | Approximately 20 GB (weights and KV cache) | 6–8 GB |
| PostgreSQL | None | 1 GB |
| API and worker | None | 1–2 GB |
| UI (Nginx) | None | Less than 0.2 GB |
| Label Studio (only when the team makes labels) | None | 1 GB |
| **Total** | **Approximately 20 of 24 GB** | **Approximately 10–12 of 24 GB** |

## 13. Later Stages (Not in the MVP)

| Stage | Components |
|---|---|
| Fine-tuning (only if the MVP evaluation shows that it is necessary) | Unsloth, Hugging Face TRL and PEFT (QLoRA), bitsandbytes, PyTorch 2.7 or later with CUDA 12.8 |
| Agents (purchase order match, supplier emails) | LangGraph with a PostgreSQL checkpoint store |
| Similar-invoice search for GL accounts | pgvector in the same PostgreSQL database, bge-m3 embeddings |
| LLM trace monitor | Langfuse (self-hosted) |
| Structured e-invoices | UBL and Factur-X parsers |
| Company single sign-on | OIDC connection to the company identity provider |

## 14. Licenses

The legal team must confirm these licenses before commercial use.

| Component | License | Commercial use |
|---|---|---|
| Qwen2.5-VL-7B-Instruct | Apache 2.0 | Permitted |
| vLLM, xgrammar | Apache 2.0 | Permitted |
| FastAPI, Pydantic, SQLAlchemy, Procrastinate, RapidFuzz, pdfplumber, Docling | MIT | Permitted |
| pypdfium2 | Apache 2.0 / BSD-3 | Permitted |
| python-oracledb | Apache 2.0 / UPL | Permitted |
| PostgreSQL | PostgreSQL License | Permitted |
| React, Vite, shadcn/ui, TanStack Query | MIT | Permitted |
| Label Studio Community Edition | Apache 2.0 | Permitted |
| extract-msg | Examine before use | Possibly restricted. Replace it if the license is not acceptable. |
| PyMuPDF | AGPL-3.0 or commercial | **Not selected** |

## 15. Open Items

| ID | Open item | Owner |
|---|---|---|
| T-01 | Select the Oracle invoice creation method: option A or option B (same as O-03). | Integration team |
| T-02 | Confirm the licenses in Section 14. | Legal team |
| T-03 | Compare Qwen2.5-VL-7B and Qwen3-VL-8B on the test set. Select one model. | Development team |
| T-04 | Pin the exact versions of vLLM, CUDA and PyTorch after the Phase 0 setup test. | Development team |
| T-05 | Select the production host for the stage after the MVP. | Project sponsor |
