"""Strict codec and bounded persistence tests for private evidence artifacts."""

import json
import os
import stat
from pathlib import Path

import pytest

from effectprobe._evidence_artifact import (
    ArtifactCompatibilityError,
    ArtifactFormatError,
    ArtifactWriteError,
    CompatibilityDifference,
    JsonValue,
    artifact_payload,
    artifact_sha256,
    canonical_artifact_bytes,
    compatibility_differences,
    evidence_artifact_from_payload,
    read_evidence_artifact,
    require_bool,
    require_compatible,
    require_fields,
    require_int,
    require_list,
    require_nullable_string,
    require_object,
    require_string,
    write_evidence_artifact,
)


def _payload() -> dict[str, JsonValue]:
    return {
        "schema": {"name": "effectprobe.private.evidence", "version": 1},
        "producer": {},
        "replay": {},
        "compatibility": {},
        "scope": {},
        "contracts": {},
        "outcomes": {},
        "evidence": {},
        "provenance": {},
        "redaction": {},
        "reproduction": None,
    }


def _canonical_raw(payload: dict[str, JsonValue]) -> bytes:
    return (
        json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        )
        + "\n"
    ).encode()


def test_canonical_round_trip_is_frozen_hashed_exclusive_and_private(tmp_path: Path) -> None:
    payload = _payload()
    payload["scope"] = {"unicode": "ambigüidade", "values": [None, True, 3]}
    artifact = evidence_artifact_from_payload(payload)
    path = tmp_path / "artifact.json"

    written = write_evidence_artifact(path, artifact)
    read = read_evidence_artifact(path)

    assert read == written
    assert artifact_payload(read.artifact) == payload
    assert read.sha256 == artifact_sha256(artifact)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not tuple(tmp_path.glob(".artifact.json.*"))
    with pytest.raises(ArtifactWriteError, match="already exists"):
        write_evidence_artifact(path, artifact)
    assert path.read_bytes() == canonical_artifact_bytes(artifact)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"\xff", "UTF-8"),
        (b'{"schema":null,"schema":null}\n', "duplicate"),
        (b'{"value":NaN}\n', "non-finite"),
        (b"[]\n", "root"),
        (b"{}\n", "fields"),
    ],
)
def test_reader_rejects_invalid_json_and_envelopes(
    tmp_path: Path, data: bytes, message: str
) -> None:
    path = tmp_path / "invalid.json"
    path.write_bytes(data)

    with pytest.raises(ArtifactFormatError, match=message):
        read_evidence_artifact(path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unknown", "unknown"),
        ("missing", "missing"),
        ("schema_name", "schema name"),
        ("schema_version", "version"),
        ("producer_type", "producer"),
    ],
)
def test_reader_rejects_schema_drift(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    payload = _payload()
    if mutation == "unknown":
        payload["unknown"] = {}
    elif mutation == "missing":
        payload.pop("scope")
    elif mutation == "schema_name":
        cast_object(payload["schema"])["name"] = "other"
    elif mutation == "schema_version":
        cast_object(payload["schema"])["version"] = 2
    else:
        payload["producer"] = []
    path = tmp_path / "drift.json"
    path.write_bytes(_canonical_raw(payload))

    with pytest.raises(ArtifactFormatError, match=message):
        read_evidence_artifact(path)


def cast_object(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def test_reader_rejects_noncanonical_trailing_and_oversized_bytes(tmp_path: Path) -> None:
    artifact = evidence_artifact_from_payload(_payload())
    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text(json.dumps(_payload()), encoding="utf-8")
    trailing = tmp_path / "trailing.json"
    trailing.write_bytes(canonical_artifact_bytes(artifact) + b" ")
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * 1_048_577)

    with pytest.raises(ArtifactFormatError, match="canonical"):
        read_evidence_artifact(noncanonical)
    with pytest.raises(ArtifactFormatError, match=r"JSON|canonical"):
        read_evidence_artifact(trailing)
    with pytest.raises(ArtifactFormatError, match="1 MiB"):
        read_evidence_artifact(oversized)


def test_reader_rejects_missing_symlink_and_nonregular_paths(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(canonical_artifact_bytes(evidence_artifact_from_payload(_payload())))
    link = tmp_path / "link.json"
    link.symlink_to(target)

    with pytest.raises(ArtifactFormatError, match="inspected"):
        read_evidence_artifact(tmp_path / "missing.json")
    with pytest.raises(ArtifactFormatError, match="regular"):
        read_evidence_artifact(link)
    with pytest.raises(ArtifactFormatError, match="regular"):
        read_evidence_artifact(tmp_path)


def test_writer_requires_existing_real_parent_and_cleans_temporary_files(tmp_path: Path) -> None:
    artifact = evidence_artifact_from_payload(_payload())
    missing = tmp_path / "missing" / "artifact.json"
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(ArtifactWriteError, match="parent"):
        write_evidence_artifact(missing, artifact)
    with pytest.raises(ArtifactWriteError, match="non-symlink"):
        write_evidence_artifact(linked / "artifact.json", artifact)
    assert not tuple(tmp_path.rglob(".artifact.json.*"))


def test_in_memory_limits_reject_deep_wide_long_and_large_values() -> None:
    deep: JsonValue = None
    for _ in range(34):
        deep = [deep]
    wide_list: list[JsonValue] = [None for _ in range(257)]
    cases: list[tuple[JsonValue, str]] = [
        (deep, "nesting"),
        (wide_list, "too many"),
        ({str(index): None for index in range(257)}, "too many"),
        ("x" * 65_537, "too long"),
        (2**63, "64-bit"),
    ]
    for value, message in cases:
        payload = _payload()
        payload["scope"] = {"value": value}
        with pytest.raises(ArtifactFormatError, match=message):
            evidence_artifact_from_payload(payload)

    payload = _payload()
    payload["scope"] = {"values": ["x" * 65_536 for _ in range(17)]}
    with pytest.raises(ArtifactFormatError, match=r"1 MiB|string is too long"):
        canonical_artifact_bytes(evidence_artifact_from_payload(payload))


def test_schema_requirement_helpers_reject_wrong_types_and_fields() -> None:
    assert require_object({}, "value") == {}
    assert require_list([], "value") == []
    assert require_string("x", "value") == "x"
    assert require_bool(True, "value")
    assert require_int(1, "value") == 1
    assert require_nullable_string(None, "value") is None
    assert require_nullable_string("x", "value") == "x"
    require_fields({"x": 1}, frozenset({"x"}), "value")

    for call in (
        lambda: require_object([], "value"),
        lambda: require_list({}, "value"),
        lambda: require_string(1, "value"),
        lambda: require_bool(1, "value"),
        lambda: require_int(True, "value"),
        lambda: require_nullable_string(1, "value"),
        lambda: require_fields({"x": 1}, frozenset({"y"}), "value"),
    ):
        with pytest.raises(ArtifactFormatError):
            call()


def test_compatibility_diff_is_complete_deterministic_and_safe() -> None:
    recorded: dict[str, JsonValue] = {
        "same": 1,
        "changed": "before",
        "nested": {"removed": True},
        "array": [1, 2],
    }
    current: dict[str, JsonValue] = {
        "same": 1,
        "changed": "after",
        "nested": {"added": False},
        "array": [1],
    }

    differences = compatibility_differences(recorded, current)

    assert tuple(item.path for item in differences) == (
        "compatibility.array[1]",
        "compatibility.changed",
        "compatibility.nested.added",
        "compatibility.nested.removed",
    )
    with pytest.raises(ArtifactCompatibilityError) as raised:
        require_compatible(recorded, current)
    assert raised.value.differences == differences
    assert CompatibilityDifference("path", "left", "right").path == "path"
    require_compatible(recorded, recorded)


def test_writer_reports_unpublishable_destination(tmp_path: Path) -> None:
    artifact = evidence_artifact_from_payload(_payload())
    destination = tmp_path / "directory"
    destination.mkdir()

    with pytest.raises(ArtifactWriteError, match="exists"):
        write_evidence_artifact(destination, artifact)
    assert os.listdir(tmp_path) == ["directory"]
