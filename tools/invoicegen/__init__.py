"""Synthetic invoice generator (plan Phase 3).

Builds invoices from the mock EBS data (PO-backed and non-PO) and the golden
scenarios S1-S15, renders them with 11 HTML layouts through WeasyPrint, can add
scan noise (image-only PDF), and writes a ground-truth sidecar
<sha256>.truth.json per file that the stub LLM and the eval harness use.

    python -m tools.invoicegen --count 500 --out /data/invoicegen
"""
