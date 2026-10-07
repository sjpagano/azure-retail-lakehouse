import csv
import io
import json
from datetime import date
from pathlib import Path

import pytest

from retail_pipeline.quality import FIELDS, MAX_BYTES, evaluate

KEY = "synthetic-test-key-never-use-in-production"
AS_OF = date(2026, 9, 30)
GOOD = {
    "order_id": "ORD-01",
    "order_date": "2026-09-30",
    "customer_email": "Alex@example.test",
    "region": "NA",
    "sku": "BOOK-01",
    "quantity": "2",
    "unit_price": "0.10",
    "currency": "USD",
}


def csv_bytes(rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def test_pseudonym_and_decimal_revenue():
    result = evaluate(csv_bytes([GOOD]), AS_OF, KEY)
    assert result["report"]["status"] == "passed"
    assert result["silver"][0]["revenue_cents"] == 20
    assert result["gold"][0]["revenue_cents"] == 20
    assert len(result["silver"][0]["customer_token"]) == 64
    assert "example.test" not in json.dumps(result)
    assert "customer_email" not in json.dumps(result)


@pytest.mark.parametrize(
    ("field", "value", "rule"),
    [
        ("order_id", "", "required_fields"),
        ("order_id", "bad!", "order_id_format"),
        ("sku", "x", "sku_format"),
        ("customer_email", "bad", "email_format"),
        ("region", "XX", "region_enum"),
        ("currency", "BTC", "currency_enum"),
        ("order_date", "2026-10-01", "date_freshness"),
        ("order_date", "2026-09-01", "date_freshness"),
        ("order_date", "2026-02-30", "date_format"),
        ("order_date", "20260930", "date_format"),
        ("quantity", "-1", "quantity_range"),
        ("quantity", "0", "quantity_range"),
        ("quantity", "10001", "quantity_range"),
        ("quantity", "1.5", "quantity_range"),
        ("unit_price", "NaN", "price_precision_range"),
        ("unit_price", "Infinity", "price_precision_range"),
        ("unit_price", "1.001", "price_precision_range"),
        ("unit_price", "0", "price_precision_range"),
        ("unit_price", "-1", "price_precision_range"),
        ("unit_price", "1e2", "price_precision_range"),
    ],
)
def test_invalid_fields_are_quarantined(field, value, rule):
    result = evaluate(csv_bytes([{**GOOD, field: value}]), AS_OF, KEY)
    assert result["report"]["status"] == "rejected"
    assert result["report"]["rule_counts"][rule] == 1
    assert result["quarantine"][0]["row_number"] == 2
    assert not result["silver"]


def test_every_duplicate_is_rejected():
    result = evaluate(csv_bytes([GOOD, GOOD]), AS_OF, KEY)
    assert result["report"]["rule_counts"]["unique_order_id"] == 2
    assert result["report"]["accepted_rows"] == 0


def test_quality_threshold_boundary():
    rows = [{**GOOD, "order_id": f"ORD-{i}"} for i in range(20)]
    rows[0]["quantity"] = "0"
    assert evaluate(csv_bytes(rows), AS_OF, KEY)["report"]["status"] == "passed"
    assert evaluate(csv_bytes(rows[:19]), AS_OF, KEY)["report"]["status"] == "rejected"


@pytest.mark.parametrize(
    "raw", [b"", b"unexpected,columns\n1,2\n", b"\xff\xfe", b'a,b\n"unterminated']
)
def test_schema_and_encoding_fail_closed(raw):
    result = evaluate(raw, AS_OF, KEY)
    assert result["report"]["status"] == "rejected"
    assert result["report"]["rule_counts"] == {"schema_or_encoding": 1}


def test_empty_batch_and_ragged_rows():
    assert evaluate(csv_bytes([]), AS_OF, KEY)["report"]["rule_counts"] == {"nonempty_batch": 1}
    raw = csv_bytes([GOOD]).rstrip() + b",unexpected\n"
    assert evaluate(raw, AS_OF, KEY)["report"]["rule_counts"]["required_fields"] == 1


def test_identity_includes_policy_date_and_pseudonym_key():
    raw = csv_bytes([GOOD])
    a = evaluate(raw, AS_OF, KEY)
    assert a == evaluate(raw, AS_OF, KEY)
    assert a["report"]["batch_id"] != evaluate(raw, date(2026, 10, 1), KEY)["report"]["batch_id"]
    assert a["report"]["batch_id"] != evaluate(raw, AS_OF, KEY + "rotated")["report"]["batch_id"]


def test_currency_totals_are_separate():
    rows = [GOOD, {**GOOD, "order_id": "ORD-02", "currency": "EUR"}]
    result = evaluate(csv_bytes(rows), AS_OF, KEY)
    assert len(result["gold"]) == 2
    assert result["report"]["reconciliation_cents"] == {"USD": 20, "EUR": 20}


def test_size_and_key_guards():
    with pytest.raises(ValueError, match="32 characters"):
        evaluate(csv_bytes([GOOD]), AS_OF, "short")
    with pytest.raises(ValueError, match="10 MiB"):
        evaluate(b"x" * (MAX_BYTES + 1), AS_OF, KEY)


def test_row_limit(monkeypatch):
    monkeypatch.setattr("retail_pipeline.quality.MAX_ROWS", 1)
    assert evaluate(csv_bytes([GOOD, GOOD]), AS_OF, KEY)["report"]["status"] == "rejected"


def test_bundled_fixtures():
    root = Path(__file__).resolve().parents[1]
    assert (
        evaluate((root / "examples/sales-clean.csv").read_bytes(), AS_OF, KEY)["report"][
            "accepted_rows"
        ]
        == 6
    )
    dirty = evaluate((root / "examples/sales-dirty.csv").read_bytes(), AS_OF, KEY)
    assert dirty["report"]["status"] == "rejected"
    assert dirty["report"]["rejected_rows"] == 5
