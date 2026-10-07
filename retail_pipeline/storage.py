"""Conditional writes make retries safe. Readers consume only committed manifests."""

from pathlib import Path, PurePosixPath

from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from retail_pipeline.quality import MAX_BYTES


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value:
        raise ValueError("Relative storage path required")
    if len(value) > 200 or any(not part for part in value.split("/")) or "." in value.split("/"):
        raise ValueError("Invalid storage path")
    return value


class LocalStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def put(self, path: str, data: bytes) -> None:
        target = (self.root / safe_path(path)).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("Storage path escapes root")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as stream:
                stream.write(data)
        except FileExistsError:
            if target.read_bytes() != data:
                raise ValueError("Existing batch content differs; refusing overwrite") from None


class AzureStore:
    def __init__(self, account_url: str):
        self.client = BlobServiceClient(account_url, credential=DefaultAzureCredential())

    def read_bronze(self, blob_name: str) -> bytes:
        safe_path(blob_name)
        blob = self.client.get_blob_client("bronze", blob_name)
        props = blob.get_blob_properties()
        if props.size > MAX_BYTES:
            raise ValueError("Batch exceeds the 10 MiB limit")
        # If the source changes after the size check, pin the read to its ETag.
        from azure.core import MatchConditions

        return blob.download_blob(
            etag=props.etag, match_condition=MatchConditions.IfNotModified
        ).readall()

    def put(self, path: str, data: bytes) -> None:
        container, blob_name = safe_path(path).split("/", 1)
        blob = self.client.get_blob_client(container, blob_name)
        try:
            blob.upload_blob(
                data,
                overwrite=False,
                content_settings=ContentSettings(content_type="application/json"),
            )
        except ResourceExistsError:
            if blob.download_blob().readall() != data:
                raise ValueError("Existing batch content differs; refusing overwrite") from None
