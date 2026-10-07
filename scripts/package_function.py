"""Build a source-only deployment archive. Azure performs the Linux dependency build."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
destination = root / "dist" / "function.zip"
destination.parent.mkdir(exist_ok=True)
files = [root / name for name in ("function_app.py", "host.json", "requirements.txt")]
files += sorted((root / "retail_pipeline").glob("*.py"))
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for file in files:
        archive.write(file, file.relative_to(root))
print(destination)
