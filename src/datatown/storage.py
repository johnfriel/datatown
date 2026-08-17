"""S3-compatible object-storage clients and read-only health checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config

from datatown.config import StorageConfig

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


@dataclass(frozen=True, slots=True)
class StorageProbeResult:
    """Identity returned by a successful read-only storage probe."""

    bucket: str


def create_storage_client(config: StorageConfig) -> S3Client:
    """Create an S3 client without relying on ambient AWS credential discovery."""
    client = boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        region_name=config.region,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
        config=Config(
            connect_timeout=config.connect_timeout_seconds,
            read_timeout=config.read_timeout_seconds,
            # Recent AWS SDKs opt into CRC checksums that Supabase's S3 layer does not
            # implement. Required-only preserves compatibility with ordinary S3 backends.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            retries={"max_attempts": 5, "mode": "standard"},
            s3={"addressing_style": "path"},
            signature_version="s3v4",
        ),
    )
    return client


def probe_storage(config: StorageConfig, *, client: S3Client | None = None) -> StorageProbeResult:
    """Check access to the configured bucket without creating or changing objects."""
    storage_client = client if client is not None else create_storage_client(config)
    storage_client.head_bucket(Bucket=config.bucket)
    return StorageProbeResult(bucket=config.bucket)
