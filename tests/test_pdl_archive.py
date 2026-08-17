from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, SSLError
from botocore.stub import Stubber

from datatown.config import StorageConfig
from datatown.pdl.archive import (
    MULTIPART_PART_SIZE,
    TRANSFER_CONFIG,
    ArchiveError,
    _ensure_file_object,
    _retry_ssl_upload_part,
    _s3_error_detail,
    build_archive_plan,
    parse_acquired_at,
)
from datatown.storage import create_storage_client


def storage_config() -> StorageConfig:
    return StorageConfig(
        endpoint_url="https://storage.example.test/s3",
        region="us-test-1",
        bucket="third-party-datasets",
        access_key_id="access-key",
        secret_access_key="secret-key",
    )


def source_files(tmp_path: Path) -> tuple[Path, Path]:
    csv_path = tmp_path / "companies.csv"
    json_path = tmp_path / "companies.json"
    csv_path.write_bytes(b'id,name\n"pdl-1","Example"\n')
    json_path.write_bytes(b'{"id":"pdl-1","name":"Example"}\n')
    return csv_path, json_path


def test_parse_acquired_at_accepts_dates_and_normalizes_offsets() -> None:
    assert parse_acquired_at("2026-08-17") == datetime(2026, 8, 17, tzinfo=UTC)
    assert parse_acquired_at("2026-08-16T19:00:00-05:00") == datetime(2026, 8, 17, tzinfo=UTC)


def test_multipart_parts_keep_known_artifacts_below_s3_part_limit() -> None:
    assert MULTIPART_PART_SIZE == 32 * 1024 * 1024
    assert TRANSFER_CONFIG.multipart_chunksize == MULTIPART_PART_SIZE
    assert TRANSFER_CONFIG.max_request_concurrency == 1
    assert not TRANSFER_CONFIG.use_threads


def test_build_archive_plan_hashes_originals_and_builds_deterministic_manifest(
    tmp_path: Path,
) -> None:
    csv_path, json_path = source_files(tmp_path)

    plan = build_archive_plan(
        csv_path,
        json_path,
        acquired_at=datetime(2026, 8, 17, 14, 30, tzinfo=UTC),
        source_url="https://example.test/pdl",
    )

    assert plan.snapshot.manifest_object_key == "pdl/company/2026-08-17/manifest.json"
    assert [artifact.metadata.object_key for artifact in plan.artifacts] == [
        "pdl/company/2026-08-17/companies.csv",
        "pdl/company/2026-08-17/companies.json",
    ]
    assert plan.artifacts[0].metadata.sha256 == hashlib.sha256(csv_path.read_bytes()).hexdigest()
    assert plan.artifacts[1].metadata.sha256 == hashlib.sha256(json_path.read_bytes()).hexdigest()

    manifest = json.loads(plan.manifest_bytes)
    assert manifest["source"] == "pdl"
    assert manifest["dataset"] == "company"
    assert manifest["acquired_at"] == "2026-08-17T14:30:00Z"
    assert [file["role"] for file in manifest["files"]] == [
        "original_csv",
        "original_json",
    ]
    assert plan.manifest_sha256 == hashlib.sha256(plan.manifest_bytes).hexdigest()


def test_build_archive_plan_refuses_ambiguous_object_keys(tmp_path: Path) -> None:
    source = tmp_path / "companies.data"
    source.write_bytes(b"original")

    with pytest.raises(ArchiveError, match="distinct filenames"):
        build_archive_plan(source, source, acquired_at=datetime(2026, 8, 17, tzinfo=UTC))


def test_existing_object_with_matching_identity_is_reused(tmp_path: Path) -> None:
    csv_path, json_path = source_files(tmp_path)
    artifact = build_archive_plan(
        csv_path, json_path, acquired_at=datetime(2026, 8, 17, tzinfo=UTC)
    ).artifacts[0]
    config = storage_config()
    client = create_storage_client(config)
    stubber = Stubber(client)
    stubber.add_response(
        "head_object",
        {
            "ContentLength": artifact.metadata.byte_size,
            "Metadata": {"sha256": artifact.metadata.sha256},
        },
        {"Bucket": config.bucket, "Key": artifact.metadata.object_key},
    )

    with stubber:
        uploaded = _ensure_file_object(client, config, artifact, None)

    assert not uploaded


def test_existing_object_collision_is_rejected(tmp_path: Path) -> None:
    csv_path, json_path = source_files(tmp_path)
    artifact = build_archive_plan(
        csv_path, json_path, acquired_at=datetime(2026, 8, 17, tzinfo=UTC)
    ).artifacts[0]
    config = storage_config()
    client = create_storage_client(config)
    stubber = Stubber(client)
    stubber.add_response(
        "head_object",
        {"ContentLength": artifact.metadata.byte_size, "Metadata": {"sha256": "0" * 64}},
        {"Bucket": config.bucket, "Key": artifact.metadata.object_key},
    )

    with stubber, pytest.raises(ArchiveError, match="Remote object collision"):
        _ensure_file_object(client, config, artifact, None)


def test_source_change_after_hashing_is_rejected_before_storage_access(tmp_path: Path) -> None:
    csv_path, json_path = source_files(tmp_path)
    artifact = build_archive_plan(
        csv_path, json_path, acquired_at=datetime(2026, 8, 17, tzinfo=UTC)
    ).artifacts[0]
    csv_path.write_bytes(csv_path.read_bytes() + b"changed")

    with pytest.raises(ArchiveError, match="changed after it was hashed"):
        _ensure_file_object(
            create_storage_client(storage_config()), storage_config(), artifact, None
        )


def test_s3_error_detail_recovers_status_when_service_omits_error_text() -> None:
    error = ClientError(
        {"Error": {"Code": "", "Message": ""}, "ResponseMetadata": {"HTTPStatusCode": 413}},
        "UploadPart",
    )

    assert _s3_error_detail(error) == "UploadPart: HTTP 413, code unspecified"


def test_ssl_upload_part_errors_receive_bounded_backoff() -> None:
    error = SSLError(endpoint_url="https://storage.example.test", error="bad record mac")

    assert _retry_ssl_upload_part(attempts=1, caught_exception=error) == 1.0
    assert _retry_ssl_upload_part(attempts=4, caught_exception=error) == 8.0
    assert _retry_ssl_upload_part(attempts=10, caught_exception=error) is None
    assert _retry_ssl_upload_part(attempts=1, caught_exception=RuntimeError()) is None
