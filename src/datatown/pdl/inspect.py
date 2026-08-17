"""Bounded, streaming inspection of the PDL free company dataset."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from datatown.formatting import format_bytes
from datatown.hashing import sha256_file

EXPECTED_CSV_HEADER = (
    "country",
    "founded",
    "id",
    "industry",
    "linkedin_url",
    "locality",
    "name",
    "region",
    "size",
    "website",
)
EXPECTED_JSON_FIELDS = (
    "id",
    "website",
    "name",
    "founded",
    "size",
    "locality",
    "region",
    "country",
    "industry",
    "linkedin_url",
)
JSON_OFFSET_FRACTIONS = (0.0, 0.25, 0.5, 0.75, 0.99)
DEFAULT_COMPARE_RECORDS = 100_000


class PDLInspectionError(ValueError):
    """Raised when a PDL source artifact cannot be inspected safely."""


@dataclass(frozen=True, slots=True)
class FileInfo:
    path: Path
    byte_size: int
    sha256: str | None


@dataclass(frozen=True, slots=True)
class FieldObservation:
    name: str
    observed_types: tuple[str, ...]
    null_count: int
    observed_count: int


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    records_compared: int
    aligned_id_mismatches: int
    duplicate_json_ids: int
    malformed_csv_rows: int
    field_mismatches: tuple[tuple[str, int], ...]
    default_parser_first_malformed_row: int | None
    default_parser_first_id_mismatch: int | None


@dataclass(frozen=True, slots=True)
class PDLInspection:
    csv_file: FileInfo
    json_file: FileInfo
    csv_header: tuple[str, ...]
    json_fields: tuple[str, ...]
    field_observations: tuple[FieldObservation, ...]
    sample_records: tuple[dict[str, object], ...]
    json_offset_probes: int
    json_offset_schema_mismatches: int
    structured_value_count: int
    comparison: ComparisonResult


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _load_json_record(line: str, *, location: str) -> dict[str, object]:
    try:
        value: object = json.loads(line)
    except json.JSONDecodeError as error:
        raise PDLInspectionError(f"Malformed JSON at {location}: {error.msg}") from error
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise PDLInspectionError(f"Expected a JSON object at {location}")
    return value


def _normalize_csv_value(field: str, value: str | None) -> object:
    if value is None or value == "":
        return None
    if field == "founded":
        try:
            return int(value)
        except ValueError as error:
            raise PDLInspectionError(f"CSV founded value is not an integer: {value!r}") from error
    return value


def _file_info(path: Path, *, compute_sha256: bool) -> FileInfo:
    if not path.is_file():
        raise PDLInspectionError(f"Source file does not exist: {path}")
    return FileInfo(
        path=path,
        byte_size=path.stat().st_size,
        sha256=sha256_file(path) if compute_sha256 else None,
    )


def _default_parser_issues(
    csv_path: Path, json_path: Path, limit: int
) -> tuple[int | None, int | None]:
    first_malformed_row: int | None = None
    first_id_mismatch: int | None = None
    with (
        csv_path.open("r", encoding="utf-8", newline="") as csv_source,
        json_path.open("r", encoding="utf-8") as json_source,
    ):
        reader = csv.DictReader(csv_source)
        for position, (csv_record, json_line) in enumerate(
            islice(zip(reader, json_source, strict=False), limit), start=1
        ):
            json_record = _load_json_record(json_line, location=f"JSON line {position}")
            if first_malformed_row is None and None in csv_record:
                first_malformed_row = position
            if first_id_mismatch is None and csv_record.get("id") != json_record.get("id"):
                first_id_mismatch = position
            if first_malformed_row is not None and first_id_mismatch is not None:
                break
    return first_malformed_row, first_id_mismatch


def _probe_json_offsets(path: Path) -> tuple[int, int]:
    byte_size = path.stat().st_size
    probes = 0
    schema_mismatches = 0
    with path.open("rb") as source:
        for fraction in JSON_OFFSET_FRACTIONS:
            offset = int(byte_size * fraction)
            source.seek(offset)
            if offset:
                source.readline()
            line_offset = source.tell()
            line = source.readline()
            if not line:
                continue
            try:
                text = line.decode("utf-8")
            except UnicodeDecodeError as error:
                raise PDLInspectionError(
                    f"JSON is not UTF-8 near byte offset {line_offset}"
                ) from error
            record = _load_json_record(text, location=f"JSON byte offset {line_offset}")
            probes += 1
            if tuple(record) != EXPECTED_JSON_FIELDS:
                schema_mismatches += 1
    return probes, schema_mismatches


def _compare_files(
    csv_path: Path,
    json_path: Path,
    *,
    sample_size: int,
    compare_records: int,
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[FieldObservation, ...],
    tuple[dict[str, object], ...],
    int,
    ComparisonResult,
]:
    observed_types: dict[str, Counter[str]] = defaultdict(Counter)
    null_counts: Counter[str] = Counter()
    observed_counts: Counter[str] = Counter()
    field_mismatches: Counter[str] = Counter()
    samples: list[dict[str, object]] = []
    json_fields: tuple[str, ...] = ()
    seen_json_ids: set[str] = set()
    id_mismatches = 0
    duplicate_json_ids = 0
    malformed_csv_rows = 0
    structured_value_count = 0
    compared = 0

    with (
        csv_path.open("r", encoding="utf-8", newline="") as csv_source,
        json_path.open("r", encoding="utf-8") as json_source,
    ):
        reader = csv.DictReader(csv_source, escapechar="\\")
        if reader.fieldnames is None:
            raise PDLInspectionError("CSV file has no header")
        csv_header = tuple(reader.fieldnames)

        for position, (csv_record, json_line) in enumerate(
            islice(zip(reader, json_source, strict=False), compare_records), start=1
        ):
            json_record = _load_json_record(json_line, location=f"JSON line {position}")
            if not json_fields:
                json_fields = tuple(json_record)
            if len(samples) < sample_size:
                samples.append(json_record)

            json_id = json_record.get("id")
            if isinstance(json_id, str):
                if json_id in seen_json_ids:
                    duplicate_json_ids += 1
                seen_json_ids.add(json_id)

            if None in csv_record:
                malformed_csv_rows += 1

            for field, value in json_record.items():
                value_type = _json_type(value)
                observed_types[field][value_type] += 1
                observed_counts[field] += 1
                if value is None:
                    null_counts[field] += 1
                if value_type in {"array", "object"}:
                    structured_value_count += 1

            if csv_record.get("id") != json_record.get("id"):
                id_mismatches += 1
            else:
                for field in csv_header:
                    if _normalize_csv_value(field, csv_record.get(field)) != json_record.get(field):
                        field_mismatches[field] += 1
            compared += 1

    if compared == 0:
        raise PDLInspectionError("Source files contain no comparable records")

    fields_in_order = json_fields or EXPECTED_JSON_FIELDS
    observations = tuple(
        FieldObservation(
            name=field,
            observed_types=tuple(sorted(observed_types[field])),
            null_count=null_counts[field],
            observed_count=observed_counts[field],
        )
        for field in fields_in_order
    )
    default_malformed_row, default_id_mismatch = _default_parser_issues(
        csv_path, json_path, compare_records
    )
    comparison = ComparisonResult(
        records_compared=compared,
        aligned_id_mismatches=id_mismatches,
        duplicate_json_ids=duplicate_json_ids,
        malformed_csv_rows=malformed_csv_rows,
        field_mismatches=tuple(sorted(field_mismatches.items())),
        default_parser_first_malformed_row=default_malformed_row,
        default_parser_first_id_mismatch=default_id_mismatch,
    )
    return (
        csv_header,
        json_fields,
        observations,
        tuple(samples),
        structured_value_count,
        comparison,
    )


def inspect_pdl_files(
    csv_path: str | Path,
    json_path: str | Path,
    *,
    sample_size: int = 3,
    compare_records: int = DEFAULT_COMPARE_RECORDS,
    compute_sha256: bool = False,
) -> PDLInspection:
    """Inspect and compare bounded portions of both PDL company artifacts."""
    if sample_size <= 0:
        raise ValueError("sample_size must be greater than zero")
    if compare_records <= 0:
        raise ValueError("compare_records must be greater than zero")

    csv_file_path = Path(csv_path)
    json_file_path = Path(json_path)
    csv_file = _file_info(csv_file_path, compute_sha256=compute_sha256)
    json_file = _file_info(json_file_path, compute_sha256=compute_sha256)
    (
        csv_header,
        json_fields,
        observations,
        samples,
        structured_value_count,
        comparison,
    ) = _compare_files(
        csv_file_path,
        json_file_path,
        sample_size=sample_size,
        compare_records=compare_records,
    )
    probes, probe_mismatches = _probe_json_offsets(json_file_path)

    return PDLInspection(
        csv_file=csv_file,
        json_file=json_file,
        csv_header=csv_header,
        json_fields=json_fields,
        field_observations=observations,
        sample_records=samples,
        json_offset_probes=probes,
        json_offset_schema_mismatches=probe_mismatches,
        structured_value_count=structured_value_count,
        comparison=comparison,
    )


def _render_value(value: object, *, limit: int = 80) -> str:
    rendered = json.dumps(value, ensure_ascii=False)
    if len(rendered) <= limit:
        return rendered
    return f"{rendered[: limit - 1]}…"


def _render_hash(file_info: FileInfo) -> str:
    return file_info.sha256 or "not computed (use --sha256 for a full-file scan)"


def render_inspection(inspection: PDLInspection) -> str:
    """Render a bounded PDL inspection report for an operator."""
    comparison = inspection.comparison
    lines = [
        "CSV artifact",
        f"  path: {inspection.csv_file.path}",
        f"  size: {format_bytes(inspection.csv_file.byte_size)}",
        f"  SHA-256: {_render_hash(inspection.csv_file)}",
        "  parser: CSV with backslash escape character",
        f"  header: {', '.join(inspection.csv_header)}",
        f"  expected header: {'yes' if inspection.csv_header == EXPECTED_CSV_HEADER else 'NO'}",
        "",
        "JSON artifact",
        f"  path: {inspection.json_file.path}",
        f"  size: {format_bytes(inspection.json_file.byte_size)}",
        f"  SHA-256: {_render_hash(inspection.json_file)}",
        "  format: newline-delimited JSON objects",
        f"  fields: {', '.join(inspection.json_fields)}",
        (f"  expected fields: {'yes' if inspection.json_fields == EXPECTED_JSON_FIELDS else 'NO'}"),
        (
            f"  offset schema probes: {inspection.json_offset_probes} checked, "
            f"{inspection.json_offset_schema_mismatches} mismatch(es)"
        ),
        "",
        f"Observed JSON types (first {comparison.records_compared:,} records)",
    ]

    for observation in inspection.field_observations:
        types = ", ".join(observation.observed_types)
        lines.append(
            f"  {observation.name}: {types}; nulls "
            f"{observation.null_count:,}/{observation.observed_count:,}"
        )

    lines.extend(["", f"Sample JSON records ({len(inspection.sample_records)})"])
    for number, record in enumerate(inspection.sample_records, start=1):
        rendered_fields = ", ".join(
            f"{field}={_render_value(value)}" for field, value in record.items()
        )
        lines.append(f"  {number}: {rendered_fields}")

    mismatch_description = (
        ", ".join(f"{field}={count:,}" for field, count in comparison.field_mismatches)
        if comparison.field_mismatches
        else "none"
    )
    default_malformed_row = comparison.default_parser_first_malformed_row
    default_id_mismatch = comparison.default_parser_first_id_mismatch
    lines.extend(
        [
            "",
            "Representation comparison",
            f"  records compared: {comparison.records_compared:,}",
            f"  ID mismatches with vendor escape rule: {comparison.aligned_id_mismatches:,}",
            f"  duplicate JSON IDs: {comparison.duplicate_json_ids:,}",
            (
                "  malformed-width CSV rows with vendor escape rule: "
                f"{comparison.malformed_csv_rows:,}"
            ),
            f"  value mismatches after null/year normalization: {mismatch_description}",
            (
                f"  default CSV parser first malformed-width row: {default_malformed_row:,}"
                if default_malformed_row is not None
                else "  default CSV parser first malformed-width row: none in comparison window"
            ),
            (
                f"  default CSV parser first ID mismatch: {default_id_mismatch:,}"
                if default_id_mismatch is not None
                else "  default CSV parser first ID mismatch: none in comparison window"
            ),
            f"  structured JSON values observed: {inspection.structured_value_count:,}",
            "",
            "Import recommendation",
            "  preferred input: newline-delimited JSON",
            "  retain both files as original archived artifacts",
            (
                "  reason: JSON preserves scalar values that the CSV escape convention can "
                "represent ambiguously"
            ),
        ]
    )
    return "\n".join(lines)
