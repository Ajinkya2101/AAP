# Model usage

Which models the pipeline uses, where, and why. Three models in total; everything else is rules.

## At a glance

| Model | Kind | Runs on | Used for |
|---|---|---|---|
| Qwen2.5-VL-7B-Instruct | Vision-language model | GPU, vLLM (`vllm` service, fp8) | Reading the invoice (extraction) |
| Choice model (`CHOICE_MODEL`, by default the same Qwen2.5-VL-7B) | LLM, text only | Same vLLM server | Picking one option from a short list when rules cannot decide |
| BAAI/bge-m3 | Embedding model | CPU, ONNX Runtime | Finding similar past invoice lines for GL coding |

There is no separate OCR engine. Scanned pages go to the vision model as images.

## 1. Extraction: Qwen2.5-VL-7B

- **Where:** pipeline stage 5, `src/aap/extraction/extract.py`.
- **What it does:** takes the page images, plus the PDF text layer when it is good enough, and returns the invoice as structured JSON (header fields and lines).
- **Why a vision model:** invoices arrive as scans and photos as well as digital PDFs. One model handles both, and the layout (tables, totals boxes) carries meaning that plain text loses.
- **Guard rails:** the output must match a JSON schema. Up to 4 page images per request; longer invoices are split into several requests. Values are then checked against the text layer; on image-only pages they are flagged `unverified_image_only`.

## 2. Choices: the "pick one" model

- **Where:** one function, `choose()` in `src/aap/mapping/choose.py`, called from six places.
- **What it does:** code prepares up to 5 candidates. The model answers with one candidate id, or "none" / "review". It cannot invent a value, because the schema only allows those ids.
- **Why:** rules and fuzzy matching settle the clear cases. The model is only the last resort for the ambiguous ones, where meaning matters more than spelling.
- **Every call is logged** in `agent_decision` (candidates, prompt, answer, reason, model).
- **How often it is asked:** `python -m aap.eval.model_use` reports the calls per node.

| Node | Question | Reached when | Result |
|---|---|---|---|
| `map.supplier` | Which supplier sent this invoice? | No alias, tax ID, exact name or email-domain match, and no clear fuzzy winner | Flagged `check_map` |
| `map.site` | Which supplier site matches the address? | Several pay sites and no clear address winner | Flagged `check_map` |
| `map.terms` | Which payment terms mean the same? | No alias, exact name or parsable pattern (net N, discount, EOM, immediate) | Flagged `check_map` |
| `map.category` | Which purchasing category fits this line? | The supplier has no past line with the same text | Flagged `check_map` |
| `agent.pair_lines` | Which PO line is this invoice line? | Best score below 0.80, or less than 0.15 ahead of the next line | Reason `LINE_PAIRED_BY_MODEL` |
| `agent.gl_code` | Which GL account for this non-PO line? | Similar past lines disagree, or similarity is below 0.90 | Always goes to review |

**Which model answers:** `CHOICE_MODEL` defaults to the extraction model, so today the vision model also answers choices. A second server for a text model (Qwen3-8B, compose profile `gpu2`) is defined, but the application has a single `VLLM_BASE_URL` and nothing routes choice requests to that second server.

## 3. Embeddings: bge-m3

- **Where:** `src/aap/embed/` and `src/aap/agents/tools/gl.py`.
- **What it does:** turns invoice line descriptions into vectors, stored in `gl_history_line.embedding` (pgvector). For a non-PO line, the 5 most similar past lines of the same supplier are looked up.
- **Why:** past coding is the best guide to the GL account, and wording varies ("A4 paper 80gsm" vs "copy paper A4"). Embeddings match on meaning, which spelling-based matching misses.
- **How the result is used:** if all 5 agree and similarity is at least 0.90, the account is taken without a model. Otherwise the choice model picks among the accounts found.
- **Upkeep:** a job fills missing embeddings every 5 minutes, and lines a clerk confirms are embedded when they are added to the history.
- **Fallback:** if the model files are missing or `EMBED_ENABLED=false`, RapidFuzz text matching is used instead.

## Not models

- **RapidFuzz and regex:** all the fuzzy scoring and terms parsing before a model is asked.
- **Stub backend (`LLM_BACKEND=stub`):** no model at all. Extraction answers come from ground-truth files and choices take the first candidate. For development and tests without a GPU.

## Settings

All in `.env` (see `.env.example`): model names, `LLM_TEMPERATURE=0` (same answer for the same document), `EXTRACT_MAX_TOKENS=4096`, `CHOICE_MAX_TOKENS=300`, `MATCH_AUTO_ACCEPT`, `MATCH_MARGIN`, `GL_SIM_AUTO`.
