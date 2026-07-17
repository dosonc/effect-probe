"""Private MCP evidence capture, inspection, compatibility, and replay tests."""

import copy
import json
import os
import stat
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import pytest

from effectprobe import __all__
from effectprobe._evidence_artifact import (
    ArtifactCompatibilityError,
    ArtifactFormatError,
    ArtifactWriteError,
    JsonValue,
    artifact_payload,
    evidence_artifact_from_payload,
    write_evidence_artifact,
)
from effectprobe._mcp_evidence_replay import (
    EvidenceInspection,
    McpEvidenceCaptureError,
    McpEvidenceMode,
    build_mcp_compatibility,
    canonical_evaluative_projection,
    capture_mcp_evidence,
    inspect_mcp_evidence,
    read_mcp_evidence,
    record_mcp_evidence,
    replay_mcp_evidence,
    validate_mcp_artifact,
)
from effectprobe._mcp_refund_comparison import (
    McpRefundCaseResult,
    McpRefundWorldTracker,
    build_mcp_refund_case,
)
from effectprobe._semantic_kernel import AxisStatus, ErrorRecord, evaluate_case


@dataclass(frozen=True, slots=True)
class _Evaluation:
    result: McpRefundCaseResult
    tracker: McpRefundWorldTracker


@dataclass(frozen=True, slots=True)
class _Recorded:
    keyed: Path
    unsafe: Path
    keyed_evaluation: _Evaluation
    unsafe_evaluation: _Evaluation


@pytest.fixture(scope="module")
def keyed_evaluation() -> _Evaluation:
    case, tracker = build_mcp_refund_case(keyed=True)
    return _Evaluation(evaluate_case(case), tracker)


@pytest.fixture(scope="module")
def unsafe_evaluation() -> _Evaluation:
    case, tracker = build_mcp_refund_case(keyed=False)
    return _Evaluation(evaluate_case(case), tracker)


@pytest.fixture(scope="module")
def recorded(
    tmp_path_factory: pytest.TempPathFactory,
    keyed_evaluation: _Evaluation,
    unsafe_evaluation: _Evaluation,
) -> _Recorded:
    directory = tmp_path_factory.mktemp("recorded-evidence")
    keyed = directory / "keyed.json"
    unsafe = directory / "unsafe.json"
    write_evidence_artifact(
        keyed,
        capture_mcp_evidence(
            mode="keyed",
            result=keyed_evaluation.result,
            tracker=keyed_evaluation.tracker,
        ),
    )
    write_evidence_artifact(
        unsafe,
        capture_mcp_evidence(
            mode="unsafe",
            result=unsafe_evaluation.result,
            tracker=unsafe_evaluation.tracker,
        ),
    )
    return _Recorded(keyed, unsafe, keyed_evaluation, unsafe_evaluation)


def _object(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _array(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _canonical_raw(payload: dict[str, JsonValue]) -> bytes:
    return (
        json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        )
        + "\n"
    ).encode()


def _write_payload(path: Path, payload: dict[str, JsonValue]) -> None:
    artifact = evidence_artifact_from_payload(payload, validator=validate_mcp_artifact)
    write_evidence_artifact(path, artifact)


def test_keyed_and_unsafe_artifacts_preserve_outcomes_confirmation_and_cleanup(
    recorded: _Recorded,
) -> None:
    keyed = inspect_mcp_evidence(recorded.keyed)
    unsafe = inspect_mcp_evidence(recorded.unsafe)

    assert keyed.clean_validity == keyed.retry_safety == "PASS"
    assert keyed.confirmation_count == 0
    assert keyed.trial_count == 2
    assert keyed.committed_effect_count == 2
    assert keyed.transport_delivery_count == 3
    assert unsafe.clean_validity == "PASS"
    assert unsafe.retry_safety == "FAIL"
    assert unsafe.confirmation_count == 2
    assert unsafe.trial_count == 6
    assert unsafe.committed_effect_count == 9
    assert unsafe.transport_delivery_count == 9
    assert set((*keyed.cleanup_dispositions, *unsafe.cleanup_dispositions)) == {"pass"}
    assert unsafe.reproduction_matched is None


def test_artifact_partitions_lost_result_and_identity_truth(recorded: _Recorded) -> None:
    payload = artifact_payload(read_mcp_evidence(recorded.keyed).artifact)
    evidence = _object(payload["evidence"])
    primary = _object(evidence["primary"])
    clean = _object(primary["clean"])
    perturbed = _object(primary["perturbed"])
    attempts = _array(perturbed["attempts"])
    first = _object(attempts[0])
    first_view = _object(first["subject_view"])
    second_view = _object(_object(attempts[1])["subject_view"])
    harness = _object(perturbed["harness_truth"])

    assert first_view["outcome"] == "client_result_lost"
    assert first_view["returned_result"] is None
    assert second_view["outcome"] == "returned"
    assert second_view["returned_result"] == harness["undelivered_result"]
    assert "observer_truth" in first
    assert "harness_truth" not in first_view
    assert "transport_truth" not in first_view
    assert len(_array(clean["transport_truth"])) == 1
    assert len(_array(perturbed["transport_truth"])) == 2
    for delivery_value in _array(perturbed["transport_truth"]):
        delivery = _object(delivery_value)
        assert set(delivery) == {"ordinal", "mcp_request_id", "operation_key"}


def test_artifact_contains_all_pair_level_candidate_and_confirmation_evidence(
    recorded: _Recorded,
) -> None:
    payload = artifact_payload(read_mcp_evidence(recorded.unsafe).artifact)
    evidence = _object(payload["evidence"])
    primary = _object(evidence["primary"])
    confirmations = _array(evidence["confirmations"])

    primary_retry = _object(_array(primary["retry_results"])[0])
    assert primary_retry["candidate"] is True
    assert primary_retry["verdict"] == "INCONCLUSIVE"
    assert len(confirmations) == 2
    for confirmation_value in confirmations:
        confirmation = _object(confirmation_value)
        retry = _object(_array(confirmation["retry_results"])[0])
        assert retry["candidate"] is True
        assert retry["verdict"] == "INCONCLUSIVE"
        assert confirmation["clean"] is not None
        assert confirmation["perturbed"] is not None
    outcomes = _object(payload["outcomes"])
    final_retry = _object(_array(_object(outcomes["retry_safety"])["invariants"])[0])
    assert final_retry["candidate"] is False
    assert final_retry["verdict"] == "FAIL"


def test_artifact_is_deterministic_redacted_and_not_public(recorded: _Recorded) -> None:
    second = capture_mcp_evidence(
        mode="keyed",
        result=recorded.keyed_evaluation.result,
        tracker=recorded.keyed_evaluation.tracker,
    )
    first = read_mcp_evidence(recorded.keyed)
    assert artifact_payload(first.artifact) == artifact_payload(second)
    content = recorded.keyed.read_text(encoding="utf-8")
    assert os.fspath(sys.executable) not in content
    for world in recorded.keyed_evaluation.tracker.worlds:
        assert os.fspath(world.database_path) not in content
        for delivery in world.deliveries:
            assert str(delivery.process_id) not in content
    assert __all__ == ("__version__",)


def test_error_messages_are_redacted_and_forbidden_value_scan_fails_closed(
    keyed_evaluation: _Evaluation,
) -> None:
    secret = "SECRET-CANARY-exception-message"
    primary = replace(
        keyed_evaluation.result.primary,
        errors=(ErrorRecord("retry", "cleanup", "SecretError", secret),),
    )
    result = replace(keyed_evaluation.result, primary=primary)

    artifact = capture_mcp_evidence(mode="keyed", result=result, tracker=keyed_evaluation.tracker)
    text = str(artifact_payload(artifact))

    assert secret not in text
    assert "SecretError" in text
    assert "message_redacted" in text
    with pytest.raises(McpEvidenceCaptureError, match="forbidden"):
        capture_mcp_evidence(
            mode="keyed",
            result=keyed_evaluation.result,
            tracker=keyed_evaluation.tracker,
            forbidden_values=(_PAYMENT_CANARY,),
        )


_PAYMENT_CANARY = "payment/refund-001"


@pytest.mark.parametrize(
    "failure",
    ["missing_world", "not_cleaned", "protocol", "scope", "explanation", "lineage"],
)
def test_capture_refuses_incomplete_unknown_or_inconsistent_evidence(
    keyed_evaluation: _Evaluation, failure: str
) -> None:
    result = keyed_evaluation.result
    tracker = copy.deepcopy(keyed_evaluation.tracker)
    if failure == "missing_world":
        tracker.worlds.pop()
    elif failure == "not_cleaned":
        tracker.worlds[0].cleaned = False
    elif failure == "protocol":
        tracker.preflight_protocol_version = "other"
    elif failure == "scope":
        result = replace(result, scope=replace(result.scope, reportable=True))
    elif failure == "explanation":
        invariant = replace(
            result.clean_validity.invariants[0], explanation="SECRET unregistered explanation"
        )
        result = replace(
            result,
            clean_validity=replace(result.clean_validity, invariants=(invariant,)),
        )
    else:
        with pytest.raises(McpEvidenceCaptureError):
            capture_mcp_evidence(
                mode="keyed",
                result=result,
                tracker=tracker,
                lineage="verified_replay",
            )
        return

    with pytest.raises(McpEvidenceCaptureError):
        capture_mcp_evidence(
            mode="keyed",
            result=result,
            tracker=tracker,
        )


def _mutate_compatibility(payload: dict[str, JsonValue], dimension: str) -> None:
    compatibility = _object(payload["compatibility"])
    if dimension == "schema":
        _object(compatibility["schema"])["version"] = 2
    elif dimension == "redaction":
        _object(compatibility["redaction"])["version"] = 2
        _object(payload["redaction"])["version"] = 2
    elif dimension == "case":
        _object(compatibility["case"])["version"] = 2
    elif dimension == "subject":
        component = _object(_array(_object(compatibility["subject"])["components"])[0])
        component["sha256"] = "0" * 64
    elif dimension == "runner":
        _object(compatibility["runner"])["version"] = 2
        _object(payload["producer"])["version"] = 2
    elif dimension == "lock":
        _object(compatibility["dependencies"])["lock_sha256"] = "0" * 64
    elif dimension == "distribution":
        distributions = _array(
            _object(compatibility["dependencies"])["installed_locked_distributions"]
        )
        _object(distributions[0])["version"] = "0"
    elif dimension == "runtime":
        _object(compatibility["runtime"])["python_version"] = "0"
    elif dimension == "input":
        _object(compatibility["input"])["amount_minor_units"] = 1
        _object(_object(payload["scope"])["input"])["amount_minor_units"] = 1
    elif dimension == "fixture":
        contracts = _object(compatibility["contracts"])
        _object(contracts["fixture"])["version"] = 2
        _object(_object(payload["contracts"])["fixture"])["version"] = 2
    elif dimension == "observer":
        _object(compatibility["observer"])["complete_history"] = False
        _object(_object(payload["scope"])["observer_coverage"])["complete_history"] = False
    elif dimension == "schedule":
        _object(compatibility["schedule"])["attempt_count"] = 3
    else:
        contracts = _object(compatibility["contracts"])
        _object(contracts["mcp_tool"])["name"] = "other"
        _object(_object(payload["contracts"])["mcp_tool"])["name"] = "other"


@pytest.mark.parametrize(
    "dimension",
    [
        "schema",
        "redaction",
        "case",
        "subject",
        "runner",
        "lock",
        "distribution",
        "runtime",
        "input",
        "fixture",
        "observer",
        "schedule",
        "mcp_tool",
    ],
)
def test_replay_refuses_every_compatibility_dimension_before_execution(
    tmp_path: Path, recorded: _Recorded, dimension: str
) -> None:
    payload = artifact_payload(read_mcp_evidence(recorded.keyed).artifact)
    _mutate_compatibility(payload, dimension)
    source = tmp_path / f"{dimension}.json"
    destination = tmp_path / "child.json"
    _write_payload(source, payload)
    calls: list[McpEvidenceMode] = []

    def should_not_run(mode: McpEvidenceMode) -> tuple[McpRefundCaseResult, McpRefundWorldTracker]:
        calls.append(mode)
        raise AssertionError("incompatible evidence must not execute")

    with pytest.raises(ArtifactCompatibilityError) as raised:
        replay_mcp_evidence(source, destination, run=should_not_run)

    assert raised.value.differences
    assert calls == []
    assert not destination.exists()


@pytest.mark.parametrize("field", ["registry", "replayable"])
def test_replay_refuses_unknown_or_nonreplayable_registry(
    tmp_path: Path, recorded: _Recorded, field: str
) -> None:
    payload = artifact_payload(read_mcp_evidence(recorded.keyed).artifact)
    replay = _object(payload["replay"])
    if field == "registry":
        replay["registry_id"] = "unknown"
        _object(_object(payload["compatibility"])["case"])["registry_id"] = "unknown"
    else:
        replay["replayable"] = False
    source = tmp_path / f"{field}.json"
    _write_payload(source, payload)

    with pytest.raises(ArtifactCompatibilityError) as raised:
        replay_mcp_evidence(source, tmp_path / "child.json")

    assert raised.value.differences[0].path


def test_schema_and_structural_drift_are_format_refusals(
    tmp_path: Path, recorded: _Recorded
) -> None:
    payload = artifact_payload(read_mcp_evidence(recorded.keyed).artifact)
    cases: list[tuple[str, dict[str, JsonValue]]] = []
    schema = copy.deepcopy(payload)
    _object(schema["schema"])["version"] = 2
    cases.append(("schema", schema))
    unknown = copy.deepcopy(payload)
    primary = _object(_object(unknown["evidence"])["primary"])
    primary["unknown"] = True
    cases.append(("unknown", unknown))
    wrong_type = copy.deepcopy(payload)
    clean = _object(_object(_object(wrong_type["evidence"])["primary"])["clean"])
    _object(clean["cleanup"])["resource_closed"] = 1
    cases.append(("type", wrong_type))
    lineage = copy.deepcopy(payload)
    _object(lineage["replay"])["source_sha256"] = "0" * 64
    cases.append(("lineage", lineage))
    scope = copy.deepcopy(payload)
    _object(scope["scope"])["schedule"] = "other"
    cases.append(("scope", scope))
    contracts = copy.deepcopy(payload)
    _object(_object(contracts["contracts"])["fixture"])["version"] = 2
    cases.append(("contracts", contracts))

    for name, value in cases:
        path = tmp_path / f"{name}.json"
        path.write_bytes(_canonical_raw(value))
        with pytest.raises(ArtifactFormatError):
            read_mcp_evidence(path)


def test_compatible_keyed_and_unsafe_replays_record_lineage_and_match(
    tmp_path: Path, recorded: _Recorded
) -> None:
    keyed_child = tmp_path / "keyed-child.json"
    unsafe_child = tmp_path / "unsafe-child.json"

    keyed = replay_mcp_evidence(recorded.keyed, keyed_child)
    unsafe = replay_mcp_evidence(recorded.unsafe, unsafe_child)

    assert keyed.matched and unsafe.matched
    for source, child, replay in (
        (recorded.keyed, keyed_child, keyed),
        (recorded.unsafe, unsafe_child, unsafe),
    ):
        inspection = inspect_mcp_evidence(child)
        assert inspection.lineage == "verified_replay"
        assert inspection.source_sha256 == read_mcp_evidence(source).sha256
        assert inspection.reproduction_matched is True
        assert stat.S_IMODE(child.stat().st_mode) == 0o600
        assert replay.child.sha256 == inspection.sha256


def test_compatible_nonreproduction_preserves_new_evidence(
    tmp_path: Path, recorded: _Recorded
) -> None:
    def changed_run(
        mode: McpEvidenceMode,
    ) -> tuple[McpRefundCaseResult, McpRefundWorldTracker]:
        assert mode == "keyed"
        original = recorded.keyed_evaluation.result
        changed = replace(
            original,
            clean_validity=replace(original.clean_validity, status=AxisStatus.ERROR),
        )
        return changed, recorded.keyed_evaluation.tracker

    child = tmp_path / "different.json"
    replay = replay_mcp_evidence(recorded.keyed, child, run=changed_run)
    inspection = inspect_mcp_evidence(child)

    assert not replay.matched
    assert inspection.reproduction_matched is False
    assert inspection.clean_validity == "ERROR"
    assert inspect_mcp_evidence(recorded.keyed).clean_validity == "PASS"


def test_replay_failure_existing_destination_and_same_path_leave_source_unchanged(
    tmp_path: Path, recorded: _Recorded
) -> None:
    source_before = recorded.keyed.read_bytes()
    destination = tmp_path / "existing.json"
    destination.write_text("owned", encoding="utf-8")

    def fail(_mode: McpEvidenceMode) -> tuple[McpRefundCaseResult, McpRefundWorldTracker]:
        raise RuntimeError("configured replay preflight failure")

    with pytest.raises(RuntimeError, match="preflight"):
        replay_mcp_evidence(recorded.keyed, tmp_path / "absent.json", run=fail)
    assert not (tmp_path / "absent.json").exists()
    with pytest.raises(ArtifactWriteError, match="already exists"):
        replay_mcp_evidence(
            recorded.keyed,
            destination,
            run=lambda _mode: (
                recorded.keyed_evaluation.result,
                recorded.keyed_evaluation.tracker,
            ),
        )
    assert destination.read_text(encoding="utf-8") == "owned"
    with pytest.raises(ArtifactFormatError, match="differ"):
        replay_mcp_evidence(recorded.keyed, recorded.keyed)
    assert recorded.keyed.read_bytes() == source_before


def test_record_workflow_writes_and_refuses_existing_destination(tmp_path: Path) -> None:
    path = tmp_path / "recorded.json"
    second_path = tmp_path / "recorded-again.json"

    written = record_mcp_evidence("keyed", path)
    second = record_mcp_evidence("keyed", second_path)

    assert read_mcp_evidence(path) == written
    assert path.read_bytes() == second_path.read_bytes()
    assert written.sha256 == second.sha256
    with pytest.raises(ArtifactWriteError, match="already exists"):
        record_mcp_evidence("keyed", path)


def test_live_compatibility_is_complete_and_mode_specific() -> None:
    unsafe = build_mcp_compatibility("unsafe")
    keyed = build_mcp_compatibility("keyed")

    assert unsafe.keys() == keyed.keys()
    assert _object(unsafe["subject"])["id"] == "mcp_unsafe_refund_tool"
    assert _object(keyed["subject"])["id"] == "mcp_keyed_refund_tool"
    assert _object(unsafe["runtime"])["mcp_protocol_revision"] == "2025-11-25"
    assert _object(unsafe["dependencies"])["installed_locked_distributions"]
    with pytest.raises(McpEvidenceCaptureError, match="unsupported"):
        build_mcp_compatibility(cast("McpEvidenceMode", "other"))


def test_projection_removes_generated_transport_and_refund_values(recorded: _Recorded) -> None:
    read = read_mcp_evidence(recorded.keyed)
    projection = canonical_evaluative_projection(read.artifact)
    text = str(projection)
    payload = artifact_payload(read.artifact)
    primary = _object(_object(payload["evidence"])["primary"])
    perturbed = _object(primary["perturbed"])
    delivery = _object(_array(perturbed["transport_truth"])[0])

    raw_request = cast("str", delivery["mcp_request_id"])
    assert f"'mcp_request_id': '{raw_request}'" not in text
    assert "transport_request_ids_distinct" in text
    assert "generated-refund-identity/1" in text


def test_safe_inspection_is_frozen_and_does_not_execute(recorded: _Recorded) -> None:
    summary = inspect_mcp_evidence(recorded.unsafe)

    assert isinstance(summary, EvidenceInspection)
    assert summary.mode == "unsafe"
    assert summary.omitted_categories == (
        "absolute_paths",
        "command_lines",
        "environment_names_and_values",
        "exception_messages_and_tracebacks",
        "process_identifiers",
        "subprocess_stderr",
    )
    with pytest.raises(AttributeError):
        summary.retry_safety = "PASS"  # type: ignore[misc]


def test_validator_rejects_wrong_receipt_outcome_and_bad_reproduction(
    tmp_path: Path, recorded: _Recorded
) -> None:
    payload = artifact_payload(read_mcp_evidence(recorded.keyed).artifact)
    evidence = _object(payload["evidence"])
    perturbed = _object(_object(evidence["primary"])["perturbed"])
    first_view = _object(_object(_array(perturbed["attempts"])[0])["subject_view"])
    first_view["returned_result"] = copy.deepcopy(perturbed["subject_result"])
    path = tmp_path / "contradiction.json"
    path.write_bytes(_canonical_raw(payload))
    with pytest.raises(ArtifactFormatError, match="contradicts"):
        read_mcp_evidence(path)

    payload = artifact_payload(read_mcp_evidence(recorded.keyed).artifact)
    payload["reproduction"] = {"projection_version": 1, "matched": True}
    path = tmp_path / "bad-reproduction.json"
    path.write_bytes(_canonical_raw(payload))
    with pytest.raises(ArtifactFormatError, match="recording"):
        read_mcp_evidence(path)
