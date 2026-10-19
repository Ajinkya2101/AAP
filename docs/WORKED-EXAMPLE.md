# Worked Example: One Invoice Through the Whole Flow

This file follows real-looking invoices through every step of Diagram 1 (DIAGRAMS.md). At each step it shows:
- the data that goes in and comes out,
- whether a model is used, which one, and exactly how it is called.

All company names, numbers and account codes are made up for illustration.

**Examples:**
- **Example 1** is a PO-backed hardware invoice. It shows extraction, mapping, checks, PO matching and a quantity mismatch.
- **Example 2** is a cloud bill with no PO. It shows jargon mapping and GL account selection.
- **Example 3** is an invoice with a missing PO number. It shows the vendor email loop.
- The last two sections show how models are called in code, and summarise where each model is used.

---

## The Setup

**Models running locally (vLLM, on the laptop GPU):**

| Model | Used for |
|---|---|
| Qwen2.5-VL-7B (vision-language) | Reading invoices (text and page pictures) |
| Qwen3-8B (language) | Choosing between candidates, fuzzy matching, drafting emails. In the MVP, the vision model does this too. |
| bge-m3 (embeddings, CPU) | Turning text into vectors to find "similar" past lines |

**Company data the application can read:**

| Data | Where it comes from (Oracle EBS) |
|---|---|
| Vendors and sites | `AP_SUPPLIERS`, `AP_SUPPLIER_SITES_ALL` |
| Payment terms | `AP_TERMS` (plus `AP_TERMS_LINES` for the number of due days) |
| Purchase orders | `PO_HEADERS_ALL`, `PO_LINES_ALL`, `PO_LINE_LOCATIONS_ALL`, `PO_DISTRIBUTIONS_ALL` |
| Goods receipts | `RCV_TRANSACTIONS` (summarised in `PO_LINE_LOCATIONS_ALL.QUANTITY_RECEIVED`) |
| GL accounts | `GL_CODE_COMBINATIONS` |
| Past invoices and their coding | `AP_INVOICES_ALL`, `AP_INVOICE_LINES_ALL`, `AP_INVOICE_DISTRIBUTIONS_ALL` |

The application keeps a read-only copy of this data in PostgreSQL and refreshes it nightly. It queries Oracle live only for things that must be current, such as receipts and duplicate checks.

---

## Example 1: PO-Backed Hardware Invoice

### The invoice (what the vendor sent)

```
NORTHWIND IT SUPPLIES INC.                         INVOICE
1200 Commerce St, Dallas TX 75201                  Invoice #: NW-INV-20457
Tax ID (EIN): 84-1234567                           Date: 12-Sep-2026
                                                   Your PO#: 4500123
Bill to: ACSE Corp, Pune                           Terms: Net 30 days from date of invoice

#  Description                          Qty   Unit Price     Amount
1  Dell Latitude 5440 14" i7/16GB/512GB  10    $1,150.00  $11,500.00
2  Dell Dock WD19S USB-C 130W            10      $180.00   $1,800.00
3  On-site imaging & setup                1      $500.00     $500.00
                                              Subtotal    $13,800.00
                                              Sales tax 8% $1,104.00
                                              TOTAL DUE   $14,904.00
```

### Step 1: Invoice arrives

- The email arrives from `billing@northwind.example` with one PDF attachment.
- The application saves the PDF and calculates its fingerprint (SHA-256 hash): `9f2c…e41a`.
- It has not seen this hash before, so this is not a re-upload.
- **Model used:** none.

### Step 2: What kind of file?

- The file content says PDF (the extension is not trusted).
- **Text-layer quality check on page 1:**

  | Check | Result |
  |---|---|
  | Characters on page | 1,842 (minimum 200) ✓ |
  | Broken glyphs such as `(cid:123)` | 0 ✓ |
  | Share of real words and numbers | 97% ✓ |
  | Text covers the table area | Yes ✓ |

- **Route:** 3b, text and page picture.
- **Model used:** none. These are plain code checks.

### Step 3b: Read the text and the page

- `pdfplumber` gives every word with its position on the page, for example `"14,904.00"` at box `[480, 612, 540, 624]`.
- `pypdfium2` renders the page as a 200-DPI picture.
- **Model used:** Qwen2.5-VL-7B. It gets the picture and the text together, and must answer in the `SourceInvoice` JSON schema.

**What the model receives (simplified):**

```
SYSTEM: You extract data from supplier invoices. Copy every value exactly as it
appears. Do not convert, calculate or guess. If a value is not on the invoice,
return null.

USER:  [page 1 image]
       Text layer of page 1:
       NORTHWIND IT SUPPLIES INC. ... TOTAL DUE $14,904.00
```

**What the model returns.** vLLM forces this shape, so it cannot return anything else:

```json
{
  "vendor_name": "NORTHWIND IT SUPPLIES INC.",
  "vendor_tax_id": "84-1234567",
  "invoice_number": "NW-INV-20457",
  "invoice_date": "12-Sep-2026",
  "po_number": "4500123",
  "payment_terms": "Net 30 days from date of invoice",
  "currency_symbol": "$",
  "lines": [
    {"description": "Dell Latitude 5440 14\" i7/16GB/512GB", "quantity": "10", "unit_price": "$1,150.00", "amount": "$11,500.00"},
    {"description": "Dell Dock WD19S USB-C 130W", "quantity": "10", "unit_price": "$180.00", "amount": "$1,800.00"},
    {"description": "On-site imaging & setup", "quantity": "1", "unit_price": "$500.00", "amount": "$500.00"}
  ],
  "subtotal": "$13,800.00",
  "tax_amount": "$1,104.00",
  "total": "$14,904.00"
}
```

**Source check (code, no model):**
- Code looks for every returned value in the pdfplumber words, and all 22 values are found.
- Each value gets its page number and box, which the review screen uses to highlight it.
- If the model had returned `"$14,940.00"` (a misread), the check would fail to find it and flag the field `not_found_in_source`.

### Step 4: Translate to company terms

First **normalize** (plain code), then **map** (lookup tables first, a model only if needed).

| Field | Vendor wrote | Normalized (code) | Mapped to Oracle | How |
|---|---|---|---|---|
| Vendor | NORTHWIND IT SUPPLIES INC. | `northwind it supplies` | vendor_id 30418, "NORTHWIND IT SUPPLIES INC" | Alias table hit on the normalized name |
| Vendor site | 1200 Commerce St, Dallas | `1200 commerce st dallas` | Site `DALLAS-HQ` | The vendor has 3 sites. Address fuzzy match scores 96 / 41 / 38, so the top one is clear. |
| Invoice date | 12-Sep-2026 | 2026-09-12 | 2026-09-12 | dateparser; no day/month ambiguity |
| Currency | $ | USD | USD | "$" is ambiguous, so the vendor's default currency (USD) decides |
| Payment terms | Net 30 days from date of invoice | 30 days, from invoice date | `30 NET` | Parser pulls out 30 days, then finds the Oracle term with due days = 30 |
| Amounts | $11,500.00 … | `Decimal("11500.00")` … | same | Strip symbols and separators |
| Invoice type | (not on invoice) | — | `STANDARD` | Fixed configuration value |

**Model used:** none here. Every value matched a table or a parser. Example 2 shows the case where the model is needed.

### Step 5: Automatic checks

All plain code. Each rule returns pass, warning or fail.

| # | Check | Calculation | Result |
|---|---|---|---|
| C1 | Required fields present | vendor, invoice #, date, total, currency, terms all present | ✓ |
| C2 | Line maths | 10 × 1,150.00 = 11,500.00; 10 × 180.00 = 1,800.00; 1 × 500.00 = 500.00 | ✓ |
| C3 | Lines add up to subtotal | 11,500 + 1,800 + 500 = 13,800.00 | ✓ |
| C4 | Tax maths | 13,800.00 × 8% = 1,104.00 | ✓ |
| C5 | Grand total | 13,800.00 + 1,104.00 = 14,904.00 | ✓ |
| C6 | Tax ID format | `NN-NNNNNNN` (US EIN) | ✓ |
| C7 | Tax ID matches the vendor master | Oracle has 84-1234567 for vendor 30418 | ✓ |
| C8 | Vendor active, site active | Both active | ✓ |
| C9 | Invoice date sensible | Not in the future, not older than 1 year | ✓ |
| C10 | Duplicate | No Oracle invoice NW-INV-20457 for vendor 30418; no same-amount, same-date invoice either | ✓ |
| C11 | Bank details unchanged | No bank details on invoice. If present, they must match the vendor master, otherwise a fraud flag. | ✓ |
| C12 | Source check | All values found on the page (step 3b) | ✓ |

**Result:** all pass, so the invoice goes to PO matching.

### Step 6: Match to Purchase Order and Goods Receipt

**6a. Find the PO (code, no model).**

The invoice has a PO number, so it is a direct lookup:

```sql
-- tool: get_po(po_number = '4500123')   read-only, parameterized
SELECT h.segment1 AS po_number, h.vendor_id, h.currency_code, h.closed_code,
       l.line_num, l.item_description, l.unit_price,
       ll.quantity, ll.quantity_received, ll.quantity_billed
FROM   po_headers_all h
JOIN   po_lines_all l            ON l.po_header_id = h.po_header_id
JOIN   po_line_locations_all ll  ON ll.po_line_id  = l.po_line_id
WHERE  h.segment1 = :po_number;
```

**Result:**

| PO line | Description | Unit price | Ordered | Received | Already billed |
|---|---|---|---|---|---|
| 1 | LAPTOP, 14 IN, STANDARD ISSUE | 1,150.00 | 10 | **8** | 0 |
| 2 | DOCKING STATION, USB-C | 180.00 | 10 | 10 | 0 |
| 3 | DEVICE IMAGING SERVICE | 500.00 | 1 | 1 | 0 |
| 4 | LAPTOP, 16 IN, ENGINEERING | 1,150.00 | 5 | 0 | 0 |

**Header checks (code):** the PO vendor is 30418 ✓, the currency is USD ✓, and the PO is open ✓.

**6b. Pair each invoice line with a PO line (the "fuzzy part").**

Code scores every invoice line against every PO line:

```
score = 0.45 × price match           (1 if unit prices are equal, else 0)
      + 0.20 × quantity fits         (1 if billed qty ≤ ordered − already billed)
      + 0.35 × text similarity       (average of RapidFuzz and embedding cosine, 0–1)
```

**Score matrix** (illustrative numbers):

| Invoice line ↓ / PO line → | PO 1 (14 in laptop) | PO 2 (dock) | PO 3 (imaging) | PO 4 (16 in laptop) |
|---|---|---|---|---|
| Inv 1: Dell Latitude 5440 14" … | **0.84** | 0.10 | 0.06 | **0.82** |
| Inv 2: Dell Dock WD19S … | 0.08 | **0.93** | 0.05 | 0.08 |
| Inv 3: On-site imaging & setup | 0.04 | 0.05 | **0.90** | 0.04 |

- The best overall pairing is found with the Hungarian algorithm (`scipy.optimize.linear_sum_assignment`).
- **Auto-accept rule:** best score ≥ 0.80 **and** at least 0.15 ahead of the next option.
  - Inv 2 → PO 2 and Inv 3 → PO 3 are clear.
  - Inv 1 is **not** clear: PO 1 and PO 4 are both laptops at the same price, so the scores are 0.84 vs 0.82.
- **Model used:** Qwen3-8B, only for Inv 1, and only allowed to pick from the two candidates.

```
SYSTEM: Match the invoice line to the purchase order line that describes the
same product. Answer with the PO line number, or "none".

USER:  Invoice line: "Dell Latitude 5440 14" i7/16GB/512GB", qty 10, $1,150.00
       Candidates:
         PO line 1: "LAPTOP, 14 IN, STANDARD ISSUE", qty open 10, $1,150.00
         PO line 4: "LAPTOP, 16 IN, ENGINEERING",    qty open 5,  $1,150.00
```

```json
{"po_line": 1, "reason": "Latitude 5440 is a 14-inch model; the invoice also says 14\". Line 4 is 16-inch."}
```

- The schema only allows `1`, `4` or `"none"`, so the model cannot invent a line.
- The model adds general knowledge (a Latitude 5440 is a 14-inch laptop) that text similarity cannot supply.
- The model's reason is saved and shown on the review screen.

**6c. Three-way comparison (code, no model).**

| Pair | Price: invoice vs PO | Qty billed vs received | Result |
|---|---|---|---|
| Inv 1 ↔ PO 1 | 1,150.00 = 1,150.00 ✓ | **10 billed, 8 received** ✗ | **Quantity mismatch** |
| Inv 2 ↔ PO 2 | 180.00 = 180.00 ✓ | 10 ≤ 10 ✓ | Match |
| Inv 3 ↔ PO 3 | 500.00 = 500.00 ✓ | 1 ≤ 1 ✓ | Match |

- Receipts are queried live from Oracle at this moment, because a receipt might have been entered an hour ago.
- Tolerances (for example, price ±1%) are finance-team settings, not AI decisions.

**Outcome:** line 1 fails, so the invoice goes to **review** (step 9) with this summary. The summary is filled in from a template; no model is needed:

> Line 1 (Dell Latitude 5440): billed 10, received 8 on PO 4500123 line 1. The price matches.
> Suggested action: hold the invoice until the remaining 2 units are received, or ask the vendor for a credit note for 2 units.

### Step 7: Choose the GL account

- **This invoice is PO-backed, so no model is needed.** Each PO line already carries the account the buyer charged, in `PO_DISTRIBUTIONS_ALL`:

  | Line | GL account (from the PO) | Meaning |
  |---|---|---|
  | Laptops | 01-000-1650-0000 | Company 01 / no cost centre / Computer equipment (asset) |
  | Docks | 01-510-6410-0000 | Company 01 / IT dept / Computer accessories expense |
  | Imaging | 01-510-6450-0000 | Company 01 / IT dept / IT services expense |

- In Oracle EBS you don't even send these. When the invoice line references the PO line, Oracle copies the account from the PO.

### Step 9: AP team reviews

The clerk sees:
- the PDF, with the laptop line highlighted,
- the mismatch summary,
- the model's reason for choosing PO line 1.

The clerk chooses **"Hold until received"**, and the application records the decision.

- Three days later, the nightly sync sees `QUANTITY_RECEIVED = 10` on PO line 1.
- The application re-runs step 6c, and everything matches.
- The clerk approves.

### Step 10: Invoice posted

The Oracle adapter turns the result into Open Interface rows:

```
AP_INVOICES_INTERFACE
  INVOICE_NUM = 'NW-INV-20457'   VENDOR_ID = 30418   VENDOR_SITE_CODE = 'DALLAS-HQ'
  INVOICE_DATE = 2026-09-12      INVOICE_AMOUNT = 14904.00   INVOICE_CURRENCY_CODE = 'USD'
  TERMS_NAME = '30 NET'          INVOICE_TYPE_LOOKUP_CODE = 'STANDARD'   SOURCE = '<configured>'

AP_INVOICE_LINES_INTERFACE
  1  ITEM  11500.00  PO_NUMBER='4500123' PO_LINE_NUMBER=1 QUANTITY_INVOICED=10 UNIT_PRICE=1150
  2  ITEM   1800.00  PO_NUMBER='4500123' PO_LINE_NUMBER=2 QUANTITY_INVOICED=10 UNIT_PRICE=180
  3  ITEM    500.00  PO_NUMBER='4500123' PO_LINE_NUMBER=3 QUANTITY_INVOICED=1  UNIT_PRICE=500
  4  TAX    1104.00
```

- The import program runs and Oracle returns invoice ID 882910.
- Every step above is in the audit log.

---

## Example 2: Cloud Bill With No PO (Jargon Mapping and GL Selection)

### The invoice

```
Amazon Web Services, Inc.          Invoice: INV-US-2026-8841327   Date: 01 Sep 2026
Terms: Due upon receipt            Account: 1234-5678-9012

Amazon Elastic Compute Cloud - US East (N. Virginia) - Linux/UNIX On-Demand   $2,340.18
Amazon Simple Storage Service - US East (N. Virginia)                          $412.07
TOTAL                                                                          $2,752.25
```

### Steps 1–3

- These work the same as in Example 1.
- There is no PO number on the invoice. That is expected: this vendor is flagged **"PO not required"** (subscription vendor) in the vendor settings, so the missing PO is not an error.

### Step 4: Translate to company terms

| Field | Vendor wrote | Result | How | Model? |
|---|---|---|---|---|
| Terms | Due upon receipt | `IMMEDIATE` | Value map table: `due upon receipt → IMMEDIATE` | No |
| Line 1 category | Amazon Elastic Compute Cloud - US East … | `IT-CLD-INF-001` (Cloud compute) | See below | Maybe |
| Line 2 category | Amazon Simple Storage Service - US East | `IT-CLD-STO-002` (Cloud storage) | See below | Maybe |

**How a line description becomes an internal code:**

1. **Exact table:** has this vendor used this exact description before? For line 1, yes: 14 past invoices contain the same text, all coded `IT-CLD-INF-001`. Take it, no model needed.
2. **Similarity search:** if the text is new, turn it into a vector with bge-m3 and find the 5 most similar past lines (pgvector). If all 5 share one code with similarity ≥ 0.90, take it, no model needed.
3. **Model decides:** if the top results disagree, Qwen3-8B chooses from **only** the codes found, plus their catalogue descriptions.

### Step 7: Choose the GL account (non-PO, so a real decision is needed)

**What is a GL account?**
- It is the "bucket" in the company's accounts where a cost is recorded. Financial statements, budgets and tax reports are all built from these buckets.
- In Oracle it is a combination of segments, for example `01-510-6420-0000`:
  - `01` is the company,
  - `510` is the cost centre (the IT department),
  - `6420` is the natural account (Cloud infrastructure expense),
  - `0000` is the sub-account.

**Line 1 (EC2 compute), decided without a model:**
- The vendor default rule says AWS goes to cost centre 510.
- The history search finds 5 of 5 similar past lines coded to account 6420, with similarity 0.91–0.97.
- That meets the auto-accept rule, so the line is coded `01-510-6420-0000` with high confidence.

**Line 2 (S3 storage), decided with a model.** The history search finds mixed answers:

| Past line (same vendor) | Similarity | Coded to |
|---|---|---|
| Amazon Simple Storage Service - US East | 0.96 | 6430 Data storage |
| Amazon S3 - Standard storage | 0.93 | 6430 Data storage |
| Amazon Simple Storage Service - EU (Ireland) | 0.92 | 6420 Cloud infrastructure |
| Amazon S3 Glacier | 0.88 | 6430 Data storage |
| AWS Data Transfer - US East | 0.81 | 6420 Cloud infrastructure |

**Model used:** Qwen3-8B, which may only answer `6420`, `6430` or `"review"`:

```
SYSTEM: Choose the GL natural account for this invoice line. Use the past
decisions and the account descriptions. Answer only with one of the allowed
accounts, or "review" if unsure.

USER:  Line: "Amazon Simple Storage Service - US East (N. Virginia)", $412.07
       Accounts: 6420 = Cloud compute and networking; 6430 = Data storage and backup
       Past decisions: [the 5 rows above]
```

```json
{"account": "6430", "confidence": "medium", "reason": "S3 is object storage; 3 of 4 storage lines went to 6430. The one 6420 line was an EU storage line, likely a past miscoding."}
```

**Afterwards (code, no model):**
- The result is `01-510-6430-0000`.
- Code checks that the combination exists and is enabled in `GL_CODE_COMBINATIONS` ✓.
- Because the model decided and the confidence is medium, the line goes to review.
- When the clerk confirms, the decision is added to the history, so the next S3 line is coded without a model.

---

## Example 3: Missing PO Number (Vendor Email Loop)

A hardware invoice arrives from Northwind with **no PO number**. Northwind is a "PO required" vendor.

**Step 6a, fallback search (code):** search Northwind's open POs where:
- the currency is the same,
- the open amount is at least the invoice amount,
- and a unit price matches at least one invoice line.

There are two possible outcomes:

| Outcome | What happens |
|---|---|
| Exactly one PO fits | Suggest it on the review screen: "PO 4500123 likely. All 3 unit prices match." |
| None or several fit | Go to step 8, ask the vendor |

**Step 8, draft the email.**

**Model used:** Qwen3-8B, in free-text mode. The facts come from the data; the model only writes the wording.

```
To: billing@northwind.example
Subject: RE: Invoice NW-INV-20511 – PO number needed

Hello Northwind team,

Thank you for invoice NW-INV-20511 dated 20-Sep-2026 for $3,600.00.
We cannot process it yet because it does not show our purchase order number.
Could you reply with the PO number for this order, or send a corrected invoice?

Kind regards,
ACSE Accounts Payable
```

- A person approves the email before it is sent.
- The application stores the email's Message-ID, so it can link the reply to this invoice.

**Vendor reply:** "Sorry about that, it's PO 4500187."

**Model used:** Qwen2.5-VL-7B, with a small schema of one field, `{"po_number": string | null}`. The reply text goes in, and `{"po_number": "4500187"}` comes out.

- The source check confirms that "4500187" is in the reply text.
- The invoice goes back to step 6 with the PO number filled in.

---

## How a Model Call Looks in Code

Every model call in this application uses one pattern: **the input, a list of allowed answers or a schema, and a structured output.**

```python
import json
from openai import OpenAI

llm = OpenAI(base_url="http://vllm:8000/v1", api_key="local")  # local vLLM, not OpenAI

def ask(model: str, system: str, content: list, schema: dict) -> dict:
    resp = llm.chat.completions.create(
        model=model,
        temperature=0,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": content}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "answer", "schema": schema}},
    )
    return json.loads(resp.choices[0].message.content)

# Extraction (step 3): picture + text in, SourceInvoice out
ask("qwen2.5-vl-7b", EXTRACT_PROMPT,
    [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{page_png}"}},
     {"type": "text", "text": page_text}],
    SourceInvoice.model_json_schema())

# Bounded choice (steps 4, 6b, 7): the schema is an enum of the candidates
ask("qwen3-8b", MATCH_PROMPT, [{"type": "text", "text": line_and_candidates}],
    {"type": "object",
     "properties": {"po_line": {"enum": [1, 4, "none"]}, "reason": {"type": "string"}},
     "required": ["po_line", "reason"]})
```

- The **enum** is what makes a choice safe. vLLM can only generate one of the listed values.
- A **fine-tuned model** uses the same call. Only the `model` name changes to the LoRA adapter name.

---

## Summary: Where Each Model Is Used

| Step | Model | Job | Output | If unsure |
|---|---|---|---|---|
| 3b / 3c Read invoice | Qwen2.5-VL-7B | Copy values from the page | `SourceInvoice` JSON (schema-forced) | Source check flags values not found on the page |
| 4 Translate terms | Qwen3-8B (fallback only) | Pick from fuzzy-match candidates | One candidate or "none" | "none" goes to review |
| 4 Line item codes | bge-m3, then Qwen3-8B if history disagrees | Find similar past lines, then choose | Internal item code | Review |
| 5 Checks | **None** | — | — | — |
| 6a Find PO | **None** (SQL); suggestion only when the number is missing | — | — | Vendor email |
| 6b Line pairing | Qwen3-8B, only for ambiguous pairs | Pick the PO line | PO line number or "none" | Review |
| 6c 3-way compare | **None** | — | — | — |
| 7 GL account (non-PO) | bge-m3, then Qwen3-8B if history disagrees | Choose an account | Allowed account or "review" | Review |
| 8 Vendor email | Qwen3-8B (writing); Qwen2.5-VL-7B (reading the reply) | Draft the email; extract the answer | Draft for approval; small JSON | A person edits the draft |

**The pattern:** code does lookups, maths and rules. A model is used only when a decision needs reading, language or general knowledge. Even then, it chooses from a list that code has prepared, and a person reviews anything uncertain.


Something to consider:
| Function              | Document              | What I'd test first             |
| --------------------- | --------------------- | ------------------------------- |
| Invoice understanding | Qwen2.5-VL-7B         | **Qwen3-VL-30B-A3B-Instruct**   |
| Ambiguous matching    | Qwen3-8B              | **Qwen3-30B-A3B**               |
| Embeddings            | bge-m3                | **bge-m3 initially**            |
| OCR/text extraction   | pdfplumber etc.       | **Keep deterministic pipeline** |
| Arithmetic/validation | Python                | **Keep Python**                 |
| PO/GL retrieval       | PostgreSQL + pgvector | **Keep retrieval**              |
