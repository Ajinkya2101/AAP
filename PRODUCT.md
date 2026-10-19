# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: **AP clerks** at a company that runs Oracle E-Business Suite. They sit at a desk on wide or dual monitors and work through a queue of supplier invoices for hours at a time. Their job is to check what the application extracted, fix mapped values (PO line, GL account), and approve or reject each invoice.

Secondary roles (from ARCHITECTURE.md, not built yet): **AP supervisor** (clerk permissions plus value-map approval and the error list) and **administrator** (configuration, reference-data sync, restarting failed jobs). Today the dev build has no login; an "acting as" name goes into the audit trail.

## Product Purpose

Autonomous Accounts Payable ("AAP", shown in the UI as ACSE AP). It takes supplier invoices uploaded by a person (PDF, image, spreadsheet, text, or a saved email file), extracts every field with a local LLM, normalizes and maps the values to Oracle EBS reference data, validates them, runs PO matching and GL coding agents, and posts approved invoices through the Payables Open Interface. Success means more invoices post without a person touching them. When one does need a person, the clerk can see why and fix it fast, and every value, decision and action can be traced.

## Positioning

The whole thing runs locally and keeps data private: models and database stay on the client's own host, with no cloud LLM. It also works natively with Oracle EBS, through EBS-faithful tables, the open interface, holds and reject codes. Every value carries its provenance (page, source value, method), and the deterministic checks and human approval come before anything is posted.

## Operating Context

- Status lifecycle per document: RECEIVED → PARSED → ROUTED → EXTRACTED → NORMALIZED → MAPPED → VALIDATED → IN_REVIEW / ON_HOLD → APPROVED → SUBMITTED → CREATED (or ORACLE_ERROR, REJECTED, FAILED, NOT_INVOICE).
- Clerks work from Oracle EBS habits: exact values, holds (`QTY REC`, `PRICE`), reject codes, PO numbers, GL account strings like `01-510-6420-0000`, and operating units (ACSE US OU in USD, ACSE India OU in INR).
- On the review screen, the original file sits next to the extracted fields, the validation results, the agent decisions, holds, Oracle submissions and the status history.
- The application has no mailbox and sends no email (out of scope since 2026-10-03). When the supplier must fix something, the invoice goes to review with that as the suggested action and the clerk contacts the vendor.

## Capabilities and Constraints

- Stack: FastAPI with server-rendered Jinja2 + HTMX (HTMX is vendored and works offline). The user has confirmed it stays that way. The React UI in ARCHITECTURE.md is not the current plan.
- Current UI surfaces: Dashboard (figures over all processed invoices, manual upload, the records with a status filter and delete), Invoice detail (review, approve/reject, per-line corrections, delete), Admin (job queue, failed documents, reference sync, GL history/embeddings, eval runs).
- Invoices arrive by manual upload only: the dashboard's upload form or `POST /api/documents`. Mailbox integration is not in scope for now, not even as a dummy.
- Backend comes first. The UI stays simple until the backend is finished.
- Oracle EBS is a mock in PostgreSQL for now, behind `ErpReadPort` / `ErpWritePort`.
- Undecided: login, roles and supervisor-specific screens.

## Brand Commitments

- **Simple and minimal. No "AI razzmatazz".** No sparkle icons, gradients, glowing "AI" badges, chat bubbles, animated thinking states or magic language. The fact that an LLM is involved should show up as plain provenance (method: `extracted`, `value_map`, `fuzzy_llm`, `user`), not as spectacle. This is binding and comes from the user.
- The name in the UI today is "ACSE AP".

## Evidence on Hand

- Synthetic invoices and truth sidecars: `test-data/invoices-dev-55`, `test-data/invoices-batch-500`. The generator is `tools/invoicegen`, with 11 layouts.
- Golden scenarios S1–S15, with results in `docs/IMPLEMENTATION-PLAN-STATUS.md`.
- Eval results are stored in `eval_run` / `eval_result`.
- There are no real client invoices, customers, testimonials or client deployments yet. The product is an internal MVP and hasn't been shown to a client. Don't fabricate any of these.

## Product Principles

1. Show the reason, not the magic. Every flag, hold and decision says in plain words why it exists and where the value came from.
2. The clerk's queue comes first. Screens exist to move invoices from review to posted with as few steps as possible.
3. Nothing reaches Oracle without a named person approving it.
4. Speak Oracle EBS: use the ERP's own terms, codes and identifiers, not invented labels.
5. Keep it simple until the backend is done. Choose plain and working over polished and speculative.
