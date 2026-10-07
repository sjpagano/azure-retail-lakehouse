import json

import azure.functions as func
import pytest

import function_app
from retail_pipeline.storage import LocalStore
from tests.test_quality import GOOD, KEY, csv_bytes


def request(body):
    return func.HttpRequest(
        method="POST", url="http://localhost/api/process-sales", body=json.dumps(body).encode()
    )


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"blob_name": "../bad.csv", "as_of": "2026-09-30"},
        {"blob_name": "a.txt", "as_of": "2026-09-30"},
        {"blob_name": "a.csv", "as_of": "invalid"},
        {"blob_name": 1, "as_of": "2026-09-30"},
    ],
)
def test_request_validation(body):
    assert function_app.process_sales(request(body)).status_code == 400


def test_function_runs_shared_engine(tmp_path, monkeypatch):
    class Store(LocalStore):
        def read_bronze(self, path):
            return csv_bytes([GOOD])

    monkeypatch.setattr(function_app, "AzureStore", lambda url: Store(tmp_path))
    monkeypatch.setenv("LAKE_ACCOUNT_URL", "https://demo.blob.core.windows.net")
    monkeypatch.setenv("PSEUDONYM_KEY", KEY)
    response = function_app.process_sales(request({"blob_name": "a.csv", "as_of": "2026-09-30"}))
    assert response.status_code == 200
    assert json.loads(response.get_body())["status"] == "passed"


def test_service_failure_does_not_leak_details(monkeypatch):
    monkeypatch.delenv("LAKE_ACCOUNT_URL", raising=False)
    response = function_app.process_sales(request({"blob_name": "a.csv", "as_of": "2026-09-30"}))
    assert response.status_code == 500
    assert json.loads(response.get_body()) == {"error": "Processing failed"}
