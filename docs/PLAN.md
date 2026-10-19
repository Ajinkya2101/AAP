# Autonomous AP Multi-Agent System — Implementation Plan

Local-only, privacy-preserving invoice resolution: extract → normalize → validate → 3-way match → GL route → exception handling → post.

## 1. Hardware & Constraints

| Component | Spec | Implication |
|---|---|---|
| GPU | RTX 5090 Laptop, **24 GB VRAM**, Blackwell (sm_120) | QLoRA of 7–8B models (incl. VLMs) is comfortable. 32B+ training is out; 32B inference only alone and with a tiny KV cache. |
| RAM | **32 GB** | The real bottleneck. Don't train and serve concurrently. Cap WSL2 at ~24 GB. |
| CPU | Core Ultra 9 275HX, 24 cores | Plenty for PDF rendering, CPU embeddings, Postgres, agents. |
| Disk | ~800 GB free | Budget 150–250 GB for models, checkpoints, datasets. |
| OS | Windows 11 | **Run the ML stack in WSL2 Ubuntu** — vLLM has no native Windows support; bitsandbytes/flash-attn are fragile there. |

**Blackwell pitfalls:** CUDA 12.8+ and PyTorch ≥ 2.7 (cu128) are required, otherwise "no kernel image for sm_120". Use recent vLLM and bitsandbytes. Prefer SDPA attention over flash-attn 2.

**Laptop thermals:** train plugged in, max-performance power mode, cooling pad; checkpoint every 100–200 steps.

## 2. Model Stack

| Role | Model | Training | Serving |
|---|---|---|---|
| Invoice image → verbatim JSON | **Qwen2.5-VL-7B-Instruct** (evaluate Qwen3-VL-8B as drop-in) | Optional QLoRA via Unsloth (~12–18 GB) | vLLM, FP8 (~9 GB) |
| Taxonomy mapping + agent reasoning | **Qwen3-8B** | Optional QLoRA (~10 GB) | vLLM FP8 + multi-LoRA (~9 GB) |
| Embeddings (GL RAG, code candidates) | bge-m3 | none | CPU |

Run both vLLM instances concurrently with `--gpu-memory-utilization 0.45` and `--max-model-len 8192` each (~2–3 GB KV cache each). 10k invoices/day ≈ 7/min — well within capacity.

**Design decisions**
- **Two-stage extraction.** The VLM extracts *verbatim* values (`"AWS EC2 US-East"`, `"7 Days"`); a separate mapper step converts them to proprietary codes (`IT-CLD-INF-001`, `NET7`) using RAG-retrieved candidates. Each stage is evaluated independently, and new GL codes only need an index update, not a retrain.
- **JSON validity comes from constrained decoding**, not fine-tuning: vLLM structured outputs (xgrammar) driven by the Pydantic schema. Syntax errors become impossible; fine-tuning (if done) only improves value correctness.

## 3. Multi-Format Input Routing

Not every invoice needs vision. Route each file to the most reliable extractor available, cheapest and most deterministic first:

| Tier | Input | Extractor | Reliability |
|---|---|---|---|
| **A. Structured** | UBL / Peppol XML, Factur-X / ZUGFeRD (XML embedded in PDF), e-invoice JSON, GST e-invoice signed QR code (if applicable) | **Deterministic parser — no LLM** | Exact |
| **B. Born-digital** | PDFs with a good text layer, DOCX, XLSX/CSV, HTML email bodies | Layout-aware text (Docling or PyMuPDF/pdfplumber with word coordinates, tables kept as markdown/rows) → LLM. Default: **text + page image together** to the VLM; evaluate text-only to Qwen3-8B as an alternative. | High — exact characters |
| **C. Scanned / image** | Scans, phone photos, image-only PDFs, PDFs whose text layer fails QA | VLM on page images | Lowest — this is where fine-tuning pays off |

**Routing is per page, not per file.** A PDF can have a typed header page and a scanned annexure.

**Text-layer QA (decides B vs C):**
- characters per page above a floor
- no `(cid:NNN)` glyphs or replacement characters
- mostly valid tokens (dictionary/number ratio)
- text covers the page area, not just the header
- a cheap OCR sample agrees with the text layer

**Cross-checks before auto-posting:**
- For every tier, key fields (total, tax ID, invoice number, bank details) must appear in the source. For tier B, also compare them against what the page image shows. A hidden text layer that disagrees with the visible page is a known fraud pattern, so any mismatch goes to review with a fraud flag.
- For tier A, still render the visual copy (if any) and confirm totals match, then run the same validation and matching as other tiers.

**Why this matters for fine-tuning:** fine-tuning decisions are made **per tier**. If most volume is tier A/B, zero-shot is likely good enough overall, and tier C invoices can go to human review instead of justifying a VLM fine-tune. Measure the volume mix first.

## 4. Is Fine-Tuning Actually Needed?

**Short answer: probably for one of the three use cases, maybe for a second, and not for the third. Don't commit up front — build the eval harness first and let the numbers decide.**

| Use case | Verdict | Reasoning |
|---|---|---|
| **#2 JSON schema adherence** | **Not needed** | Constrained decoding guarantees valid, complete-structure JSON on every call. "Zero syntax errors" is solved at the decoder, deterministically. Fine-tuning here only saves some prompt tokens — irrelevant at 7 invoices/min. |
| **#1 Taxonomy mapping** (jargon → internal codes) | **Probably not at first** | RAG + few-shot handles this well: retrieve this vendor's past postings and the top-k candidate codes, and let the model choose. It adapts instantly when codes change, and every decision is explainable ("mapped like invoice #4411"). Fine-tune only if accuracy plateaus on ambiguous items *and* you have ~5k+ historical postings. |
| **#3 VLM extraction** | **Most likely needed** | This is where a 7B model is weakest zero-shot: dense or multi-page line-item tables, poor scans, and pairing quantity/price/tax with the right row. Header fields (vendor, date, total) are usually fine zero-shot; line items are usually not good enough for autonomous posting. |

**Why hardware matters here:** the usual alternative to fine-tuning is "use a bigger model". On 24 GB, a 32B VLM only fits 4-bit with almost no KV cache, and can't run next to the mapper/agent model. Fine-tuning the 7B is how you get closer to big-model accuracy on this machine. On a 48–80 GB server, the answer might be "just run the 32B zero-shot".

**Never fine-tune the agent model.** Agent quality comes from good tools, deterministic checks, and prompts. Fine-tuning tool-use behavior is expensive and brittle.

### Decision gates (after Phase 3 baseline)

Measure the zero-shot pipeline on the held-out gold set. Thresholds below are placeholders — agree on real ones with finance.

| Metric | Skip fine-tuning if… | Otherwise |
|---|---|---|
| Header field accuracy (vendor, invoice #, date, total, tax) | ≥ 97% | Phase 4 |
| Line-item F1 (description + qty + unit price + amount) | ≥ 95% | Phase 4 |
| Item → internal code accuracy (RAG + few-shot) | ≥ 95%, or errors are caught in the review queue at an acceptable rate | Phase 5 |
| Invoices auto-posted without human touch | meets the business target | Investigate which stage is failing |

Also count **cost of error**: a wrong total that gets through is far worse than one sent to review. Confidence thresholds + the human review queue can make a zero-shot system safe; fine-tuning then just raises the auto-post rate.

## 5. Architecture

```
Email/Upload → Ingest (dedupe hash, format detection, per-page text-layer QA)
   → Router
        ├─ A: Structured (XML / embedded XML / QR) → deterministic parser
        ├─ B: Born-digital → layout text (+ page image) → LLM, schema-constrained
        └─ C: Scanned      → [VLM] page images, schema-constrained
   → Merge per-page results → verbatim JSON + source tier + per-field provenance
   → Text-vs-image cross-check (tier B)
   → [Mapper + RAG] Normalize: payment terms, item codes, vendor ID
   → Deterministic validator (math, tax ID format, required fields)
   → LangGraph orchestrator
        ├─ Matching Agent   ── tools: get_po, get_receipts, compare_lines
        ├─ GL Routing Agent ── tools: search_similar_lines (pgvector), get_gl_catalog
        └─ Exception Agent  ── tools: draft_email, send_email, read_thread
              ↳ interrupt() → waits for vendor reply (checkpointed in Postgres)
   → Human review queue (low confidence / policy)
   → Post to finance DB
```

**Stack:** LangGraph (agents, durable checkpoints, `interrupt()`), FastAPI, Postgres + pgvector (mock ERP, vector store, agent state), Mailpit (local SMTP/IMAP for dev), Label Studio, Streamlit/React review UI, Langfuse (self-hosted tracing), Docker Desktop on the WSL2 backend.

**Agent principles**
- **Arithmetic and 3-way-match comparisons are deterministic code, never the LLM.** The agent handles the fuzzy parts: aligning "Dell Latitude 5440" with PO line "Laptop 14in", finding the PO when the number is missing, and deciding what to do about a discrepancy.
- **Parameterized tools, not free-form text-to-SQL.** Read-only views plus typed functions — safer and testable.
- **Human approval before vendor emails are sent**, until draft quality is trusted. Track threads by `Message-ID` / `In-Reply-To`.
- **Closed loop:** human corrections from the review queue feed the RAG index immediately and the fine-tuning dataset (if/when used).

## 6. Data Strategy (the biggest risk)

1. **Historical invoices + their ERP postings** — the posted record *is* the label. Mine it; don't hand-label it.
2. **Synthetic generator** — Jinja/HTML templates + Faker → PDF → image, with scan noise, rotation, and odd table layouts. Perfect labels for free; target 5–20k.
3. **Public datasets** for layout robustness: DocILE (invoices), CORD, SROIE.
4. **Held-out real gold set** of 300–500 hand-verified invoices, never trained on. Every decision, including whether to fine-tune, is judged against it.

## 7. Phases (single developer)

| # | Phase | Deliverable | Est. |
|---|---|---|---|
| 0 | Environment | WSL2 (`.wslconfig` `memory=24GB`), CUDA 12.8, PyTorch cu128, vLLM, Unsloth, Docker Compose (Postgres, Mailpit, Langfuse). Smoke test: serve Qwen2.5-VL, extract one invoice. | 3–4 days |
| 1 | Schema + mock ERP | Pydantic invoice schema (line items → taxes → discounts); ERP tables (vendors, POs, PO lines, goods receipts, GL catalog, posted-line history); seed data | 1 week |
| 2 | Data | Synthetic generator, public-dataset converters, historical ingest, gold set | 1.5–2 weeks |
| 2b | Input router | Format detection, tier A parsers (UBL / Factur-X / QR), layout-aware text extraction, per-page text-layer QA, text-vs-image cross-check | 1 week |
| 3 | **Baseline + eval harness** | Zero-shot pipeline on every tier + constrained decoding + RAG mapper. Metrics **reported per tier**: per-field F1, total exact match, line-item F1, code accuracy, schema validity. For tier B, compare text-only, image-only and text+image. **Run the decision gates per tier.** | 1 week |
| 4 | *(Gated)* VLM fine-tune | Unsloth QLoRA (r=16–32, 4-bit base, `max_pixels` capped at ~1–1.5k visual tokens/page), merge, FP8 serve, A/B against baseline | 2 weeks |
| 5 | *(Gated)* Mapper fine-tune | Qwen3-8B LoRA on (verbatim text + candidate codes → code), payment-term normalization | 1–1.5 weeks |
| 6 | Matching + GL agents | LangGraph graph, tools, tolerance rules (price ±x%, qty), confidence thresholds, review queue | 2 weeks |
| 7 | Exception/comms agent | Email ingest, drafting, approval gate, waiting on replies, re-extraction from replies | 2 weeks |
| 8 | Hardening | Replay 10k synthetic invoices end-to-end, throughput and failure-mode analysis, dashboards, audit log of every agent decision | 1.5 weeks |

**Timeline:** ~10–11 weeks without fine-tuning (including the router); ~12–14 weeks with both gated phases. Phases 6–7 don't depend on 4–5 and can proceed on the zero-shot baseline, with fine-tuned adapters swapped in later.

## 8. Repo Layout

```
aap/
  schemas/        # Pydantic models — single source of truth (also drives guided decoding)
  ingest/         # PDF→image, email intake, dedupe
    router/       # format detection, text-layer QA, tier A parsers, layout text
  extraction/     # VLM client, prompts
  normalize/      # mapper client, RAG candidates
  validation/     # deterministic math/tax/field checks
  agents/         # langgraph graph, tools/, matching.py, gl.py, exceptions.py
  erp/            # SQLAlchemy models, migrations, seed
  training/       # datagen/, unsloth VLM + mapper scripts, eval/
  serving/        # vLLM launch configs
  ui/             # review queue
  docker-compose.yml
```

## 9. Open Questions

- **What is the volume mix across tiers A / B / C?** If scans are a small share, a VLM fine-tune may not be worth it. Sample a few hundred recent invoices and classify them.

- **Do historical invoices + ERP postings exist, and how many?** This decides whether Phase 2 is mostly mining or mostly synthetic generation, and whether Phase 5 is viable.
- What are the match tolerances and approval policies? These are finance rules, not ML problems — settle them early.
- Production hosting: the laptop is fine for dev and pilot. 24/7 production should move to a desktop or server GPU; the architecture carries over unchanged.
