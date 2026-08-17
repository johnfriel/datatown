from __future__ import annotations

from botocore.stub import Stubber

from datatown.config import StorageConfig
from datatown.storage import create_storage_client, probe_storage


def storage_config() -> StorageConfig:
    return StorageConfig(
        endpoint_url="https://storage.example.test/s3",
        region="us-test-1",
        bucket="third-party-datasets",
        access_key_id="access-key",
        secret_access_key="secret-key",
    )


def test_probe_storage_uses_read_only_head_bucket() -> None:
    config = storage_config()
    client = create_storage_client(config)
    stubber = Stubber(client)
    stubber.add_response("head_bucket", {}, {"Bucket": config.bucket})

    with stubber:
        result = probe_storage(config, client=client)

    assert result.bucket == config.bucket


def test_storage_client_uses_path_style_for_s3_compatible_endpoints() -> None:
    client = create_storage_client(storage_config())

    assert client.meta.config.s3["addressing_style"] == "path"
    assert client.meta.config.request_checksum_calculation == "when_required"
    assert client.meta.config.response_checksum_validation == "when_required"
