"""Fail-closed CSV validation. No raw values are included in rejection reports."""

import csv
import hashlib
import hmac
import io
import json
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

VERSION = "1.0.0"
FIELDS = [
    "order_id",
    "order_date",
    "customer_email",
    "region",
    "sku",
    "quantity",
    "unit_price",
    "currency",
]
MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000
POLICY = {
    "version": VERSION,
    "fields": FIELDS,
    "max_invalid_ratio": "0.05",
    "max_age_days": 7,
    "currencies": ["USD", "EUR", "GBP"],
    "regions": ["NA", "EU", "APAC"],
}


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def json_lines(rows: list[dict]) -> bytes:
    return b"".join(json_bytes(row) for row in rows)


def evaluate(raw: bytes, as_of: date, key: str) -> dict:
    if len(key) < 32:
        raise ValueError("Pseudonym key must contain at least 32 characters")
    if len(raw) > MAX_BYTES:
        raise ValueError("Batch exceeds the 10 MiB limit")
    digest = hashlib.sha256(raw).hexdigest()
    key_fingerprint = hmac.new(key.encode(), b"retail-key-version", hashlib.sha256).hexdigest()
    identity = {
        "input_sha256": digest,
        "as_of": as_of.isoformat(),
        "policy": POLICY,
        "transform_version": VERSION,
        "key_fingerprint": key_fingerprint,
    }
    batch_id = hashlib.sha256(json_bytes(identity)).hexdigest()
    report = {
        "batch_id": batch_id,
        "input_sha256": digest,
        "as_of": as_of.isoformat(),
        "contract_version": VERSION,
        "transform_version": VERSION,
        "policy_sha256": hashlib.sha256(json_bytes(POLICY)).hexdigest(),
        "input_rows": 0,
        "accepted_rows": 0,
        "rejected_rows": 0,
        "status": "rejected",
        "rule_counts": {},
        "reconciliation_cents": {},
    }
    accepted, rejected = [], []
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        if reader.fieldnames != FIELDS:
            raise ValueError("schema")
        rows = []
        for row in reader:
            if len(rows) >= MAX_ROWS:
                raise ValueError("row_limit")
            rows.append(row)
    except (ValueError, UnicodeDecodeError, csv.Error):
        report["rule_counts"] = {"schema_or_encoding": 1}
        return {"report": report, "silver": [], "gold": [], "quarantine": []}
    report["input_rows"] = len(rows)
    if not rows:
        report["rule_counts"] = {"nonempty_batch": 1}
        return {"report": report, "silver": [], "gold": [], "quarantine": []}
    counts = Counter(row.get("order_id", "") for row in rows)
    for row_number, row in enumerate(rows, start=2):
        failures = []
        if None in row or any(row.get(field) is None or not row[field].strip() for field in FIELDS):
            failures.append("required_fields")
        if not re.fullmatch(r"[A-Z0-9-]{2,40}", row.get("order_id") or ""):
            failures.append("order_id_format")
        if counts[row.get("order_id", "")] > 1:
            failures.append("unique_order_id")
        if not re.fullmatch(r"[A-Z0-9-]{2,32}", row.get("sku") or ""):
            failures.append("sku_format")
        email = (row.get("customer_email") or "").strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            failures.append("email_format")
        if row.get("region") not in POLICY["regions"]:
            failures.append("region_enum")
        if row.get("currency") not in POLICY["currencies"]:
            failures.append("currency_enum")
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row.get("order_date") or ""):
                raise ValueError
            age = (as_of - date.fromisoformat(row["order_date"])).days
            if age < 0 or age > POLICY["max_age_days"]:
                failures.append("date_freshness")
        except (ValueError, TypeError):
            failures.append("date_format")
        quantity = 0
        if not re.fullmatch(r"[0-9]{1,5}", row.get("quantity") or ""):
            failures.append("quantity_range")
        else:
            quantity = int(row["quantity"])
            if not 1 <= quantity <= 10_000:
                failures.append("quantity_range")
        cents = 0
        try:
            value = row.get("unit_price") or ""
            if not re.fullmatch(r"[0-9]{1,6}(\.[0-9]{1,2})?", value):
                raise ValueError
            price = Decimal(value)
            if not Decimal("0") < price <= Decimal("999999.99"):
                raise ValueError
            cents = int(price * 100)
        except (ValueError, InvalidOperation):
            failures.append("price_precision_range")
        if failures:
            rejected.append({"row_number": row_number, "rules": sorted(set(failures))})
        else:
            accepted.append(
                {
                    "order_id": row["order_id"],
                    "order_date": row["order_date"],
                    "customer_token": hmac.new(
                        key.encode(), email.encode(), hashlib.sha256
                    ).hexdigest(),
                    "region": row["region"],
                    "sku": row["sku"],
                    "quantity": quantity,
                    "unit_price_cents": cents,
                    "revenue_cents": quantity * cents,
                    "currency": row["currency"],
                }
            )
    report["accepted_rows"] = len(accepted)
    report["rejected_rows"] = len(rejected)
    report["rule_counts"] = dict(
        sorted(Counter(rule for row in rejected for rule in row["rules"]).items())
    )
    report["invalid_ratio"] = str(Decimal(len(rejected)) / Decimal(len(rows)))
    report["status"] = (
        "passed"
        if Decimal(len(rejected)) / Decimal(len(rows)) <= Decimal(POLICY["max_invalid_ratio"])
        else "rejected"
    )
    groups = {}
    totals = defaultdict(int)
    for row in accepted:
        group_key = (row["order_date"], row["region"], row["currency"])
        group = groups.setdefault(
            group_key,
            {
                "order_date": group_key[0],
                "region": group_key[1],
                "currency": group_key[2],
                "orders": 0,
                "units": 0,
                "revenue_cents": 0,
            },
        )
        group["orders"] += 1
        group["units"] += row["quantity"]
        group["revenue_cents"] += row["revenue_cents"]
        totals[row["currency"]] += row["revenue_cents"]
    report["reconciliation_cents"] = dict(sorted(totals.items()))
    return {
        "report": report,
        "silver": sorted(accepted, key=lambda row: row["order_id"]),
        "gold": [groups[k] for k in sorted(groups)],
        "quarantine": rejected,
    }
