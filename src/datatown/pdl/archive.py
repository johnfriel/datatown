"""Archive original PDL company artifacts and record their provenance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

from datatown.config import DatabaseConfig, StorageConfig
from datatown.hashing import sha256_file
from datatown.metadata.models import DatasetFileInput, DatasetSnapshotInput
from datatown.metadata.repository import RecordedSnapshot, preflight_snapshot, record_snapshot
from datatown.storage import create_storage_client

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import HeadObjectOutputTypeDef

PDL_SOURCE = "pdl"
PDL_DATASET = "company"
# Large parts reduce request overhead while keeping the known PDL originals far below S3's
# 10,000-part ceiling. Supabase separately enforces the project's cumulative file-size limit.
MULTIPART_PART_SIZE = 64 * 1024 * 1024
TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=MULTIPART_PART_SIZE,
    multipart_chunksize=MULTIPART_PART_SIZE,
    max_concurrency=4,
    use_threads=True,
)


class ArchiveError(RuntimeError):
    """Raised when an archive operation would be ambiguous or unsafe."""


@dataclass(frozen=True, slots=True)
class ArchiveArtifact:
    path: Path
    metadata: DatasetFileInput
    content_type: str
    device: int
    inode: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class ArchivePlan:
    snapshot: DatasetSnapshotInput
    artifacts: tuple[ArchiveArtifact, ...]
    manifest_bytes: bytes
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    recorded: RecordedSnapshot
    uploaded_keys: tuple[str, ...]
    existing_keys: tuple[str, ...]


ProgressReporter = Callable[[ArchiveArtifact, int, int], None]


class _UploadProgress:
    def __init__(self, artifact: ArchiveArtifact, reporter: ProgressReporter) -> None:
        self.artifact = artifact
        self.reporter = reporter
        self.transferred = 0
        self.next_percentage = 10
        self.lock = Lock()

    def __call__(self, byte_count: int) -> None:
        with self.lock:
            self.transferred += byte_count
            total = self.artifact.metadata.byte_size
            percentage = min(100, int(self.transferred * 100 / total)) if total else 100
            if percentage < self.next_percentage:
                return
            self.reporter(self.artifact, self.transferred, total)
            self.next_percentage = ((percentage // 10) + 1) * 10


def parse_acquired_at(value: str) -> datetime:
    """Parse an ISO date/datetime and normalize it to an aware UTC timestamp."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ArchiveError("--acquired-at must be an ISO date or datetime") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _artifact(
    path: Path,
    *,
    role: str,
    format_name: str,
    content_type: str,
    object_prefix: str,
) -> ArchiveArtifact:
    if not path.is_file():
        raise ArchiveError(f"Source artifact does not exist: {path}")
    before = path.stat()
    digest = sha256_file(path)
    after = path.stat()
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_identity != after_identity:
        raise ArchiveError(f"Source artifact changed while it was being hashed: {path}")
    return ArchiveArtifact(
        path=path,
        metadata=DatasetFileInput(
            role=role,
            filename=path.name,
            object_key=f"{object_prefix}/{path.name}",
            format=format_name,
            sha256=digest,
            byte_size=after.st_size,
        ),
        content_type=content_type,
        device=after.st_dev,
        inode=after.st_ino,
        modified_ns=after.st_mtime_ns,
    )


def _validate_local_artifact(artifact: ArchiveArtifact) -> None:
    try:
        current = artifact.path.stat()
    except OSError as error:
        raise ArchiveError(f"Source artifact is no longer readable: {artifact.path}") from error
    current_identity = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
    planned_identity = (
        artifact.device,
        artifact.inode,
        artifact.metadata.byte_size,
        artifact.modified_ns,
    )
    if current_identity != planned_identity:
        raise ArchiveError(f"Source artifact changed after it was hashed: {artifact.path}")


def build_archive_plan(
    csv_path: str | Path,
    json_path: str | Path,
    *,
    acquired_at: datetime,
    source_url: str | None = None,
    description: str | None = "PDL free company dataset",
) -> ArchivePlan:
    """Hash both originals and build deterministic object keys and manifest bytes."""
    acquired_at = acquired_at.astimezone(UTC)
    object_prefix = f"pdl/company/{acquired_at.date().isoformat()}"
    artifacts = (
        _artifact(
            Path(csv_path),
            role="original_csv",
            format_name="csv",
            content_type="text/csv",
            object_prefix=object_prefix,
        ),
        _artifact(
            Path(json_path),
            role="original_json",
            format_name="jsonl",
            content_type="application/x-ndjson",
            object_prefix=object_prefix,
        ),
    )
    object_keys = [artifact.metadata.object_key for artifact in artifacts]
    if len(set(object_keys)) != len(object_keys):
        raise ArchiveError("CSV and JSON originals must have distinct filenames")
    manifest_object_key = f"{object_prefix}/manifest.json"
    manifest = {
        "source": PDL_SOURCE,
        "dataset": PDL_DATASET,
        "acquired_at": acquired_at.isoformat().replace("+00:00", "Z"),
        "source_url": source_url,
        "description": description,
        "files": [
            {
                "role": artifact.metadata.role,
                "filename": artifact.metadata.filename,
                "object_key": artifact.metadata.object_key,
                "format": artifact.metadata.format,
                "sha256": artifact.metadata.sha256,
                "bytes": artifact.metadata.byte_size,
            }
            for artifact in artifacts
        ],
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    snapshot = DatasetSnapshotInput(
        source=PDL_SOURCE,
        dataset=PDL_DATASET,
        acquired_at=acquired_at,
        source_url=source_url,
        description=description,
        manifest_object_key=manifest_object_key,
        files=tuple(artifact.metadata for artifact in artifacts),
    )
    return ArchivePlan(
        snapshot=snapshot,
        artifacts=artifacts,
        manifest_bytes=manifest_bytes,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )


def _head_object(client: S3Client, bucket: str, key: str) -> HeadObjectOutputTypeDef | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        code = error.response["Error"].get("Code")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def _s3_error_detail(error: BaseException) -> str | None:
    """Recover service details hidden by boto3's high-level transfer exception."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ClientError):
            response = current.response
            error_data = response.get("Error", {})
            response_metadata = response.get("ResponseMetadata", {})
            operation = current.operation_name
            status = response_metadata.get("HTTPStatusCode")
            code = error_data.get("Code") or "unspecified"
            message = error_data.get("Message")
            detail = f"{operation}: HTTP {status or 'unknown'}, code {code}"
            return f"{detail}, {message}" if message else detail
        current = current.__cause__ or current.__context__
    return None


def _verify_remote_object(
    head: HeadObjectOutputTypeDef,
    *,
    key: str,
    byte_size: int,
    sha256: str,
) -> None:
    remote_size = head.get("ContentLength")
    remote_sha256 = head.get("Metadata", {}).get("sha256")
    if remote_size != byte_size or remote_sha256 != sha256:
        raise ArchiveError(
            f"Remote object collision at {key}: expected {byte_size} bytes/{sha256}, "
            f"found {remote_size} bytes/{remote_sha256 or 'no sha256 metadata'}"
        )


def _ensure_file_object(
    client: S3Client,
    storage: StorageConfig,
    artifact: ArchiveArtifact,
    progress: ProgressReporter | None,
) -> bool:
    metadata = artifact.metadata
    _validate_local_artifact(artifact)
    existing = _head_object(client, storage.bucket, metadata.object_key)
    if existing is not None:
        _verify_remote_object(
            existing,
            key=metadata.object_key,
            byte_size=metadata.byte_size,
            sha256=metadata.sha256,
        )
        return False

    callback = _UploadProgress(artifact, progress) if progress is not None else None
    client.upload_file(
        str(artifact.path),
        storage.bucket,
        metadata.object_key,
        ExtraArgs={
            "ContentType": artifact.content_type,
            "Metadata": {"sha256": metadata.sha256, "role": metadata.role},
        },
        Config=TRANSFER_CONFIG,
        Callback=callback,
    )
    _validate_local_artifact(artifact)
    uploaded = _head_object(client, storage.bucket, metadata.object_key)
    if uploaded is None:
        raise ArchiveError(f"Uploaded object could not be verified: {metadata.object_key}")
    _verify_remote_object(
        uploaded,
        key=metadata.object_key,
        byte_size=metadata.byte_size,
        sha256=metadata.sha256,
    )
    return True


def _ensure_manifest_object(client: S3Client, storage: StorageConfig, plan: ArchivePlan) -> bool:
    key = plan.snapshot.manifest_object_key
    existing = _head_object(client, storage.bucket, key)
    if existing is not None:
        _verify_remote_object(
            existing,
            key=key,
            byte_size=len(plan.manifest_bytes),
            sha256=plan.manifest_sha256,
        )
        return False

    client.put_object(
        Bucket=storage.bucket,
        Key=key,
        Body=plan.manifest_bytes,
        ContentType="application/json",
        Metadata={"sha256": plan.manifest_sha256, "role": "manifest"},
    )
    uploaded = _head_object(client, storage.bucket, key)
    if uploaded is None:
        raise ArchiveError(f"Uploaded manifest could not be verified: {key}")
    _verify_remote_object(
        uploaded,
        key=key,
        byte_size=len(plan.manifest_bytes),
        sha256=plan.manifest_sha256,
    )
    return True


def archive_plan(
    database: DatabaseConfig,
    storage: StorageConfig,
    plan: ArchivePlan,
    *,
    progress: ProgressReporter | None = None,
) -> ArchiveResult:
    """Upload missing objects, verify all remote identities, and record provenance."""
    preflight_snapshot(database, plan.snapshot)
    client = create_storage_client(storage)
    uploaded_keys: list[str] = []
    existing_keys: list[str] = []

    try:
        for artifact in plan.artifacts:
            if _ensure_file_object(client, storage, artifact, progress):
                uploaded_keys.append(artifact.metadata.object_key)
            else:
                existing_keys.append(artifact.metadata.object_key)

        if _ensure_manifest_object(client, storage, plan):
            uploaded_keys.append(plan.snapshot.manifest_object_key)
        else:
            existing_keys.append(plan.snapshot.manifest_object_key)
    except Exception as error:
        service_detail = _s3_error_detail(error)
        if service_detail is not None:
            raise ArchiveError(f"S3 operation failed ({service_detail})") from error
        raise

    recorded = record_snapshot(database, plan.snapshot)
    return ArchiveResult(
        recorded=recorded,
        uploaded_keys=tuple(uploaded_keys),
        existing_keys=tuple(existing_keys),
    )
