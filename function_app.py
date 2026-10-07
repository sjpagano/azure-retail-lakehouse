import json
import logging
import os
from datetime import date

import azure.functions as func

from retail_pipeline.runner import run
from retail_pipeline.storage import AzureStore, safe_path

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.route(route="process-sales", methods=["POST"])
def process_sales(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
        if not isinstance(body, dict) or set(body) != {"blob_name", "as_of"}:
            raise ValueError("Expected blob_name and as_of")
        if not isinstance(body["blob_name"], str) or not isinstance(body["as_of"], str):
            raise ValueError("Fields must be strings")
        blob_name = safe_path(body["blob_name"])
        as_of = date.fromisoformat(body["as_of"])
        if not blob_name.endswith(".csv"):
            raise ValueError("CSV source required")
    except (ValueError, TypeError, KeyError):
        return func.HttpResponse(
            json.dumps(
                {"error": "Invalid request: provide a relative CSV blob_name and ISO as_of date"}
            ),
            status_code=400,
            mimetype="application/json",
        )
    try:
        store = AzureStore(os.environ["LAKE_ACCOUNT_URL"])
        report = run(
            store.read_bronze(blob_name), blob_name, as_of, os.environ["PSEUDONYM_KEY"], store
        )
        logging.info(
            "batch=%s status=%s accepted=%s rejected=%s",
            report["batch_id"],
            report["status"],
            report["accepted_rows"],
            report["rejected_rows"],
        )
        return func.HttpResponse(json.dumps(report), mimetype="application/json")
    except Exception:
        # SDK exceptions can include resource URLs. Detailed investigation stays in restricted logs.
        logging.error("Batch processing failed; check source availability, RBAC and configuration")
        return func.HttpResponse(
            json.dumps({"error": "Processing failed"}), status_code=500, mimetype="application/json"
        )
