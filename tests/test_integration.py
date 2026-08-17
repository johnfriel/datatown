from __future__ import annotations

import os

import pytest

from datatown.config import Settings
from datatown.db import probe_database
from datatown.storage import probe_storage


@pytest.mark.integration
def test_configured_services_are_reachable_read_only() -> None:
    if os.environ.get("DATATOWN_RUN_INTEGRATION") != "1":
        pytest.skip("set DATATOWN_RUN_INTEGRATION=1 to run external connectivity checks")

    settings = Settings.from_env()

    database = probe_database(settings.database)
    storage = probe_storage(settings.storage)

    assert database.database_name
    assert database.server_version
    assert storage.bucket == settings.storage.bucket
