"""Private bounded reports for the registered controlled MCP refund evidence."""

import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Never, cast

from effectprobe._evidence_artifact import (
    EvidenceArtifact,
    EvidenceArtifactError,
    JsonValue,
    ReadEvidenceArtifact,
    artifact_payload,
    artifact_sha256,
    canonical_artifact_bytes,
    require_bool,
    require_int,
    require_list,
    require_nullable_string,
    require_object,
    require_string,
)
from effectprobe._mcp_evidence_replay import (
    McpEvidenceCaptureError,
    build_mcp_compatibility,
    read_mcp_evidence,
)

type McpReportMode = Literal["unsafe", "keyed"]
type ReportVerdict = Literal["PASS", "FAIL", "INCONCLUSIVE", "ERROR"]
type ReportAxisStatus = ReportVerdict | Literal["UNVERIFIED"]
type ReportAxisName = Literal["clean_validity", "retry_safety"]
type ReportTrialKind = Literal["clean", "perturbed"]

_REPORT_SCHEMA_NAME = "effectprobe.private.report"
_REPORT_SCHEMA_VERSION = 1
_EVIDENCE_SCHEMA_NAME = "effectprobe.private.evidence"
_EVIDENCE_SCHEMA_VERSION = 1
_RUNNER_ID = "effectprobe.private.mcp-evidence-replay"
_REPORTABLE_RUNNER_VERSION = 2
_REGISTRY_ID = "effectprobe.private.mcp-refund"
_CASE_VERSION = 1
_REDACTION_ID = "effectprobe.private.redaction/mcp-refund-v1"
_REDACTION_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}")
_EXPECTED_PROVIDER = "harness-controlled SQLite MCP refund fixture"
_EXPECTED_LIMITATIONS = (
    "private provisional MCP stdio outcome",
    "trusted local harness-controlled server and SQLite provider",
    "MCP request identity is transport evidence, not a domain operation key",
    "private experimental evidence schema and replay registry",
    "compatibility covers only the enumerated runtime and environment dimensions",
    "unmodelled host state remains unknown",
)
_EXPECTED_OMITTED_CATEGORIES = (
    "absolute_paths",
    "command_lines",
    "environment_names_and_values",
    "exception_messages_and_tracebacks",
    "process_identifiers",
    "subprocess_stderr",
)
_REPORT_LIMITATION = "private experimental terminal, JSON, and JUnit report contract"


class McpReportInputError(RuntimeError):
    """A private artifact cannot support the bounded report contract."""


class McpReportRenderingError(RuntimeError):
    """A validated private report model could not be serialized."""


@dataclass(frozen=True, slots=True)
class ReportComponent:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class ReportDistribution:
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class ReportRuntime:
    python_implementation: str
    python_version: str
    operating_system: str
    operating_system_release: str
    architecture: str
    sqlite_version: str
    mcp_protocol_revision: str


@dataclass(frozen=True, slots=True)
class ReportCompatibility:
    evidence_schema_name: str
    evidence_schema_version: int
    producer_id: str
    producer_version: int
    case_registry_id: str
    case_version: int
    subject_components: tuple[ReportComponent, ...]
    runner_components: tuple[ReportComponent, ...]
    dependency_lock_sha256: str
    installed_locked_distributions: tuple[ReportDistribution, ...]
    runtime: ReportRuntime


@dataclass(frozen=True, slots=True)
class ReportRequirement:
    kind: str
    surface: str | None


@dataclass(frozen=True, slots=True)
class ReportNamedContract:
    name: str
    requirements: tuple[ReportRequirement, ...]


@dataclass(frozen=True, slots=True)
class ReportContract:
    fixture_id: str
    fixture_version: int
    fixture_seed_payment_id: str
    fixture_seed_payment_minor_units: int
    fixture_seed_refunded_minor_units: int
    fixture_seed_key_mapping_count: int
    fixture_seed_history_count: int
    clean_assertions: tuple[ReportNamedContract, ...]
    retry_invariants: tuple[ReportNamedContract, ...]
    state_canonicalization: str
    history_canonicalization: str
    mcp_tool_name: str


@dataclass(frozen=True, slots=True)
class ReportObserver:
    surface: str
    state: bool
    history: bool
    complete_history: bool
    observation_interval: str
    provenance: str
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReportScope:
    subject_name: str
    mode: McpReportMode
    payment_id: str
    amount_minor_units: int
    logical_operation_id: str
    operation_key: str | None
    schedule: str
    observer: ReportObserver
    contract: ReportContract


@dataclass(frozen=True, slots=True)
class ReportInvariant:
    name: str
    verdict: ReportVerdict
    explanation: str
    missing_evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReportAxis:
    status: ReportAxisStatus
    invariants: tuple[ReportInvariant, ...]


@dataclass(frozen=True, slots=True)
class ReportTrial:
    pair_id: str
    kind: ReportTrialKind
    trial_id: str
    history_delta_count: int
    attempt_count: int
    transport_delivery_count: int
    cleanup_status: str
    resource_closed: bool
    database_removed: bool
    fault_boundary: str | None = None
    fault_reached: bool = False
    injected_attempt_ordinal: int | None = None


@dataclass(frozen=True, slots=True)
class McpReport:
    source_sha256: str
    source_size_bytes: int
    lineage: str
    source_artifact_sha256: str | None
    reproduction_matched: bool | None
    compatibility: ReportCompatibility
    scope: ReportScope
    clean_validity: ReportAxis
    retry_safety: ReportAxis
    applicable_pair_count: int
    confirmation_count: int
    trials: tuple[ReportTrial, ...]
    omitted_categories: tuple[str, ...]
    limitations: tuple[str, ...]


def _reject(message: str) -> Never:
    raise McpReportInputError(message)


def _expect(condition: bool, message: str) -> None:
    if not condition:
        _reject(message)


def _sha256(value: JsonValue, path: str) -> str:
    result = require_string(value, path)
    _expect(_SHA256.fullmatch(result) is not None, f"{path} is not a SHA-256 digest")
    return result


def _strings(value: JsonValue, path: str) -> tuple[str, ...]:
    return tuple(
        require_string(item, f"{path}[{index}]")
        for index, item in enumerate(require_list(value, path))
    )


def _components(value: JsonValue, path: str) -> tuple[ReportComponent, ...]:
    result: list[ReportComponent] = []
    for index, item_value in enumerate(require_list(value, path)):
        item_path = f"{path}[{index}]"
        item = require_object(item_value, item_path)
        component_path = require_string(item["path"], f"{item_path}.path")
        _expect(not Path(component_path).is_absolute(), f"{item_path}.path must be relative")
        _expect(".." not in Path(component_path).parts, f"{item_path}.path escapes the repository")
        result.append(
            ReportComponent(
                path=component_path,
                sha256=_sha256(item["sha256"], f"{item_path}.sha256"),
            )
        )
    _expect(bool(result), f"{path} must not be empty")
    _expect(len({item.path for item in result}) == len(result), f"{path} contains duplicates")
    return tuple(result)


def _distributions(value: JsonValue, path: str) -> tuple[ReportDistribution, ...]:
    result: list[ReportDistribution] = []
    for index, item_value in enumerate(require_list(value, path)):
        item_path = f"{path}[{index}]"
        item = require_object(item_value, item_path)
        result.append(
            ReportDistribution(
                name=require_string(item["name"], f"{item_path}.name"),
                version=require_string(item["version"], f"{item_path}.version"),
            )
        )
    _expect(bool(result), f"{path} must not be empty")
    _expect(
        tuple(sorted(result, key=lambda item: item.name)) == tuple(result), f"{path} is unsorted"
    )
    _expect(len({item.name for item in result}) == len(result), f"{path} contains duplicates")
    return tuple(result)


def _requirements(value: JsonValue, path: str) -> tuple[ReportRequirement, ...]:
    result: list[ReportRequirement] = []
    for index, item_value in enumerate(require_list(value, path)):
        item_path = f"{path}[{index}]"
        item = require_object(item_value, item_path)
        result.append(
            ReportRequirement(
                kind=require_string(item["kind"], f"{item_path}.kind"),
                surface=require_nullable_string(item["surface"], f"{item_path}.surface"),
            )
        )
    _expect(bool(result), f"{path} must not be empty")
    return tuple(result)


def _named_contracts(value: JsonValue, path: str) -> tuple[ReportNamedContract, ...]:
    result: list[ReportNamedContract] = []
    for index, item_value in enumerate(require_list(value, path)):
        item_path = f"{path}[{index}]"
        item = require_object(item_value, item_path)
        result.append(
            ReportNamedContract(
                name=require_string(item["name"], f"{item_path}.name"),
                requirements=_requirements(item["requirements"], f"{item_path}.requirements"),
            )
        )
    _expect(bool(result), f"{path} must not be empty")
    return tuple(result)


def _contract(value: JsonValue) -> ReportContract:
    contracts = require_object(value, "artifact.contracts")
    fixture = require_object(contracts["fixture"], "artifact.contracts.fixture")
    seed = require_object(fixture["seed"], "artifact.contracts.fixture.seed")
    canonicalization = require_object(
        contracts["canonicalization"], "artifact.contracts.canonicalization"
    )
    tool = require_object(contracts["mcp_tool"], "artifact.contracts.mcp_tool")
    return ReportContract(
        fixture_id=require_string(fixture["id"], "artifact.contracts.fixture.id"),
        fixture_version=require_int(fixture["version"], "artifact.contracts.fixture.version"),
        fixture_seed_payment_id=require_string(
            seed["payment_id"], "artifact.contracts.fixture.seed.payment_id"
        ),
        fixture_seed_payment_minor_units=require_int(
            seed["payment_minor_units"],
            "artifact.contracts.fixture.seed.payment_minor_units",
        ),
        fixture_seed_refunded_minor_units=require_int(
            seed["refunded_minor_units"],
            "artifact.contracts.fixture.seed.refunded_minor_units",
        ),
        fixture_seed_key_mapping_count=require_int(
            seed["key_mapping_count"],
            "artifact.contracts.fixture.seed.key_mapping_count",
        ),
        fixture_seed_history_count=len(
            require_list(seed["history"], "artifact.contracts.fixture.seed.history")
        ),
        clean_assertions=_named_contracts(
            contracts["clean_assertions"], "artifact.contracts.clean_assertions"
        ),
        retry_invariants=_named_contracts(
            contracts["retry_invariants"], "artifact.contracts.retry_invariants"
        ),
        state_canonicalization=require_string(
            canonicalization["state"], "artifact.contracts.canonicalization.state"
        ),
        history_canonicalization=require_string(
            canonicalization["history"], "artifact.contracts.canonicalization.history"
        ),
        mcp_tool_name=require_string(tool["name"], "artifact.contracts.mcp_tool.name"),
    )


def _observer(value: JsonValue) -> ReportObserver:
    observer = require_object(value, "artifact.scope.observer_coverage")
    return ReportObserver(
        surface=require_string(observer["surface"], "artifact.scope.observer_coverage.surface"),
        state=require_bool(observer["state"], "artifact.scope.observer_coverage.state"),
        history=require_bool(observer["history"], "artifact.scope.observer_coverage.history"),
        complete_history=require_bool(
            observer["complete_history"], "artifact.scope.observer_coverage.complete_history"
        ),
        observation_interval=require_string(
            observer["observation_interval"],
            "artifact.scope.observer_coverage.observation_interval",
        ),
        provenance=require_string(
            observer["provenance"], "artifact.scope.observer_coverage.provenance"
        ),
        limitations=_strings(
            observer["limitations"], "artifact.scope.observer_coverage.limitations"
        ),
    )


def _verdict(value: JsonValue, path: str) -> ReportVerdict:
    result = require_string(value, path)
    if result == "PASS":
        return "PASS"
    if result == "FAIL":
        return "FAIL"
    if result == "INCONCLUSIVE":
        return "INCONCLUSIVE"
    if result == "ERROR":
        return "ERROR"
    _reject(f"{path} is unsupported")


def _axis_status(value: JsonValue, path: str) -> ReportAxisStatus:
    result = require_string(value, path)
    if result == "UNVERIFIED":
        return "UNVERIFIED"
    return _verdict(result, path)


def _invariant(value: JsonValue, path: str, *, final: bool) -> ReportInvariant:
    item = require_object(value, path)
    if final:
        _expect(not require_bool(item["candidate"], f"{path}.candidate"), f"{path} is unresolved")
    return ReportInvariant(
        name=require_string(item["name"], f"{path}.name"),
        verdict=_verdict(item["verdict"], f"{path}.verdict"),
        explanation=require_string(item["explanation"], f"{path}.explanation"),
        missing_evidence=_strings(item["missing_evidence"], f"{path}.missing_evidence"),
    )


def _axis(value: JsonValue, path: str) -> ReportAxis:
    item = require_object(value, path)
    return ReportAxis(
        status=_axis_status(item["status"], f"{path}.status"),
        invariants=tuple(
            _invariant(invariant, f"{path}.invariants[{index}]", final=True)
            for index, invariant in enumerate(
                require_list(item["invariants"], f"{path}.invariants")
            )
        ),
    )


def _expected_invariant(
    *, name: str, verdict: str, explanation: str, candidate: bool
) -> dict[str, JsonValue]:
    return {
        "name": name,
        "verdict": verdict,
        "explanation": explanation,
        "missing_evidence": [],
        "candidate": candidate,
    }


def _expected_outcomes(mode: McpReportMode) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    clean = _expected_invariant(
        name="one_mcp_refund_matches_request",
        verdict="PASS",
        explanation="clean MCP refund committed and returned once",
        candidate=False,
    )
    retry = _expected_invariant(
        name="no_additional_mcp_refund_after_retry",
        verdict="PASS" if mode == "keyed" else "FAIL",
        explanation=(
            "MCP retry preserved the clean committed-effect and receipt semantics"
            if mode == "keyed"
            else (
                "MCP retry produced an additional or inconsistent committed refund; "
                "reproduced in two confirmations"
            )
        ),
        candidate=False,
    )
    return (
        {"status": "PASS", "invariants": [clean]},
        {"status": "PASS" if mode == "keyed" else "FAIL", "invariants": [retry]},
    )


def _expected_pair_results(mode: McpReportMode) -> tuple[list[JsonValue], list[JsonValue]]:
    clean, retry = _expected_outcomes(mode)
    retry_invariant = require_object(
        require_list(retry["invariants"], "retry.invariants")[0], "retry"
    )
    if mode == "unsafe":
        retry_invariant = _expected_invariant(
            name="no_additional_mcp_refund_after_retry",
            verdict="INCONCLUSIVE",
            explanation="MCP retry produced an additional or inconsistent committed refund",
            candidate=True,
        )
    return (
        list(require_list(clean["invariants"], "clean.invariants")),
        [retry_invariant],
    )


def _cleanup(value: JsonValue, path: str, protocol: str) -> tuple[str, bool, bool]:
    item = require_object(value, path)
    status = require_string(item["status"], f"{path}.status")
    closed = require_bool(item["resource_closed"], f"{path}.resource_closed")
    removed = require_bool(item["database_removed"], f"{path}.database_removed")
    recorded_protocol = require_nullable_string(
        item["protocol_version"], f"{path}.protocol_version"
    )
    _expect(
        status == "pass" and closed and removed and recorded_protocol == protocol,
        f"{path} does not prove completed cleanup and protocol negotiation",
    )
    return status, closed, removed


def _history_delta(baseline: JsonValue, final: JsonValue, path: str) -> int:
    before = require_list(
        require_object(baseline, f"{path}.baseline")["history"], f"{path}.baseline.history"
    )
    after = require_list(require_object(final, f"{path}.final")["history"], f"{path}.final.history")
    _expect(
        len(after) >= len(before) and after[: len(before)] == before, f"{path} is not append-only"
    )
    return len(after) - len(before)


def _attempt(
    value: JsonValue,
    path: str,
    *,
    expected_input: JsonValue,
    expected_operation_id: str,
    expected_trial_id: str,
) -> tuple[dict[str, JsonValue], str, JsonValue, JsonValue]:
    item = require_object(value, path)
    identity = require_object(item["identity"], f"{path}.identity")
    _expect(
        identity["operation_id"] == expected_operation_id
        and identity["trial_id"] == expected_trial_id,
        f"{path} changes logical operation or trial identity",
    )
    delivery_id = require_string(identity["delivery_id"], f"{path}.identity.delivery_id")
    attempt_id = require_string(identity["attempt_id"], f"{path}.identity.attempt_id")
    _expect(delivery_id != attempt_id, f"{path} aliases delivery and attempt identity")
    view = require_object(item["subject_view"], f"{path}.subject_view")
    _expect(view["input"] == expected_input, f"{path} changes the subject-visible input")
    return (
        identity,
        require_string(view["outcome"], f"{path}.subject_view.outcome"),
        view["returned_result"],
        item["observer_truth"],
    )


def _transport(
    value: JsonValue,
    path: str,
    *,
    expected_count: int,
    operation_key: str,
    forbidden_identities: set[str],
) -> tuple[str, ...]:
    items = require_list(value, path)
    _expect(len(items) == expected_count, f"{path} has an unexpected delivery count")
    request_ids: list[str] = []
    for index, item_value in enumerate(items):
        item_path = f"{path}[{index}]"
        item = require_object(item_value, item_path)
        _expect(item["ordinal"] == index + 1, f"{item_path} has an unexpected ordinal")
        request_id = require_string(item["mcp_request_id"], f"{item_path}.mcp_request_id")
        _expect(request_id not in forbidden_identities, f"{item_path} aliases a semantic identity")
        _expect(item["operation_key"] == operation_key, f"{item_path} changes the operation key")
        request_ids.append(request_id)
    _expect(len(set(request_ids)) == len(request_ids), f"{path} aliases MCP request identities")
    return tuple(request_ids)


def _validate_observed_refunds(
    observation: JsonValue,
    path: str,
    *,
    expected_count: int,
    mode: McpReportMode,
    payment_id: str,
    amount_minor_units: int,
    operation_key: str,
) -> None:
    item = require_object(observation, path)
    history = require_list(item["history"], f"{path}.history")
    _expect(len(history) == expected_count, f"{path} has an unexpected committed history count")
    refund_ids: list[str] = []
    for index, event_value in enumerate(history):
        event_path = f"{path}.history[{index}]"
        event = require_object(event_value, event_path)
        _expect(
            event["payment_id"] == payment_id
            and event["amount_minor_units"] == amount_minor_units
            and event["operation_key"] == (operation_key if mode == "keyed" else None),
            f"{event_path} is outside the registered refund evidence",
        )
        refund_ids.append(require_string(event["refund_id"], f"{event_path}.refund_id"))
    _expect(len(set(refund_ids)) == len(refund_ids), f"{path} reuses committed-effect identity")
    state = require_object(item["state"], f"{path}.state")
    _expect(
        state["payment_id"] == payment_id
        and state["payment_minor_units"] == 10_000
        and state["refunded_minor_units"] == expected_count * amount_minor_units
        and state["key_mapping_count"] == (1 if mode == "keyed" and expected_count else 0),
        f"{path} state and committed history disagree",
    )


def _validate_receipt_matches_history(
    receipt: JsonValue, observation: JsonValue, path: str, *, event_index: int
) -> None:
    item = require_object(receipt, f"{path}.receipt")
    history = require_list(
        require_object(observation, f"{path}.observation")["history"],
        f"{path}.observation.history",
    )
    event = require_object(history[event_index], f"{path}.observation.history[{event_index}]")
    _expect(
        item["refund_id"] == event["refund_id"]
        and item["payment_id"] == event["payment_id"]
        and item["amount_minor_units"] == event["amount_minor_units"],
        f"{path} returned result and committed-effect evidence disagree",
    )


def _validate_baseline(value: JsonValue, path: str, payment_id: str) -> None:
    item = require_object(value, path)
    _expect(require_list(item["history"], f"{path}.history") == [], f"{path} history is not fresh")
    _expect(
        item["state"]
        == {
            "payment_id": payment_id,
            "payment_minor_units": 10_000,
            "refunded_minor_units": 0,
            "key_mapping_count": 0,
        },
        f"{path} state is outside the registered fixture",
    )


def _validate_pair(
    value: JsonValue,
    *,
    expected_pair_id: str,
    mode: McpReportMode,
    expected_input: JsonValue,
    operation_id: str,
    operation_key: str,
    payment_id: str,
    amount_minor_units: int,
    protocol: str,
    seen_trial_ids: set[str],
    seen_delivery_ids: set[str],
    seen_attempt_ids: set[str],
    recorded_request_ids: list[str],
) -> tuple[ReportTrial, ReportTrial]:
    path = f"artifact.evidence.{expected_pair_id}"
    pair = require_object(value, path)
    _expect(pair["pair_id"] == expected_pair_id, f"{path}.pair_id is inconsistent")
    expected_clean, expected_retry = _expected_pair_results(mode)
    _expect(pair["clean_results"] == expected_clean, f"{path}.clean_results are inconsistent")
    _expect(pair["retry_results"] == expected_retry, f"{path}.retry_results are inconsistent")
    _expect(pair["errors"] == [], f"{path}.errors prevents registered report eligibility")

    clean = require_object(pair["clean"], f"{path}.clean")
    clean_trial_id = f"{expected_pair_id}/clean"
    _expect(clean["trial_id"] == clean_trial_id, f"{path}.clean trial identity is inconsistent")
    _validate_baseline(clean["baseline"], f"{path}.clean.baseline", payment_id)
    clean_identity, clean_outcome, clean_result, clean_observation = _attempt(
        clean["attempt"],
        f"{path}.clean.attempt",
        expected_input=expected_input,
        expected_operation_id=operation_id,
        expected_trial_id=clean_trial_id,
    )
    _expect(
        clean_outcome == "returned" and clean_result is not None, f"{path}.clean did not return"
    )
    _validate_observed_refunds(
        clean_observation,
        f"{path}.clean.attempt.observer_truth",
        expected_count=1,
        mode=mode,
        payment_id=payment_id,
        amount_minor_units=amount_minor_units,
        operation_key=operation_key,
    )
    _validate_receipt_matches_history(
        clean_result, clean_observation, f"{path}.clean", event_index=0
    )
    clean_delta = _history_delta(clean["baseline"], clean_observation, f"{path}.clean.history")
    _expect(
        clean["history_delta"]
        == require_object(clean_observation, f"{path}.clean.observation")["history"],
        f"{path}.clean history delta is inconsistent",
    )
    clean_delivery_id = require_string(clean_identity["delivery_id"], f"{path}.clean.delivery_id")
    clean_attempt_id = require_string(clean_identity["attempt_id"], f"{path}.clean.attempt_id")
    clean_ids = {
        operation_id,
        operation_key,
        clean_trial_id,
        clean_delivery_id,
        clean_attempt_id,
    }
    _expect(
        len(clean_ids) == 5,
        f"{path}.clean aliases logical, key, trial, delivery, or attempt identity",
    )
    recorded_request_ids.extend(
        _transport(
            clean["transport_truth"],
            f"{path}.clean.transport_truth",
            expected_count=1,
            operation_key=operation_key,
            forbidden_identities=clean_ids,
        )
    )
    clean_cleanup = _cleanup(clean["cleanup"], f"{path}.clean.cleanup", protocol)

    perturbed = require_object(pair["perturbed"], f"{path}.perturbed")
    perturbed_trial_id = f"{expected_pair_id}/perturbed"
    _expect(
        perturbed["operation_id"] == operation_id and perturbed["trial_id"] == perturbed_trial_id,
        f"{path}.perturbed changes operation or trial identity",
    )
    _validate_baseline(perturbed["baseline"], f"{path}.perturbed.baseline", payment_id)
    attempts = require_list(perturbed["attempts"], f"{path}.perturbed.attempts")
    _expect(len(attempts) == 2, f"{path}.perturbed must contain two attempts")
    parsed_attempts = [
        _attempt(
            attempt,
            f"{path}.perturbed.attempts[{index}]",
            expected_input=expected_input,
            expected_operation_id=operation_id,
            expected_trial_id=perturbed_trial_id,
        )
        for index, attempt in enumerate(attempts)
    ]
    first_identity, first_outcome, first_result, first_observation = parsed_attempts[0]
    second_identity, second_outcome, second_result, second_observation = parsed_attempts[1]
    _expect(
        first_outcome == "client_result_lost" and first_result is None,
        f"{path}.perturbed first result was not lost at the client boundary",
    )
    _expect(
        second_outcome == "returned"
        and second_result is not None
        and perturbed["subject_result"] == second_result,
        f"{path}.perturbed second result was not returned",
    )
    first_count = 1
    final_count = 1 if mode == "keyed" else 2
    _validate_observed_refunds(
        first_observation,
        f"{path}.perturbed.attempts[0].observer_truth",
        expected_count=first_count,
        mode=mode,
        payment_id=payment_id,
        amount_minor_units=amount_minor_units,
        operation_key=operation_key,
    )
    _validate_observed_refunds(
        second_observation,
        f"{path}.perturbed.attempts[1].observer_truth",
        expected_count=final_count,
        mode=mode,
        payment_id=payment_id,
        amount_minor_units=amount_minor_units,
        operation_key=operation_key,
    )
    _validate_receipt_matches_history(
        second_result,
        second_observation,
        f"{path}.perturbed.second_result",
        event_index=0 if mode == "keyed" else 1,
    )
    incremental_count = _history_delta(
        first_observation,
        second_observation,
        f"{path}.perturbed.first_to_final_history",
    )
    _expect(
        incremental_count == (0 if mode == "keyed" else 1),
        f"{path}.perturbed first-to-final history growth is inconsistent",
    )
    perturbed_delta = _history_delta(
        perturbed["baseline"], second_observation, f"{path}.perturbed.history"
    )
    harness = require_object(perturbed["harness_truth"], f"{path}.perturbed.harness_truth")
    first_attempt_id = require_string(
        first_identity["attempt_id"], f"{path}.perturbed.attempts[0].identity.attempt_id"
    )
    reached = _strings(
        harness["reached_attempt_ids"], f"{path}.perturbed.harness_truth.reached_attempt_ids"
    )
    second_attempt_id = require_string(
        second_identity["attempt_id"], f"{path}.perturbed.attempts[1].identity.attempt_id"
    )
    _expect(
        harness["boundary_name"] == "mcp_client_result_delivery"
        and harness["injected_attempt_id"] == first_attempt_id
        and reached == (first_attempt_id, second_attempt_id),
        f"{path}.perturbed does not prove the declared injected schedule",
    )
    undelivered = require_object(
        harness["undelivered_result"], f"{path}.perturbed.harness_truth.undelivered_result"
    )
    first_history = require_list(
        require_object(first_observation, f"{path}.perturbed.first_observation")["history"],
        f"{path}.perturbed.first_observation.history",
    )
    first_event = require_object(first_history[0], f"{path}.perturbed.first_observation.history[0]")
    _expect(
        undelivered["refund_id"] == first_event["refund_id"]
        and undelivered["payment_id"] == first_event["payment_id"]
        and undelivered["amount_minor_units"] == first_event["amount_minor_units"],
        f"{path}.perturbed undelivered result and committed-effect evidence disagree",
    )
    delivery_ids = {
        require_string(first_identity["delivery_id"], f"{path}.perturbed.first.delivery_id"),
        require_string(second_identity["delivery_id"], f"{path}.perturbed.second.delivery_id"),
    }
    attempt_ids = {
        first_attempt_id,
        second_attempt_id,
    }
    _expect(len(delivery_ids) == len(attempt_ids) == 2, f"{path}.perturbed aliases identities")
    _expect(delivery_ids.isdisjoint(attempt_ids), f"{path}.perturbed aliases delivery and attempt")
    pair_trial_ids = {clean_trial_id, perturbed_trial_id}
    pair_delivery_ids = {clean_delivery_id, *delivery_ids}
    pair_attempt_ids = {clean_attempt_id, *attempt_ids}
    all_ids = {
        operation_id,
        operation_key,
        *pair_trial_ids,
        *pair_delivery_ids,
        *pair_attempt_ids,
    }
    _expect(
        len(pair_trial_ids) == 2
        and len(pair_delivery_ids) == 3
        and len(pair_attempt_ids) == 3
        and len(all_ids) == 10,
        f"{path} aliases logical, key, trial, delivery, or attempt identity",
    )
    _expect(
        all_ids.isdisjoint(seen_trial_ids | seen_delivery_ids | seen_attempt_ids),
        f"{path} reuses trial, delivery, or attempt identity across pairs",
    )
    seen_trial_ids.update(pair_trial_ids)
    seen_delivery_ids.update(pair_delivery_ids)
    seen_attempt_ids.update(pair_attempt_ids)
    recorded_request_ids.extend(
        _transport(
            perturbed["transport_truth"],
            f"{path}.perturbed.transport_truth",
            expected_count=2,
            operation_key=operation_key,
            forbidden_identities=all_ids,
        )
    )
    perturbed_cleanup = _cleanup(perturbed["cleanup"], f"{path}.perturbed.cleanup", protocol)
    return (
        ReportTrial(
            pair_id=expected_pair_id,
            kind="clean",
            trial_id=clean_trial_id,
            history_delta_count=clean_delta,
            attempt_count=1,
            transport_delivery_count=1,
            cleanup_status=clean_cleanup[0],
            resource_closed=clean_cleanup[1],
            database_removed=clean_cleanup[2],
        ),
        ReportTrial(
            pair_id=expected_pair_id,
            kind="perturbed",
            trial_id=perturbed_trial_id,
            history_delta_count=perturbed_delta,
            attempt_count=2,
            transport_delivery_count=2,
            cleanup_status=perturbed_cleanup[0],
            resource_closed=perturbed_cleanup[1],
            database_removed=perturbed_cleanup[2],
            fault_boundary="mcp_client_result_delivery",
            fault_reached=True,
            injected_attempt_ordinal=1,
        ),
    )


def _supported_mode(value: JsonValue, path: str) -> McpReportMode:
    mode = require_string(value, path)
    if mode == "unsafe":
        return "unsafe"
    if mode == "keyed":
        return "keyed"
    _reject(f"{path} is not a registered mode")


def _build_report(read: ReadEvidenceArtifact) -> McpReport:
    payload = artifact_payload(read.artifact)
    compatibility_value = require_object(payload["compatibility"], "artifact.compatibility")
    replay = require_object(payload["replay"], "artifact.replay")
    scope_value = require_object(payload["scope"], "artifact.scope")
    producer = require_object(payload["producer"], "artifact.producer")
    mode = _supported_mode(replay["mode"], "artifact.replay.mode")
    live = build_mcp_compatibility(mode)

    schema = require_object(compatibility_value["schema"], "artifact.compatibility.schema")
    redaction = require_object(compatibility_value["redaction"], "artifact.compatibility.redaction")
    case = require_object(compatibility_value["case"], "artifact.compatibility.case")
    subject = require_object(compatibility_value["subject"], "artifact.compatibility.subject")
    runner = require_object(compatibility_value["runner"], "artifact.compatibility.runner")
    dependencies = require_object(
        compatibility_value["dependencies"], "artifact.compatibility.dependencies"
    )
    runtime_value = require_object(compatibility_value["runtime"], "artifact.compatibility.runtime")

    _expect(
        schema == {"name": _EVIDENCE_SCHEMA_NAME, "version": _EVIDENCE_SCHEMA_VERSION},
        "artifact evidence schema is not reportable",
    )
    _expect(
        redaction == {"id": _REDACTION_ID, "version": _REDACTION_VERSION},
        "artifact redaction policy is not reportable",
    )
    _expect(
        case == {"registry_id": _REGISTRY_ID, "version": _CASE_VERSION},
        "artifact case is not reportable",
    )
    _expect(
        subject["id"] == f"mcp_{mode}_refund_tool" and subject["mode"] == mode,
        "artifact subject is not the registered report source",
    )
    _expect(
        runner["id"] == _RUNNER_ID and runner["version"] == _REPORTABLE_RUNNER_VERSION,
        "artifact runner is not reportable",
    )
    _expect(
        producer == {"id": _RUNNER_ID, "version": _REPORTABLE_RUNNER_VERSION},
        "artifact producer is not reportable",
    )
    _expect(
        require_bool(scope_value["reportable"], "artifact.scope.reportable"),
        "artifact scope is not reportable",
    )
    for field in ("input", "contracts", "observer", "schedule"):
        _expect(
            compatibility_value[field] == live[field],
            f"artifact registered {field} contract is unsupported",
        )

    operation_key = require_nullable_string(
        scope_value["operation_key"], "artifact.scope.operation_key"
    )
    if operation_key is None:
        _reject("artifact operation key is unavailable")
    scope_input = require_object(scope_value["input"], "artifact.scope.input")
    subject_operation_key = require_string(
        scope_input["operation_key"], "artifact.scope.input.operation_key"
    )
    compatibility_input = require_object(
        compatibility_value["input"], "artifact.compatibility.input"
    )
    _expect(
        operation_key == subject_operation_key == compatibility_input["operation_key"],
        "artifact selected and subject-visible operation keys disagree",
    )
    payment_id = require_string(scope_input["payment_id"], "artifact.scope.input.payment_id")
    amount_minor_units = require_int(
        scope_input["amount_minor_units"], "artifact.scope.input.amount_minor_units"
    )
    operation_id = require_string(scope_value["operation_id"], "artifact.scope.operation_id")

    runtime = ReportRuntime(
        python_implementation=require_string(
            runtime_value["python_implementation"],
            "artifact.compatibility.runtime.python_implementation",
        ),
        python_version=require_string(
            runtime_value["python_version"], "artifact.compatibility.runtime.python_version"
        ),
        operating_system=require_string(
            runtime_value["operating_system"], "artifact.compatibility.runtime.operating_system"
        ),
        operating_system_release=require_string(
            runtime_value["operating_system_release"],
            "artifact.compatibility.runtime.operating_system_release",
        ),
        architecture=require_string(
            runtime_value["architecture"], "artifact.compatibility.runtime.architecture"
        ),
        sqlite_version=require_string(
            runtime_value["sqlite_version"], "artifact.compatibility.runtime.sqlite_version"
        ),
        mcp_protocol_revision=require_string(
            runtime_value["mcp_protocol_revision"],
            "artifact.compatibility.runtime.mcp_protocol_revision",
        ),
    )
    compatibility = ReportCompatibility(
        evidence_schema_name=require_string(schema["name"], "artifact.compatibility.schema.name"),
        evidence_schema_version=require_int(
            schema["version"], "artifact.compatibility.schema.version"
        ),
        producer_id=require_string(producer["id"], "artifact.producer.id"),
        producer_version=require_int(producer["version"], "artifact.producer.version"),
        case_registry_id=require_string(
            case["registry_id"], "artifact.compatibility.case.registry_id"
        ),
        case_version=require_int(case["version"], "artifact.compatibility.case.version"),
        subject_components=_components(
            subject["components"], "artifact.compatibility.subject.components"
        ),
        runner_components=_components(
            runner["components"], "artifact.compatibility.runner.components"
        ),
        dependency_lock_sha256=_sha256(
            dependencies["lock_sha256"], "artifact.compatibility.dependencies.lock_sha256"
        ),
        installed_locked_distributions=_distributions(
            dependencies["installed_locked_distributions"],
            "artifact.compatibility.dependencies.installed_locked_distributions",
        ),
        runtime=runtime,
    )
    scope = ReportScope(
        subject_name=require_string(scope_value["subject_name"], "artifact.scope.subject_name"),
        mode=mode,
        payment_id=payment_id,
        amount_minor_units=amount_minor_units,
        logical_operation_id=operation_id,
        operation_key=operation_key,
        schedule=require_string(scope_value["schedule"], "artifact.scope.schedule"),
        observer=_observer(scope_value["observer_coverage"]),
        contract=_contract(payload["contracts"]),
    )

    outcomes = require_object(payload["outcomes"], "artifact.outcomes")
    expected_clean, expected_retry = _expected_outcomes(mode)
    _expect(
        outcomes["clean_validity"] == expected_clean, "clean axis and invariant evidence disagree"
    )
    _expect(
        outcomes["retry_safety"] == expected_retry, "retry axis and invariant evidence disagree"
    )
    clean_axis = _axis(outcomes["clean_validity"], "artifact.outcomes.clean_validity")
    retry_axis = _axis(outcomes["retry_safety"], "artifact.outcomes.retry_safety")

    evidence = require_object(payload["evidence"], "artifact.evidence")
    confirmations = require_list(evidence["confirmations"], "artifact.evidence.confirmations")
    expected_confirmation_count = 0 if mode == "keyed" else 2
    _expect(
        len(confirmations) == expected_confirmation_count,
        "artifact confirmation evidence does not support its final retry axis",
    )
    trials: list[ReportTrial] = []
    seen_trial_ids: set[str] = set()
    seen_delivery_ids: set[str] = set()
    seen_attempt_ids: set[str] = set()
    recorded_request_ids: list[str] = []
    trials.extend(
        _validate_pair(
            evidence["primary"],
            expected_pair_id="primary",
            mode=mode,
            expected_input=scope_value["input"],
            operation_id=operation_id,
            operation_key=operation_key,
            payment_id=payment_id,
            amount_minor_units=amount_minor_units,
            protocol=runtime.mcp_protocol_revision,
            seen_trial_ids=seen_trial_ids,
            seen_delivery_ids=seen_delivery_ids,
            seen_attempt_ids=seen_attempt_ids,
            recorded_request_ids=recorded_request_ids,
        )
    )
    for index, pair in enumerate(confirmations, start=1):
        pair_id = f"confirmation/{index}"
        trials.extend(
            _validate_pair(
                pair,
                expected_pair_id=pair_id,
                mode=mode,
                expected_input=scope_value["input"],
                operation_id=operation_id,
                operation_key=operation_key,
                payment_id=payment_id,
                amount_minor_units=amount_minor_units,
                protocol=runtime.mcp_protocol_revision,
                seen_trial_ids=seen_trial_ids,
                seen_delivery_ids=seen_delivery_ids,
                seen_attempt_ids=seen_attempt_ids,
                recorded_request_ids=recorded_request_ids,
            )
        )

    semantic_ids = {
        operation_id,
        operation_key,
        *seen_trial_ids,
        *seen_delivery_ids,
        *seen_attempt_ids,
    }
    _expect(
        all(request_id not in semantic_ids for request_id in recorded_request_ids),
        "artifact MCP request identity aliases a semantic identity",
    )

    redaction_value = require_object(payload["redaction"], "artifact.redaction")
    omitted_categories = _strings(
        redaction_value["omitted_categories"], "artifact.redaction.omitted_categories"
    )
    scope_limitations = _strings(scope_value["limitations"], "artifact.scope.limitations")
    provenance = require_object(payload["provenance"], "artifact.provenance")
    provenance_limitations = _strings(
        provenance["known_limitations"], "artifact.provenance.known_limitations"
    )
    _expect(
        redaction_value["id"] == _REDACTION_ID
        and redaction_value["version"] == _REDACTION_VERSION
        and omitted_categories == _EXPECTED_OMITTED_CATEGORIES,
        "artifact redaction declaration is unsupported",
    )
    _expect(
        provenance["provider"] == _EXPECTED_PROVIDER
        and provenance["runner"] == _RUNNER_ID
        and provenance_limitations == _EXPECTED_LIMITATIONS
        and scope_limitations == _EXPECTED_LIMITATIONS,
        "artifact provenance or limitations are unsupported",
    )
    reproduction = payload["reproduction"]
    reproduction_matched = (
        None
        if reproduction is None
        else require_bool(
            require_object(reproduction, "artifact.reproduction")["matched"],
            "artifact.reproduction.matched",
        )
    )
    report = McpReport(
        source_sha256=read.sha256,
        source_size_bytes=read.size_bytes,
        lineage=require_string(replay["lineage"], "artifact.replay.lineage"),
        source_artifact_sha256=require_nullable_string(
            replay["source_sha256"], "artifact.replay.source_sha256"
        ),
        reproduction_matched=reproduction_matched,
        compatibility=compatibility,
        scope=scope,
        clean_validity=clean_axis,
        retry_safety=retry_axis,
        applicable_pair_count=1,
        confirmation_count=len(confirmations),
        trials=tuple(trials),
        omitted_categories=omitted_categories,
        limitations=(
            *scope_limitations,
            *scope.observer.limitations,
            _REPORT_LIMITATION,
        ),
    )
    try:
        _validate_projected_text(report_payload(report))
    except ValueError as error:
        _reject(f"artifact contains unsafe report text: {error}")
    return report


def build_mcp_report(path: Path) -> McpReport:
    """Strictly project one reportable registered MCP artifact without execution."""

    try:
        return _build_report(read_mcp_evidence(path))
    except McpReportInputError:
        raise
    except (
        EvidenceArtifactError,
        McpEvidenceCaptureError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ) as error:
        raise McpReportInputError(f"artifact is not reportable: {error}") from error


def validate_reportable_mcp_artifact(artifact: EvidenceArtifact) -> None:
    """Require that a completed in-memory artifact satisfies report eligibility."""

    encoded = canonical_artifact_bytes(artifact)
    read = ReadEvidenceArtifact(
        artifact=artifact,
        sha256=artifact_sha256(artifact),
        size_bytes=len(encoded),
    )
    try:
        _build_report(read)
    except McpReportInputError:
        raise
    except (McpEvidenceCaptureError, KeyError, IndexError, TypeError, ValueError) as error:
        raise McpReportInputError(f"artifact is not reportable: {error}") from error


def _requirements_payload(values: tuple[ReportRequirement, ...]) -> list[JsonValue]:
    return [{"kind": item.kind, "surface": item.surface} for item in values]


def _named_contract_payload(values: tuple[ReportNamedContract, ...]) -> list[JsonValue]:
    return [
        {"name": item.name, "requirements": _requirements_payload(item.requirements)}
        for item in values
    ]


def _axis_payload(value: ReportAxis) -> dict[str, JsonValue]:
    return {
        "status": value.status,
        "invariants": [
            {
                "name": item.name,
                "verdict": item.verdict,
                "explanation": item.explanation,
                "missing_evidence": list(item.missing_evidence),
            }
            for item in value.invariants
        ],
    }


def _trial_payload(value: ReportTrial) -> dict[str, JsonValue]:
    return {
        "pair_id": value.pair_id,
        "kind": value.kind,
        "trial_id": value.trial_id,
        "history_delta_count": value.history_delta_count,
        "attempt_count": value.attempt_count,
        "transport_delivery_count": value.transport_delivery_count,
        "cleanup": {
            "status": value.cleanup_status,
            "resource_closed": value.resource_closed,
            "database_removed": value.database_removed,
        },
        "fault": (
            None
            if value.fault_boundary is None
            else {
                "boundary": value.fault_boundary,
                "reached": value.fault_reached,
                "injected_attempt_ordinal": value.injected_attempt_ordinal,
            }
        ),
    }


def report_payload(value: McpReport) -> dict[str, JsonValue]:
    """Return the detached private report-v1 JSON projection."""

    compatibility = value.compatibility
    runtime = compatibility.runtime
    scope = value.scope
    contract = scope.contract
    return {
        "schema": {"name": _REPORT_SCHEMA_NAME, "version": _REPORT_SCHEMA_VERSION},
        "source": {
            "artifact_sha256": value.source_sha256,
            "size_bytes": value.source_size_bytes,
            "lineage": value.lineage,
            "source_artifact_sha256": value.source_artifact_sha256,
            "reproduction_matched": value.reproduction_matched,
        },
        "scope": {
            "subject_name": scope.subject_name,
            "mode": scope.mode,
            "input": {
                "payment_id": scope.payment_id,
                "amount_minor_units": scope.amount_minor_units,
            },
            "logical_operation_id": scope.logical_operation_id,
            "operation_key": scope.operation_key,
            "schedule": scope.schedule,
            "observer": {
                "surface": scope.observer.surface,
                "state": scope.observer.state,
                "history": scope.observer.history,
                "complete_history": scope.observer.complete_history,
                "observation_interval": scope.observer.observation_interval,
                "provenance": scope.observer.provenance,
                "limitations": list(scope.observer.limitations),
            },
            "contract": {
                "fixture": {
                    "id": contract.fixture_id,
                    "version": contract.fixture_version,
                    "seed": {
                        "payment_id": contract.fixture_seed_payment_id,
                        "payment_minor_units": contract.fixture_seed_payment_minor_units,
                        "refunded_minor_units": contract.fixture_seed_refunded_minor_units,
                        "key_mapping_count": contract.fixture_seed_key_mapping_count,
                        "history_count": contract.fixture_seed_history_count,
                    },
                },
                "clean_assertions": _named_contract_payload(contract.clean_assertions),
                "retry_invariants": _named_contract_payload(contract.retry_invariants),
                "canonicalization": {
                    "state": contract.state_canonicalization,
                    "history": contract.history_canonicalization,
                },
                "mcp_tool_name": contract.mcp_tool_name,
            },
        },
        "compatibility": {
            "evidence_schema": {
                "name": compatibility.evidence_schema_name,
                "version": compatibility.evidence_schema_version,
            },
            "producer": {
                "id": compatibility.producer_id,
                "version": compatibility.producer_version,
            },
            "case": {
                "registry_id": compatibility.case_registry_id,
                "version": compatibility.case_version,
            },
            "subject_components": [
                {"path": item.path, "sha256": item.sha256}
                for item in compatibility.subject_components
            ],
            "runner_components": [
                {"path": item.path, "sha256": item.sha256}
                for item in compatibility.runner_components
            ],
            "dependencies": {
                "lock_sha256": compatibility.dependency_lock_sha256,
                "installed_locked_distributions": [
                    {"name": item.name, "version": item.version}
                    for item in compatibility.installed_locked_distributions
                ],
            },
            "runtime": {
                "python_implementation": runtime.python_implementation,
                "python_version": runtime.python_version,
                "operating_system": runtime.operating_system,
                "operating_system_release": runtime.operating_system_release,
                "architecture": runtime.architecture,
                "sqlite_version": runtime.sqlite_version,
                "mcp_protocol_revision": runtime.mcp_protocol_revision,
            },
        },
        "axes": {
            "clean_validity": _axis_payload(value.clean_validity),
            "retry_safety": _axis_payload(value.retry_safety),
        },
        "evidence": {
            "applicable_pair_count": value.applicable_pair_count,
            "confirmation_count": value.confirmation_count,
            "trials": [_trial_payload(item) for item in value.trials],
        },
        "redaction": {"omitted_categories": list(value.omitted_categories)},
        "limitations": list(value.limitations),
    }


def _validate_projected_text(value: JsonValue, path: str = "report") -> None:
    if isinstance(value, str):
        for character in value:
            if unicodedata.category(character) in {"Cc", "Cf", "Cs"}:
                raise ValueError(f"{path} contains a control or format character")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_projected_text(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_projected_text(key, f"{path}.<key>")
            _validate_projected_text(item, f"{path}.{key}")


def _require_renderable(value: McpReport) -> dict[str, JsonValue]:
    payload = report_payload(value)
    try:
        _validate_projected_text(payload)
    except ValueError as error:
        raise McpReportRenderingError("private report contains unsafe text") from error
    return payload


def render_json_report(value: McpReport) -> str:
    """Render deterministic canonical JSON for one validated report model."""

    try:
        return (
            json.dumps(
                _require_renderable(value),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
    except (TypeError, ValueError) as error:
        raise McpReportRenderingError("private JSON report serialization failed") from error


def _axis_statement(name: ReportAxisName, axis: ReportAxis, value: McpReport) -> str:
    if name == "retry_safety" and axis.status == "PASS":
        return (
            "retry_safety=PASS: Every declared retry invariant evaluated PASS for all "
            f"{value.applicable_pair_count} applicable recorded input and scenario pairs, using "
            "evidence declared sufficient for each invariant. This conclusion is limited to "
            "the recorded subject, contract, inputs, environment, observer coverage, and "
            "failure schedules."
        )
    if name == "clean_validity" and axis.status == "UNVERIFIED":
        return (
            "clean_validity=UNVERIFIED: No clean functional assertions were declared. The "
            "retry-safety result does not establish that one ordinary execution performs the "
            "intended operation correctly."
        )
    if axis.status == "FAIL":
        failed = next((item for item in axis.invariants if item.verdict == "FAIL"), None)
        explanation = "confirmed invariant violation" if failed is None else failed.explanation
        return f"{name}=FAIL: {explanation}."
    if axis.status == "INCONCLUSIVE":
        missing = sorted(
            {item for invariant in axis.invariants for item in invariant.missing_evidence}
        )
        detail = (
            ", ".join(missing)
            if missing
            else "the recorded evidence could not justify pass or fail"
        )
        return f"{name}=INCONCLUSIVE: {detail}."
    if axis.status == "ERROR":
        errored = next((item for item in axis.invariants if item.verdict == "ERROR"), None)
        detail = (
            "evaluation infrastructure malfunctioned" if errored is None else errored.explanation
        )
        return f"{name}=ERROR: {detail}."
    if axis.status == "PASS":
        return (
            f"{name}=PASS: Every declared {name.replace('_', ' ')} assertion evaluated PASS "
            f"for {value.applicable_pair_count} applicable recorded input and scenario pairs."
        )
    return f"{name}={axis.status}."


def render_terminal_report(value: McpReport) -> str:
    """Render deterministic bounded terminal text for one validated report model."""

    _require_renderable(value)
    compatibility = value.compatibility
    runtime = compatibility.runtime
    scope = value.scope
    contract = scope.contract
    lines = [
        "EffectProbe controlled MCP report (private report v1)",
        "",
        "Source",
        f"  artifact_sha256: {value.source_sha256}",
        f"  size_bytes: {value.source_size_bytes}",
        f"  lineage: {value.lineage}",
        f"  source_artifact_sha256: {value.source_artifact_sha256 or 'none'}",
        f"  reproduction_matched: {value.reproduction_matched if value.reproduction_matched is not None else 'not_applicable'}",
        "",
        "Scope",
        f"  subject: {scope.subject_name}",
        f"  mode: {scope.mode}",
        f"  input.payment_id: {scope.payment_id}",
        f"  input.amount_minor_units: {scope.amount_minor_units}",
        f"  logical_operation_id: {scope.logical_operation_id}",
        f"  operation_key: {scope.operation_key or 'none'}",
        f"  schedule: {scope.schedule}",
        f"  observer: {scope.observer.surface} ({scope.observer.provenance}; {scope.observer.observation_interval})",
        f"  fixture: {contract.fixture_id}@{contract.fixture_version}",
        f"  clean_assertions: {', '.join(item.name for item in contract.clean_assertions)}",
        f"  retry_invariants: {', '.join(item.name for item in contract.retry_invariants)}",
        "",
        "Compatibility",
        f"  evidence_schema: {compatibility.evidence_schema_name}@{compatibility.evidence_schema_version}",
        f"  producer: {compatibility.producer_id}@{compatibility.producer_version}",
        f"  case: {compatibility.case_registry_id}@{compatibility.case_version}",
        f"  dependency_lock_sha256: {compatibility.dependency_lock_sha256}",
    ]
    lines.extend(
        f"  subject_component: {item.path} sha256={item.sha256}"
        for item in compatibility.subject_components
    )
    lines.extend(
        f"  runner_component: {item.path} sha256={item.sha256}"
        for item in compatibility.runner_components
    )
    lines.extend(
        f"  locked_distribution: {item.name}=={item.version}"
        for item in compatibility.installed_locked_distributions
    )
    lines.extend(
        [
            f"  runtime.python: {runtime.python_implementation} {runtime.python_version}",
            f"  runtime.os: {runtime.operating_system} {runtime.operating_system_release}",
            f"  runtime.architecture: {runtime.architecture}",
            f"  runtime.sqlite: {runtime.sqlite_version}",
            f"  runtime.mcp_protocol: {runtime.mcp_protocol_revision}",
            "",
            "Axes",
            f"  {_axis_statement('clean_validity', value.clean_validity, value)}",
            f"  {_axis_statement('retry_safety', value.retry_safety, value)}",
        ]
    )
    for axis_name, axis in (
        ("clean_validity", value.clean_validity),
        ("retry_safety", value.retry_safety),
    ):
        lines.extend(
            f"    {axis_name}.{item.name}: {item.verdict} — {item.explanation}"
            for item in axis.invariants
        )
    lines.extend(
        [
            "",
            "Evidence",
            f"  applicable_pair_count: {value.applicable_pair_count}",
            f"  confirmation_count: {value.confirmation_count}",
        ]
    )
    lines.extend(
        (
            f"  trial: {item.trial_id}; history_delta_count={item.history_delta_count}; "
            f"attempts={item.attempt_count}; transport_deliveries={item.transport_delivery_count}; "
            f"cleanup={item.cleanup_status}"
            + (
                ""
                if item.fault_boundary is None
                else (
                    f"; fault_boundary={item.fault_boundary}; reached={str(item.fault_reached).lower()}; "
                    f"injected_attempt_ordinal={item.injected_attempt_ordinal}"
                )
            )
        )
        for item in value.trials
    )
    lines.extend(["", "Redaction"])
    lines.extend(f"  omitted: {item}" for item in value.omitted_categories)
    lines.extend(["", "Limitations"])
    lines.extend(f"  - {item}" for item in value.limitations)
    return "\n".join(lines) + "\n"


def _property(properties: ET.Element, name: str, value: object) -> None:
    ET.SubElement(properties, "property", {"name": name, "value": str(value)})


def _junit_cases(value: McpReport) -> list[tuple[ReportAxisName, ReportInvariant]]:
    cases: list[tuple[ReportAxisName, ReportInvariant]] = []
    axes: tuple[tuple[ReportAxisName, ReportAxis], ...] = (
        ("clean_validity", value.clean_validity),
        ("retry_safety", value.retry_safety),
    )
    for axis_name, axis in axes:
        if axis_name == "clean_validity" and axis.status == "UNVERIFIED" and not axis.invariants:
            cases.append(
                (
                    axis_name,
                    ReportInvariant(
                        name="clean_contract_not_declared",
                        verdict="INCONCLUSIVE",
                        explanation="clean validity is unverified because no clean assertions were declared",
                    ),
                )
            )
        else:
            cases.extend((axis_name, invariant) for invariant in axis.invariants)
            if axis.status in {"FAIL", "ERROR", "INCONCLUSIVE"} and not any(
                invariant.verdict == axis.status for invariant in axis.invariants
            ):
                cases.append(
                    (
                        axis_name,
                        ReportInvariant(
                            name=f"{axis_name}_axis_{axis.status.lower()}",
                            verdict=cast("ReportVerdict", axis.status),
                            explanation=f"{axis_name} aggregated to {axis.status}",
                        ),
                    )
                )
    return cases


def render_junit_report(value: McpReport) -> str:
    """Render deterministic JUnit XML without creating an overall EffectProbe axis."""

    _require_renderable(value)
    cases = _junit_cases(value)
    failures = sum(item.verdict == "FAIL" for _, item in cases)
    errors = sum(item.verdict == "ERROR" for _, item in cases)
    skipped = sum(item.verdict == "INCONCLUSIVE" for _, item in cases)
    suite = ET.Element(
        "testsuite",
        {
            "name": "effectprobe.controlled_mcp",
            "tests": str(len(cases)),
            "failures": str(failures),
            "errors": str(errors),
            "skipped": str(skipped),
            "time": "0",
        },
    )
    properties = ET.SubElement(suite, "properties")
    _property(properties, "effectprobe.clean_validity", value.clean_validity.status)
    _property(properties, "effectprobe.retry_safety", value.retry_safety.status)
    _property(properties, "effectprobe.artifact_sha256", value.source_sha256)
    _property(properties, "effectprobe.schedule", value.scope.schedule)
    _property(properties, "effectprobe.observer.surface", value.scope.observer.surface)
    _property(properties, "effectprobe.observer.provenance", value.scope.observer.provenance)
    _property(
        properties,
        "effectprobe.evidence_schema",
        f"{value.compatibility.evidence_schema_name}@{value.compatibility.evidence_schema_version}",
    )
    _property(
        properties,
        "effectprobe.producer",
        f"{value.compatibility.producer_id}@{value.compatibility.producer_version}",
    )
    _property(
        properties, "effectprobe.dependency_lock_sha256", value.compatibility.dependency_lock_sha256
    )
    _property(
        properties,
        "effectprobe.runtime.python_implementation",
        value.compatibility.runtime.python_implementation,
    )
    _property(
        properties, "effectprobe.runtime.python_version", value.compatibility.runtime.python_version
    )
    _property(
        properties,
        "effectprobe.runtime.operating_system",
        value.compatibility.runtime.operating_system,
    )
    _property(
        properties,
        "effectprobe.runtime.operating_system_release",
        value.compatibility.runtime.operating_system_release,
    )
    _property(
        properties, "effectprobe.runtime.architecture", value.compatibility.runtime.architecture
    )
    _property(
        properties, "effectprobe.runtime.sqlite_version", value.compatibility.runtime.sqlite_version
    )
    _property(
        properties,
        "effectprobe.runtime.mcp_protocol_revision",
        value.compatibility.runtime.mcp_protocol_revision,
    )
    for item in value.compatibility.subject_components:
        _property(properties, f"effectprobe.subject_component.{item.path}.sha256", item.sha256)
    for item in value.compatibility.runner_components:
        _property(properties, f"effectprobe.runner_component.{item.path}.sha256", item.sha256)
    for item in value.compatibility.installed_locked_distributions:
        _property(properties, f"effectprobe.locked_distribution.{item.name}.version", item.version)
    for index, limitation in enumerate(value.limitations, start=1):
        _property(properties, f"effectprobe.limitation.{index}", limitation)
    for index, category in enumerate(value.omitted_categories, start=1):
        _property(properties, f"effectprobe.redaction.omitted.{index}", category)

    for axis_name, invariant in cases:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": f"effectprobe.{axis_name}",
                "name": invariant.name,
                "time": "0",
            },
        )
        if invariant.verdict == "FAIL":
            child = ET.SubElement(
                case,
                "failure",
                {"type": "confirmed_invariant_failure", "message": invariant.explanation},
            )
            child.text = invariant.explanation
        elif invariant.verdict == "ERROR":
            child = ET.SubElement(
                case, "error", {"type": "evaluation_error", "message": invariant.explanation}
            )
            child.text = invariant.explanation
        elif invariant.verdict == "INCONCLUSIVE":
            detail = (
                f"{invariant.explanation}; missing evidence: {', '.join(invariant.missing_evidence)}"
                if invariant.missing_evidence
                else invariant.explanation
            )
            child = ET.SubElement(case, "skipped", {"type": "inconclusive", "message": detail})
            child.text = detail
    try:
        ET.indent(suite, space="  ")
        return ET.tostring(suite, encoding="utf-8", xml_declaration=True).decode("utf-8") + "\n"
    except (TypeError, ValueError) as error:
        raise McpReportRenderingError("private JUnit report serialization failed") from error
