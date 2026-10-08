"""Immutable archive of every source vintage: a local directory or ``gs://bucket/prefix``.

BigQuery only ever holds the latest vintage (Zillow restates history every month), so the
archive is what makes any past load reproducible.
"""

from __future__ import annotations

import gzip
import logging
import shutil
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def archive_name(dataset_key: str, sha256: str, last_modified: datetime | None, suffix: str) -> str:
    stamp = last_modified.strftime("%Y-%m-%d") if last_modified else "unknown-date"
    return f"{dataset_key}/{stamp}_{sha256[:16]}{suffix}.gz"


def archive_file(uri: str, local: Path, name: str) -> str:
    """Gzip ``local`` into the archive under ``name``; existing objects are never overwritten."""
    if uri.startswith("gs://"):
        return _archive_gcs(uri, local, name)
    target = Path(uri) / name
    if target.exists():
        return str(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    with local.open("rb") as src, gzip.open(tmp, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)
    tmp.replace(target)
    return str(target)


def _archive_gcs(uri: str, local: Path, name: str) -> str:
    from google.cloud import storage

    bucket_name, _, prefix = uri.removeprefix("gs://").partition("/")
    blob_name = f"{prefix.rstrip('/')}/{name}" if prefix else name
    blob = storage.Client().bucket(bucket_name).blob(blob_name)
    if blob.exists():
        return f"gs://{bucket_name}/{blob_name}"
    gz = local.with_suffix(local.suffix + ".gz")
    with local.open("rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)
    # if_generation_match=0 makes the upload fail rather than overwrite a concurrent writer.
    blob.upload_from_filename(str(gz), content_type="application/gzip", if_generation_match=0)
    gz.unlink(missing_ok=True)
    return f"gs://{bucket_name}/{blob_name}"
