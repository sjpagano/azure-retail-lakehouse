from datetime import date
from typing import Protocol

from retail_pipeline.quality import evaluate, json_bytes, json_lines
from retail_pipeline.storage import safe_path


class Store(Protocol):
    def put(self, path: str, data: bytes) -> None: ...


def run(raw: bytes, source_name: str, as_of: date, key: str, store: Store) -> dict:
    safe_path(source_name)
    result = evaluate(raw, as_of, key)
    report = result["report"]
    batch = report["batch_id"]
    # Content identity includes the logical source name, so separate sources have distinct lineage.
    import hashlib

    batch = hashlib.sha256((batch + ":" + source_name).encode()).hexdigest()
    report["batch_id"] = batch
    paths = {
        "bronze": f"bronze/{source_name}",
        "report": f"quality/{batch}.json",
        "quarantine": f"quarantine/{batch}.jsonl",
        "silver": f"silver/{batch}.jsonl",
        "gold": f"gold/{batch}.jsonl",
        "manifest": f"manifests/{batch}.json",
    }
    report["lineage"] = {
        "input": paths["bronze"],
        "outputs": [paths["report"], paths["quarantine"]],
    }
    if report["status"] == "passed":
        report["lineage"]["outputs"].extend([paths["silver"], paths["gold"], paths["manifest"]])
    store.put(paths["quarantine"], json_lines(result["quarantine"]))
    store.put(paths["report"], json_bytes(report))
    if report["status"] == "passed":
        store.put(paths["silver"], json_lines(result["silver"]))
        store.put(paths["gold"], json_lines(result["gold"]))
        # Commit marker is last. A partial upload is invisible to manifest-following consumers.
        store.put(
            paths["manifest"],
            json_bytes(
                {
                    "batch_id": batch,
                    "status": "committed",
                    "report": paths["report"],
                    "silver": paths["silver"],
                    "gold": paths["gold"],
                    "source_sha256": report["input_sha256"],
                }
            ),
        )
    return report
