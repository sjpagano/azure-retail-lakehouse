# Data contract and executable quality rules

Contract and transform version: **1.0.0**. Source of truth:
`retail_pipeline/quality.py`; behavioral evidence: `tests/test_quality.py`.

## Input contract

UTF-8 CSV (optional BOM), comma-delimited, with this exact header order:

```csv
order_id,order_date,customer_email,region,sku,quantity,unit_price,currency
```

Each record is one complete order for one SKU. Refunds, negative prices, taxes,
discounts, and multi-line orders are outside the contract. Maximum 10 MiB and
50,000 parsed records; no empty batches. Dates have no timezone component.

| Field | Required rule | Rule code |
|---|---|---|
| All | Present, nonblank, exactly the expected number of fields | `required_fields` |
| order_id | 2–40 uppercase letters, digits, or hyphens | `order_id_format` |
| order_id | Unique within this batch; **all** copies of a duplicate rejected | `unique_order_id` |
| order_date | Valid `YYYY-MM-DD` calendar date | `date_format` |
| order_date | 0–7 days before `as_of`, inclusive; no future dates | `date_freshness` |
| customer_email | Basic nonwhitespace email shape; not mailbox verification | `email_format` |
| region | NA, EU, or APAC | `region_enum` |
| sku | 2–32 uppercase letters, digits, or hyphens | `sku_format` |
| quantity | Decimal digits representing integer 1–10,000 | `quantity_range` |
| unit_price | Positive decimal <=999999.99, at most two decimal places, no exponent | `price_precision_range` |
| currency | USD, EUR, or GBP | `currency_enum` |

Email is trimmed and lowercased before keyed HMAC-SHA256. Other fields are not
silently normalized. Monetary values are parsed using `Decimal` and persisted
as integer cents; floating-point rounding never enters revenue calculations.

## Gate behavior

1. Invalid header/order, encoding, malformed CSV, or excess record count rejects
   the entire batch with `schema_or_encoding`. Row counts remain zero because
   parsing was not trusted; zero does **not** mean the file contained no records.
2. A header-only or empty logical batch rejects with `nonempty_batch`.
3. Otherwise, quarantine invalid records by logical record ordinal (header=1)
   and rule codes. Multiline CSV fields mean this is not a physical file line.
4. Publish accepted rows only when `rejected_rows / input_rows <= 0.05`.
   Exactly 5% passes; more than 5% fails. A failed batch publishes **none** of its
   valid rows. A passed batch can contain quarantined rows, disclosed in its report.
5. The byte limit and missing/short HMAC key are operational errors before
   quality evaluation; the Azure handler returns a generic 500, and ADF fails
   after retries. There may be no quality report in this case.

The 5% limit is an explicit demo policy, not an industry standard. For orders
where losing any record is unacceptable, change it to zero and add boundary
tests. Threshold changes require version review, not an ad hoc retry flag.

`as_of` is a caller-supplied **business date**. The freshness check measures age
relative to that date, not actual ingestion latency. A production scheduler must
control this parameter and monitor arrival-time SLAs separately.

## Outputs and reconciliation

Silver replaces email with `customer_token`, preserves accepted business fields,
and adds `unit_price_cents` and `revenue_cents = quantity * unit_price_cents`.
Gold groups silver by `(order_date, region, currency)` with orders, units,
and revenue cents. Never aggregate different currencies into one money total.

Report invariants tested automatically:

- Accepted + rejected = parsed input rows for a structurally valid batch.
- Rule counts count violations, so their sum can exceed rejected rows.
- Per-currency report totals match sums of silver and gold revenue.
- Clean fixture: USD 4899, EUR 3500, GBP 1500 cents.
- Failed batches have no committed manifest; retries produce the same content.

Reports include input SHA-256, batch ID, contract/transform versions, policy hash,
counts, gate decision, business date, totals, and source/output paths. They do
not echo emails or invalid values. Quarantine contains only record ordinals and
rule codes. An authorized steward joins these references back to restricted
bronze for investigation. Raw source filenames must not contain personal data.

## Acceptance scenarios

`python scripts/demo.py` regenerates checked-in evidence from both fixtures and
asserts publication behavior. `pytest` adds threshold boundaries, every field
rule, UTF-8/BOM handling, byte/row limits, deterministic identity, path traversal,
conflicting writes, HTTP validation, and simulated partial-upload failure.
The Azure adapter is mocked: this suite does not prove cloud RBAC or deployment.
Separate [owner-run Azure evidence](../evidence/azure-deployment.md) documents
the deployed clean, rejected, and retry scenarios and gold/report reconciliation.
It does not replace the wider security and operational checks in the runbook.
