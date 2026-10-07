import argparse
import json
import os
from datetime import date
from pathlib import Path

from retail_pipeline.runner import run
from retail_pipeline.storage import LocalStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and publish a retail batch locally")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--as-of", required=True, type=date.fromisoformat)
    parser.add_argument("--output", type=Path, default=Path("out"))
    args = parser.parse_args(argv)
    key = os.environ.get("PSEUDONYM_KEY", "")
    raw = args.input.read_bytes()
    store = LocalStore(args.output)
    store.put("bronze/" + args.input.name, raw)
    report = run(raw, args.input.name, args.as_of, key, store)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
