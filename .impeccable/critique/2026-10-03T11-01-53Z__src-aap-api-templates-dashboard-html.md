---
target: dashboard
total_score: 21
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 3
target_identity: "file:C:\\Projects\\AAP-Module\\src\\aap\\api\\templates\\dashboard.html"
target_fingerprint: "sha256:00911c636ee8e984b64cf1e7f0fa81501bb538172a1c0db197da2c0d03ff48d2"
target_path: "C:\\Projects\\AAP-Module\\src\\aap\\api\\templates\\dashboard.html"
timestamp: 2026-10-03T11-01-53Z
slug: src-aap-api-templates-dashboard-html
---
⚠️ DEGRADED: single-context (session only spawns sub-agents on an explicit request; no browser tool, so the review used the dashboard rendered read-only from the new source against live data: 1,067 invoices)

## Design Health Score

| # | Heuristic | Score | Key issue |
|---|---|---|---|
| 1 | Visibility of System Status | 3 | Auto-refresh and upload results are good; a delete gives no confirmation beyond the row vanishing, and nothing says when the figures were last updated. |
| 2 | Match System / Real World | 3 | EBS terms and status codes are used as they are; reason codes such as `LINE_PAIRED_BY_MODEL` are never explained. |
| 3 | User Control and Freedom | 2 | Delete is irreversible with no undo; the only guard is a browser confirm. |
| 4 | Consistency and Standards | 3 | Amounts are `71508359.24 INR` in the table and `191,660,121.12 INR` in the figures; red means "fail" in the design system but is also a Delete on every row. |
| 5 | Error Prevention | 2 | A `CREATED` invoice (already in Oracle) is deleted with the same button and weight as a test upload. |
| 6 | Recognition Rather Than Recall | 2 | 767 of 1,067 records cannot be reached: the table stops at the newest 300 with no search or paging. |
| 7 | Flexibility and Efficiency | 1 | No search, sort, paging, bulk select or keyboard path. Clearing a 1,000-file test batch is 1,000 clicks and 1,000 confirms. |
| 8 | Aesthetic and Minimalist Design | 2 | Several figures say nothing with real data, and they sit above the queue. |
| 9 | Error Recovery | 2 | Upload reports each rejected file clearly; a failed delete request shows nothing at all. |
| 10 | Help and Documentation | 1 | No explanation of statuses, reasons or what a figure counts. |
| **Total** | | **21/40** | **Acceptable** |

## Design Specificity Verdict

**LLM assessment.** The bottom half is this product: the ledger table, EBS codes and provenance are on-brand. The top half is a generic "six tiles, six charts" analytics page that any product could wear unchanged. Nothing in it shows an invoice moving RECEIVED → … → CREATED and where it gets stuck. With live data, 1,061 of 1,067 invoices sit in `IN_REVIEW`, and the page reports that four times (a tile, a bar, a chip, the reasons list) without helping the clerk act on it.

**Deterministic scan.** One real finding on the rendered page: `flat-type-hierarchy` (body 14px, h2 15px, h1 18px; largest step 1.20:1 against a 1.25:1 target). "Upload invoices", "Records" and "By file type" share one 15px heading, so the page's three zones are not distinguishable from its six sub-charts. Six advisory `design-system-color` hits on the templates are false positives (partials scanned without the stylesheet in `base.html`).

**Visual overlays.** None; no browser automation was available.

## Overall Impression

It works and is honest to the design system, but it answers "what are the totals?" when the clerk's question is "what do I do next?". The biggest opportunity is to put the review queue first and cut the analytics down to the few figures that change a decision.

## What's Working

- The records table: dense, tabular, real identifiers, status filter chips with counts.
- Refresh on change: upload and delete update figures and table without a reload; upload reports per file.
- Restraint: neutral bars, no gradients, counts always printed next to the bar.

## Priority Issues

- **[P1] Most records are unreachable.** The table shows the newest 300 of 1,067, with no search, sort or paging. Fix: server-side search (invoice number, supplier, filename) and paging. Command: `/impeccable harden`
- **[P1] The queue is buried under figures that say little.** "By file type" is one bar (`pdf 1067`), "In the pipeline" and "Failed" are zero, "Received per day" is one spike, bars for 2 and 4 next to 1,061 are the same 2px sliver; all of it sits above upload and records. Fix: records first or beside the figures; keep four or five figures; drop one-category charts; make "Need Attention" a link to the filtered queue. Command: `/impeccable distill`
- **[P1] Delete is too easy, too loud and too slow.** 300 red buttons form a red column (red is reserved for failures); posted invoices delete as easily as test files; no bulk delete; a failed delete is silent. Fix: quiet per-row delete, row checkboxes with "Delete selected", a stronger warning for `CREATED`/`SUBMITTED`, an error message on failure. Command: `/impeccable harden`
- **[P2] The 10-second refresh fights the user.** Each tick replaces the 300-row table (about 240 KB) and re-runs the statistics queries; keyboard focus in the table is destroyed; a row can move under the pointer before a Delete click. Fix: refresh on change only, or pause while pointer or focus is in the table; refresh figures less often. Command: `/impeccable optimize`
- **[P2] Flat hierarchy and inconsistent numbers.** Zone headings and chart headings share one size; table amounts lack thousands separators. Fix: one clear step between page zones and sub-sections within the 12–18px band; format every amount the same way. Command: `/impeccable typeset`

## Persona Red Flags

**Alex (power user, the AP clerk):** no search, no sort, no keyboard path to the next `IN_REVIEW` invoice; scrolls past six tiles and six charts every session; cannot bulk-delete a bad batch.

**Sam (keyboard and screen reader):** 300 buttons all announced as "Delete" with no record name; focus lost every 10 seconds; per-day counts only as mouse tooltips; selected filter chip has no `aria-current`.

**Riley (stress tester):** an empty database shows six zero tiles and empty charts before the upload form; a failed delete shows nothing; two tabs can act on rows the other deleted (handled, silently).

## Minor Observations

- Two live invoices still carry the reason `VENDOR_EMAIL_PENDING`, a leftover from the removed email flow.
- "Median upload to review: 10 s" is a pipeline speed figure; it belongs on Admin.
- "Top suppliers" is ordered by invoice count while the amount column mixes currencies.
- The Delete confirm is one long sentence in a native dialog; the Oracle caveat should appear only when it applies.

## Questions to Consider

- Is this page for the clerk (work the queue) or for a supervisor (see how the system is doing)?
- Would a single lifecycle strip (received → in review → approved → in Oracle, with counts) replace four of the six charts?
- Is Delete a clerk action at all, or a testing tool that belongs behind Admin?
