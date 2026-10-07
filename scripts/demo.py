"""Reproduce public evidence using synthetic fixtures only; no Azure account required."""

import json
from datetime import date
from pathlib import Path

from retail_pipeline.runner import run
from retail_pipeline.storage import LocalStore

ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 9, 30)
KEY = "PUBLIC-SYNTHETIC-DEMO-KEY-DO-NOT-USE-FOR-REAL-DATA"


def main() -> None:
    evidence = ROOT / "evidence"
    evidence.mkdir(exist_ok=True)
    store = LocalStore(ROOT / "out" / "demo")
    reports = {}
    for name in ("clean", "dirty"):
        source = ROOT / "examples" / f"sales-{name}.csv"
        raw = source.read_bytes()
        store.put("bronze/" + source.name, raw)
        report = run(raw, source.name, AS_OF, KEY, store)
        assert run(raw, source.name, AS_OF, KEY, store) == report, "Retry changed the report"
        manifest = ROOT / "out" / "demo" / "manifests" / f"{report['batch_id']}.json"
        assert manifest.exists() == (name == "clean")
        reports[name] = report
        (evidence / f"{name}-report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    clean, dirty = reports["clean"], reports["dirty"]
    assert (clean["input_rows"], clean["accepted_rows"], clean["rejected_rows"]) == (6, 6, 0)
    assert (dirty["input_rows"], dirty["accepted_rows"], dirty["rejected_rows"]) == (6, 1, 5)
    assert clean["reconciliation_cents"] == {"USD": 4899, "EUR": 3500, "GBP": 1500}
    gold = ROOT / "out" / "demo" / "gold" / f"{clean['batch_id']}.jsonl"
    (evidence / "clean-gold.jsonl").write_bytes(gold.read_bytes())
    print("Clean: 6/6 accepted, published; dirty: 5/6 rejected, not published.")
    print("Identical retries preserved output; revenue totals verified per currency.")
    print("Evidence: evidence/; local lake: out/demo/; no Azure calls made.")


if __name__ == "__main__":
    main()
