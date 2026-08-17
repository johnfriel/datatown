from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from datatown.pdl.inspect import (
    EXPECTED_CSV_HEADER,
    PDLInspectionError,
    inspect_pdl_files,
    render_inspection,
)


def write_pdl_fixture(
    directory: Path,
    *,
    json_name_override: str | None = None,
) -> tuple[Path, Path]:
    records: list[dict[str, object]] = [
        {
            "id": "id-1",
            "website": "example.com",
            "name": 'Example "Quoted" Company',
            "founded": 2020,
            "size": "1-10",
            "locality": "Chicago",
            "region": "Illinois",
            "country": "United States",
            "industry": "software",
            "linkedin_url": "linkedin.com/company/example",
        },
        {
            "id": "id-2",
            "website": None,
            "name": "Second Company",
            "founded": None,
            "size": "11-50",
            "locality": None,
            "region": None,
            "country": None,
            "industry": None,
            "linkedin_url": "linkedin.com/company/second",
        },
    ]
    csv_path = directory / "companies.csv"
    json_path = directory / "companies.json"
    with csv_path.open("w", encoding="utf-8", newline="") as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=EXPECTED_CSV_HEADER,
            escapechar="\\",
            doublequote=False,
        )
        writer.writeheader()
        for record in records:
            writer.writerow({field: record[field] for field in EXPECTED_CSV_HEADER})
    with json_path.open("w", encoding="utf-8") as destination:
        for position, record in enumerate(records):
            json_record = dict(record)
            if position == 1 and json_name_override is not None:
                json_record["name"] = json_name_override
            destination.write(json.dumps(json_record, ensure_ascii=False) + "\n")
    return csv_path, json_path


def test_inspection_streams_and_compares_both_representations(tmp_path: Path) -> None:
    csv_path, json_path = write_pdl_fixture(tmp_path)

    inspection = inspect_pdl_files(
        csv_path,
        json_path,
        sample_size=1,
        compare_records=10,
    )

    assert inspection.csv_header == EXPECTED_CSV_HEADER
    assert inspection.comparison.records_compared == 2
    assert inspection.comparison.aligned_id_mismatches == 0
    assert inspection.comparison.duplicate_json_ids == 0
    assert inspection.comparison.malformed_csv_rows == 0
    assert inspection.comparison.field_mismatches == ()
    assert inspection.structured_value_count == 0
    founded = next(field for field in inspection.field_observations if field.name == "founded")
    assert founded.observed_types == ("integer", "null")
    assert founded.null_count == 1


def test_inspection_reports_cross_representation_value_mismatch(tmp_path: Path) -> None:
    csv_path, json_path = write_pdl_fixture(tmp_path, json_name_override="Changed JSON Name")

    inspection = inspect_pdl_files(csv_path, json_path, compare_records=10)

    assert inspection.comparison.field_mismatches == (("name", 1),)


def test_inspection_can_hash_both_files(tmp_path: Path) -> None:
    csv_path, json_path = write_pdl_fixture(tmp_path)

    inspection = inspect_pdl_files(
        csv_path,
        json_path,
        compare_records=10,
        compute_sha256=True,
    )

    assert inspection.csv_file.sha256 == hashlib.sha256(csv_path.read_bytes()).hexdigest()
    assert inspection.json_file.sha256 == hashlib.sha256(json_path.read_bytes()).hexdigest()


def test_inspection_rejects_malformed_json(tmp_path: Path) -> None:
    csv_path, json_path = write_pdl_fixture(tmp_path)
    json_path.write_text("not json\n", encoding="utf-8")

    with pytest.raises(PDLInspectionError, match="Malformed JSON"):
        inspect_pdl_files(csv_path, json_path, compare_records=10)


def test_inspection_surfaces_an_unexpected_json_field_set(tmp_path: Path) -> None:
    csv_path, json_path = write_pdl_fixture(tmp_path)
    records = [json.loads(line) for line in json_path.read_text(encoding="utf-8").splitlines()]
    for record in records:
        record["new_vendor_field"] = "new value"
    json_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    inspection = inspect_pdl_files(csv_path, json_path, compare_records=10)
    report = render_inspection(inspection)

    assert "new_vendor_field" in inspection.json_fields
    assert "expected fields: NO" in report
    assert inspection.json_offset_schema_mismatches >= 1


def test_render_inspection_explains_bounded_hashing_and_recommendation(tmp_path: Path) -> None:
    csv_path, json_path = write_pdl_fixture(tmp_path)
    inspection = inspect_pdl_files(csv_path, json_path, compare_records=10)

    report = render_inspection(inspection)

    assert "not computed (use --sha256" in report
    assert "preferred input: newline-delimited JSON" in report
    assert "retain both files" in report
