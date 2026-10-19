# Test data

Synthetic invoices from `tools/invoicegen`. All names, numbers and accounts are made up.

| Folder | Contents |
|---|---|
| `invoices-dev-55/` | Golden scenarios S1-S15 + 40 random invoices (seed 7). This is the set delivered to the dev stack. |
| `invoices-batch-500/` | Golden scenarios S1-S15 + 500 random invoices (seed 21), 51 of them scanned (image-only PDFs). |

Each folder has:

- `pdf/`: the invoices (11 layouts, some with scan noise)
- `eml/`: emails (S12 two invoices in one email, S15 unknown sender, S14 auto-reply, plus ~15% of the random invoices)
- `truth/<sha256>.truth.json`: ground truth per PDF: the values exactly as printed, plus the expected outcome
- `manifest.json`: what to deliver (PDF or email) and the expected result of each file

Random invoices depend on the state of the mock EBS when they were generated (open PO lines,
invoice numbers already used), so they match the dev database seeded with `SEED=42`, default scale.

Deliver a folder to the running stack (stub mode reads the truth files from `TRUTH_DIR`, so copy them first):

```bat
docker compose cp test-data\invoices-batch-500 worker:/data/filestore/invoices-batch-500
docker compose exec worker sh -c "cp /data/filestore/invoices-batch-500/truth/* /data/filestore/truth/"
docker compose exec api python -m tools.invoicegen.deliver --dir /data/filestore/invoices-batch-500 --api http://localhost:8080
```
