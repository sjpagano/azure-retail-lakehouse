import json
from unittest.mock import Mock

import pytest
from azure.core.exceptions import ResourceExistsError

from retail_pipeline.cli import main
from retail_pipeline.runner import run
from retail_pipeline.storage import AzureStore, LocalStore, safe_path
from tests.test_quality import AS_OF, GOOD, KEY, csv_bytes


def test_retry_is_idempotent_and_manifest_is_last(tmp_path):
    store = LocalStore(tmp_path)
    raw = csv_bytes([GOOD])
    first = run(raw, "daily.csv", AS_OF, KEY, store)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert run(raw, "daily.csv", AS_OF, KEY, store) == first
    after = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
    manifest = json.loads(next((tmp_path / "manifests").glob("*.json")).read_text())
    assert (tmp_path / manifest["gold"]).exists()
    assert (tmp_path / manifest["silver"]).exists()


def test_rejected_batch_cannot_publish(tmp_path):
    result = run(
        csv_bytes([{**GOOD, "quantity": "-1"}]), "daily.csv", AS_OF, KEY, LocalStore(tmp_path)
    )
    assert result["status"] == "rejected"
    assert (tmp_path / "quality").exists()
    assert (tmp_path / "quarantine").exists()
    assert not (tmp_path / "gold").exists()
    assert not (tmp_path / "manifests").exists()


def test_partial_failure_has_no_manifest_and_retry_recovers(tmp_path):
    underlying = LocalStore(tmp_path)

    class Interrupted:
        def put(self, path, data):
            if path.startswith("gold/"):
                raise OSError("Simulated network outage")
            underlying.put(path, data)

    raw = csv_bytes([GOOD])
    with pytest.raises(OSError):
        run(raw, "daily.csv", AS_OF, KEY, Interrupted())
    assert not (tmp_path / "manifests").exists()
    run(raw, "daily.csv", AS_OF, KEY, underlying)
    assert len(list((tmp_path / "manifests").glob("*"))) == 1


@pytest.mark.parametrize(
    "path", ["../escape", "/absolute", "C:/escape", "a\\b", "a//b", "a/./b", ""]
)
def test_paths_are_scoped(path):
    with pytest.raises(ValueError):
        safe_path(path)


def test_existing_objects_never_overwritten(tmp_path):
    store = LocalStore(tmp_path)
    store.put("gold/test.json", b"original")
    with pytest.raises(ValueError, match="refusing overwrite"):
        store.put("gold/test.json", b"changed")
    assert (tmp_path / "gold/test.json").read_bytes() == b"original"


def test_cli_exit_codes_and_bronze_copy(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PSEUDONYM_KEY", KEY)
    source = tmp_path / "input.csv"
    source.write_bytes(csv_bytes([GOOD]))
    args = ["--input", str(source), "--as-of", "2026-09-30", "--output", str(tmp_path / "out")]
    assert main(args) == 0
    assert (tmp_path / "out/bronze/input.csv").read_bytes() == source.read_bytes()
    assert '"status": "passed"' in capsys.readouterr().out
    bad = tmp_path / "bad.csv"
    bad.write_bytes(csv_bytes([{**GOOD, "quantity": "-1"}]))
    args[1] = str(bad)
    assert main(args) == 2


def test_azure_adapter_pins_source_and_handles_retry(monkeypatch):
    service = Mock()
    blob = service.get_blob_client.return_value
    blob.get_blob_properties.return_value.size = 5
    blob.get_blob_properties.return_value.etag = "etag-1"
    blob.download_blob.return_value.readall.return_value = b"hello"
    monkeypatch.setattr("retail_pipeline.storage.DefaultAzureCredential", lambda: "credential")
    factory = Mock(return_value=service)
    monkeypatch.setattr("retail_pipeline.storage.BlobServiceClient", factory)
    store = AzureStore("https://demo.blob.core.windows.net")
    assert store.read_bronze("a.csv") == b"hello"
    assert blob.download_blob.call_args.kwargs["etag"] == "etag-1"
    store.put("silver/a.json", b"hello")
    assert blob.upload_blob.call_args.kwargs["overwrite"] is False
    blob.upload_blob.side_effect = ResourceExistsError("already exists")
    store.put("silver/a.json", b"hello")
    with pytest.raises(ValueError):
        store.put("silver/a.json", b"different")
    blob.get_blob_properties.return_value.size = 20 * 1024 * 1024
    with pytest.raises(ValueError):
        store.read_bronze("large.csv")
