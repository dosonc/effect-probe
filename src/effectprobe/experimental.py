"""Experimental trusted-local Python case contract.

This module is intentionally unstable before EffectProbe 1.0. It exposes one
bounded provider-result-loss schedule for trusted local test code. It is not a
plugin loader, evidence-artifact schema, replay interface, or security sandbox.
"""

import hashlib
import json
import math
import platform
import re
import stat
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, cast

from effectprobe import __version__
from effectprobe._lost_result import OperationId as _OperationId
from effectprobe._lost_result import TrialId as _PrivateTrialId
from effectprobe._semantic_kernel import (
    AxisResult as _AxisResult,
)
from effectprobe._semantic_kernel import (
    AxisStatus as _PrivateAxisStatus,
)
from effectprobe._semantic_kernel import (
    CaseDefinition as _CaseDefinition,
)
from effectprobe._semantic_kernel import (
    CaseResult as _CaseResult,
)
from effectprobe._semantic_kernel import (
    CleanAssertion as _CleanAssertion,
)
from effectprobe._semantic_kernel import (
    CleanEvaluationContext as _CleanEvaluationContext,
)
from effectprobe._semantic_kernel import (
    CleanupStatus as _PrivateCleanupStatus,
)
from effectprobe._semantic_kernel import (
    EvaluationDecision as _EvaluationDecision,
)
from effectprobe._semantic_kernel import (
    EvidenceKind as _PrivateEvidenceKind,
)
from effectprobe._semantic_kernel import (
    EvidenceRequirement as _EvidenceRequirement,
)
from effectprobe._semantic_kernel import (
    InvariantResult as _InvariantResult,
)
from effectprobe._semantic_kernel import (
    InvariantVerdict as _PrivateInvariantVerdict,
)
from effectprobe._semantic_kernel import (
    PairResult as _PairResult,
)
from effectprobe._semantic_kernel import (
    RetryEvaluationContext as _RetryEvaluationContext,
)
from effectprobe._semantic_kernel import (
    RetryInvariant as _RetryInvariant,
)
from effectprobe._semantic_kernel import (
    SurfaceCoverage as _SurfaceCoverage,
)
from effectprobe._semantic_kernel import (
    SurfaceObservation as _SurfaceObservation,
)
from effectprobe._semantic_kernel import (
    World as _PrivateWorld,
)
from effectprobe._semantic_kernel import (
    WorldSession as _PrivateWorldSession,
)
from effectprobe._semantic_kernel import (
    evaluate_case as _evaluate_case,
)

_TOKEN = re.compile(r"[a-z][a-z0-9_.:/-]{0,127}\Z")
_MAX_LABELS = 32
_MAX_SOURCE_FILES = 32
_MAX_FINGERPRINT_FILE_BYTES = 16 * 1024 * 1024
_MAX_JSON_DEPTH = 24
_MAX_JSON_NODES = 10_000
_MAX_JSON_ITEMS = 256
_MAX_JSON_STRING_CHARS = 16_384
_MAX_OPERATION_KEY_CHARS = 16_384
_SCHEDULE = "provider_commit_then_lose_first_result_and_retry_once"
_BOUNDARY = "provider_result_delivery"

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type DeliverResult[ResultT] = Callable[[ResultT], ResultT]


class EvidenceKind(Enum):
    """Evidence kind required by one experimental contract."""

    STATE = "state"
    HISTORY = "history"
    COMPLETE_HISTORY = "complete_history"
    SUBJECT_RESULT = "subject_result"


class InvariantVerdict(Enum):
    """Result of one clean assertion or retry invariant."""

    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    ERROR = "ERROR"


class AxisStatus(Enum):
    """Aggregate status of one axis, never an overall subject verdict."""

    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    ERROR = "ERROR"
    UNVERIFIED = "UNVERIFIED"


class CleanupStatus(Enum):
    """Outcome of the one required cleanup attempt for a world."""

    NOT_ATTEMPTED = "not_attempted"
    PASS = "pass"
    ERROR = "error"


class ApplicabilityStatus(Enum):
    """Whether the concrete input belongs to the declared case domain."""

    NOT_EVALUATED = "NOT_EVALUATED"
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    """One required evidence kind and its optional named surface."""

    kind: EvidenceKind
    surface: str | None = None


@dataclass(frozen=True, slots=True)
class ObserverCoverage:
    """Declared coverage for the one supported external-effect surface."""

    surface: str
    state: bool
    history: bool
    complete_history: bool
    observation_interval: str
    provenance: str
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Canonicalization:
    """Bounded identifiers for the caller's canonicalization rules."""

    input: str
    state: str
    event: str


@dataclass(frozen=True, slots=True)
class Observation[StateT, EventT]:
    """One state snapshot and ordered committed-effect history."""

    state: StateT
    history: tuple[EventT, ...]


@dataclass(frozen=True, slots=True)
class Decision:
    """Boolean contract decision plus a bounded, non-secret reason code."""

    passed: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class TrialId:
    """Opaque harness trial identity supplied to the lifecycle factory."""

    value: str


@dataclass(slots=True)
class World[InputT, ResultT, StateT, EventT]:
    """Capabilities supplied by one freshly provisioned trusted-local world."""

    invoke: Callable[[InputT, DeliverResult[ResultT]], ResultT]
    observe: Callable[[], Observation[StateT, EventT]]
    validate_fixture: Callable[[Observation[StateT, EventT]], None]


@dataclass(slots=True)
class WorldSession[InputT, ResultT, StateT, EventT]:
    """Explicit provisioning and one-attempt cleanup for a fresh world."""

    provision: Callable[[], World[InputT, ResultT, StateT, EventT]]
    cleanup: Callable[[World[InputT, ResultT, StateT, EventT] | None], None]


@dataclass(frozen=True, slots=True)
class CleanContext[InputT, ResultT, StateT, EventT]:
    """Evidence available to one clean functional assertion."""

    input: InputT
    baseline: Observation[StateT, EventT]
    final: Observation[StateT, EventT]
    history_delta: tuple[EventT, ...]
    returned_result: ResultT


@dataclass(frozen=True, slots=True)
class RetryContext[InputT, ResultT, StateT, EventT]:
    """Evidence available to one retry invariant."""

    input: InputT
    clean: CleanContext[InputT, ResultT, StateT, EventT]
    perturbed_baseline: Observation[StateT, EventT]
    perturbed_final: Observation[StateT, EventT]
    perturbed_history_delta: tuple[EventT, ...]
    canonical_clean_history_delta: tuple[JsonValue, ...]
    canonical_perturbed_history_delta: tuple[JsonValue, ...]
    subject_result: ResultT
    attempt_count: int
    attempt_outcomes: tuple[str, ...]
    boundary_name: str
    boundary_reached: bool
    fault_injected: bool


@dataclass(frozen=True, slots=True)
class CleanAssertion[InputT, ResultT, StateT, EventT]:
    """Named clean contract and its declared evidence requirements."""

    name: str
    requirements: tuple[EvidenceRequirement, ...]
    evaluate: Callable[[CleanContext[InputT, ResultT, StateT, EventT]], Decision]


@dataclass(frozen=True, slots=True)
class RetryInvariant[InputT, ResultT, StateT, EventT]:
    """Named retry contract and its declared evidence requirements."""

    name: str
    requirements: tuple[EvidenceRequirement, ...]
    evaluate: Callable[[RetryContext[InputT, ResultT, StateT, EventT]], Decision]


@dataclass(frozen=True, slots=True)
class ContractDescriptor:
    """Inspectable contract name and evidence requirements."""

    name: str
    requirements: tuple[EvidenceRequirement, ...]


def _always_applicable(_input: object) -> bool:
    return True


def _no_preflight() -> None:
    return None


@dataclass(frozen=True, slots=True)
class Case[InputT, ResultT, StateT, EventT]:
    """One concrete trusted-local experimental case."""

    case_name: str
    subject_name: str
    input: InputT
    operation_id: str
    operation_key_selector: Callable[[InputT], str | None]
    canonicalize_input: Callable[[InputT], JsonValue]
    canonicalization: Canonicalization
    source_files: tuple[Path, ...]
    dependency_lock: Path
    world_factory: Callable[[TrialId], WorldSession[InputT, ResultT, StateT, EventT]]
    coverage: ObserverCoverage
    canonicalize_state: Callable[[StateT], JsonValue]
    canonicalize_event: Callable[[EventT], JsonValue]
    clean_assertions: tuple[CleanAssertion[InputT, ResultT, StateT, EventT], ...]
    retry_invariants: tuple[RetryInvariant[InputT, ResultT, StateT, EventT], ...]
    limitations: tuple[str, ...]
    preflight: Callable[[], None] = _no_preflight
    applicable: Callable[[InputT], bool] = _always_applicable


@dataclass(frozen=True, slots=True)
class RuntimeFingerprint:
    """Bounded runtime fields included in the experimental compatibility scope."""

    effectprobe_version: str
    python_implementation: str
    python_version: str
    sys_platform: str
    machine: str


@dataclass(frozen=True, slots=True)
class ScopeFingerprint:
    """Redacted compatibility scope for one concrete case execution."""

    case_name: str
    subject_name: str
    operation_id: str
    operation_key_present: bool
    source_sha256: str
    dependency_lock_sha256: str
    input_sha256: str
    operation_key_sha256: str | None
    contract_sha256: str
    observer_sha256: str
    runtime: RuntimeFingerprint
    canonicalization: Canonicalization
    clean_assertions: tuple[ContractDescriptor, ...]
    retry_invariants: tuple[ContractDescriptor, ...]
    schedule: str
    boundary: str
    scope_sha256: str
    coverage: ObserverCoverage
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InvariantReport:
    """Redacted result for one contract evaluator."""

    name: str
    verdict: InvariantVerdict
    reason_code: str
    missing_evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AxisReport:
    """One axis and its constituent invariant results."""

    status: AxisStatus
    invariants: tuple[InvariantReport, ...]


@dataclass(frozen=True, slots=True)
class AttemptReport:
    """Identity and schedule proof for one concrete subject invocation."""

    delivery_id: str
    attempt_id: str
    outcome: str
    boundary_reached: bool
    fault_injected: bool
    observed_history_count: int


@dataclass(frozen=True, slots=True)
class TrialReport:
    """Redacted lifecycle evidence for one clean or perturbed world."""

    trial_id: str
    baseline_history_count: int | None
    attempts: tuple[AttemptReport, ...]
    cleanup: CleanupStatus


@dataclass(frozen=True, slots=True)
class ErrorSummary:
    """Fixed-category infrastructure error without raw exception details."""

    axis: Literal["clean", "retry"] | None
    phase: str
    category: str


@dataclass(frozen=True, slots=True)
class PairReport:
    """Redacted clean/perturbed evidence for one primary or confirmation pair."""

    pair_id: str
    clean: TrialReport
    perturbed: TrialReport
    errors: tuple[ErrorSummary, ...]


@dataclass(frozen=True, slots=True)
class ReportError:
    """Report-level failure that occurred before evaluative axes existed."""

    phase: Literal["configuration", "preflight", "applicability"]
    category: str


@dataclass(frozen=True, slots=True)
class CaseReport:
    """Immutable redacted projection for one experimental case run."""

    scope: ScopeFingerprint | None
    applicability: ApplicabilityStatus
    applicable_pair_count: int
    clean_validity: AxisReport | None
    retry_safety: AxisReport | None
    primary: PairReport | None
    confirmations: tuple[PairReport, ...]
    report_error: ReportError | None


class _ConfigurationError(ValueError):
    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


def _validate_token(value: object, *, category: str) -> str:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise _ConfigurationError(category)
    return value


def _require_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError
    return value


def _validate_tokens(values: tuple[str, ...], *, category: str) -> None:
    if len(values) > _MAX_LABELS or len(set(values)) != len(values):
        raise _ConfigurationError(category)
    for value in values:
        _validate_token(value, category=category)


def _validate_json(value: object, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if nodes[0] > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
        raise _ConfigurationError("invalid_input_projection")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _ConfigurationError("invalid_input_projection")
        return
    if isinstance(value, str):
        if len(value) > _MAX_JSON_STRING_CHARS:
            raise _ConfigurationError("invalid_input_projection")
        return
    if isinstance(value, list):
        items = cast("list[object]", value)
        if len(items) > _MAX_JSON_ITEMS:
            raise _ConfigurationError("invalid_input_projection")
        for item in items:
            _validate_json(item, depth=depth + 1, nodes=nodes)
        return
    if isinstance(value, dict):
        items = cast("dict[object, object]", value)
        if len(items) > _MAX_JSON_ITEMS or any(not isinstance(key, str) for key in items):
            raise _ConfigurationError("invalid_input_projection")
        for key, item in items.items():
            assert isinstance(key, str)
            if len(key) > _MAX_JSON_STRING_CHARS:
                raise _ConfigurationError("invalid_input_projection")
            _validate_json(item, depth=depth + 1, nodes=nodes)
        return
    raise _ConfigurationError("invalid_input_projection")


def _json_bytes(value: JsonValue) -> bytes:
    _validate_json(value)
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise _ConfigurationError("invalid_input_projection") from error


def _sha256_frame(label: bytes, data: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(label)
    digest.update(b"\0")
    digest.update(len(data).to_bytes(8, "big"))
    digest.update(data)
    return digest.hexdigest()


def _read_fingerprint_file(path: Path, *, category: str) -> bytes:
    try:
        details = path.lstat()
        if not stat.S_ISREG(details.st_mode) or details.st_size > _MAX_FINGERPRINT_FILE_BYTES:
            raise _ConfigurationError(category)
        data = path.read_bytes()
    except _ConfigurationError:
        raise
    except (OSError, ValueError) as error:
        raise _ConfigurationError(category) from error
    if len(data) > _MAX_FINGERPRINT_FILE_BYTES:
        raise _ConfigurationError(category)
    return data


def _source_digest(paths: tuple[Path, ...]) -> str:
    if not paths or len(paths) > _MAX_SOURCE_FILES:
        raise _ConfigurationError("invalid_source_fingerprint")
    file_digests = sorted(
        _sha256_frame(
            b"effectprobe.experimental.source-file.v1",
            _read_fingerprint_file(path, category="invalid_source_fingerprint"),
        )
        for path in paths
    )
    return _sha256_frame(
        b"effectprobe.experimental.source-set.v1",
        _json_bytes(cast("JsonValue", file_digests)),
    )


def _runtime_fingerprint() -> RuntimeFingerprint:
    machine = platform.machine().lower() or "unknown"
    machine = re.sub(r"[^a-z0-9_.-]", "_", machine)[:64] or "unknown"
    return RuntimeFingerprint(
        effectprobe_version=__version__,
        python_implementation=platform.python_implementation().lower(),
        python_version=platform.python_version(),
        sys_platform=sys.platform,
        machine=machine,
    )


def _requirement_payload(requirement: EvidenceRequirement) -> dict[str, JsonValue]:
    return {
        "kind": requirement.kind.value,
        "surface": requirement.surface,
    }


def _contract_payload[InputT, ResultT, StateT, EventT](
    case: Case[InputT, ResultT, StateT, EventT],
) -> dict[str, JsonValue]:
    return {
        "canonicalization": {
            "input": case.canonicalization.input,
            "state": case.canonicalization.state,
            "event": case.canonicalization.event,
        },
        "clean": [
            {
                "name": item.name,
                "requirements": [_requirement_payload(value) for value in item.requirements],
            }
            for item in case.clean_assertions
        ],
        "retry": [
            {
                "name": item.name,
                "requirements": [_requirement_payload(value) for value in item.requirements],
            }
            for item in case.retry_invariants
        ],
    }


def _contract_descriptors[InputT, ResultT, StateT, EventT](
    case: Case[InputT, ResultT, StateT, EventT],
) -> tuple[tuple[ContractDescriptor, ...], tuple[ContractDescriptor, ...]]:
    return (
        tuple(
            ContractDescriptor(name=value.name, requirements=value.requirements)
            for value in case.clean_assertions
        ),
        tuple(
            ContractDescriptor(name=value.name, requirements=value.requirements)
            for value in case.retry_invariants
        ),
    )


def _observer_payload(coverage: ObserverCoverage) -> dict[str, JsonValue]:
    return {
        "surface": coverage.surface,
        "state": coverage.state,
        "history": coverage.history,
        "complete_history": coverage.complete_history,
        "observation_interval": coverage.observation_interval,
        "provenance": coverage.provenance,
        "limitations": list(coverage.limitations),
    }


def _is_evidence_requirement(value: object) -> bool:
    return isinstance(value, EvidenceRequirement)


def _is_evidence_kind(value: object) -> bool:
    return isinstance(value, EvidenceKind)


def _is_observer_coverage(value: object) -> bool:
    return isinstance(value, ObserverCoverage)


def _is_canonicalization(value: object) -> bool:
    return isinstance(value, Canonicalization)


def _is_contract(value: object) -> bool:
    return isinstance(value, (CleanAssertion, RetryInvariant))


def _is_path(value: object) -> bool:
    return isinstance(value, Path)


def _validate_requirement(requirement: EvidenceRequirement) -> None:
    if not _is_evidence_requirement(requirement) or not _is_evidence_kind(requirement.kind):
        raise _ConfigurationError("invalid_evidence_requirement")
    if requirement.kind is EvidenceKind.SUBJECT_RESULT:
        if requirement.surface is not None:
            raise _ConfigurationError("invalid_evidence_requirement")
        return
    if requirement.surface is None:
        raise _ConfigurationError("invalid_evidence_requirement")
    _validate_token(requirement.surface, category="invalid_evidence_requirement")


def _validate_case[InputT, ResultT, StateT, EventT](
    case: Case[InputT, ResultT, StateT, EventT],
) -> None:
    _validate_token(case.case_name, category="invalid_case_name")
    _validate_token(case.subject_name, category="invalid_subject_name")
    _validate_token(case.operation_id, category="invalid_operation_id")
    if not _is_canonicalization(case.canonicalization):
        raise _ConfigurationError("invalid_canonicalization")
    _validate_token(case.canonicalization.input, category="invalid_canonicalization")
    _validate_token(case.canonicalization.state, category="invalid_canonicalization")
    _validate_token(case.canonicalization.event, category="invalid_canonicalization")
    coverage = case.coverage
    if not _is_observer_coverage(coverage):
        raise _ConfigurationError("invalid_observer")
    _validate_token(coverage.surface, category="invalid_observer")
    _validate_token(coverage.observation_interval, category="invalid_observer")
    _validate_token(coverage.provenance, category="invalid_observer")
    _validate_tokens(coverage.limitations, category="invalid_observer")
    _validate_tokens(case.limitations, category="invalid_case_limitations")
    try:
        state = _require_bool(coverage.state)
        history = _require_bool(coverage.history)
        complete_history = _require_bool(coverage.complete_history)
    except TypeError as error:
        raise _ConfigurationError("invalid_observer") from error
    if (not state and not history) or (complete_history and not history):
        raise _ConfigurationError("invalid_observer")
    if not case.retry_invariants:
        raise _ConfigurationError("empty_retry_contract")
    clean_names = tuple(item.name for item in case.clean_assertions)
    retry_names = tuple(item.name for item in case.retry_invariants)
    _validate_tokens(clean_names, category="invalid_clean_contract")
    _validate_tokens(retry_names, category="invalid_retry_contract")
    if set(clean_names) & set(retry_names):
        raise _ConfigurationError("duplicate_contract_name")
    for contract in (*case.clean_assertions, *case.retry_invariants):
        if not _is_contract(contract) or not callable(contract.evaluate):
            raise _ConfigurationError("invalid_contract")
        if len(contract.requirements) > _MAX_LABELS:
            raise _ConfigurationError("invalid_evidence_requirement")
        for requirement in contract.requirements:
            _validate_requirement(requirement)
    if (
        not case.source_files
        or any(not _is_path(path) for path in case.source_files)
        or not _is_path(case.dependency_lock)
    ):
        raise _ConfigurationError("invalid_fingerprint_path")
    for callback in (
        case.operation_key_selector,
        case.canonicalize_input,
        case.world_factory,
        case.canonicalize_state,
        case.canonicalize_event,
        case.preflight,
        case.applicable,
    ):
        if not callable(callback):
            raise _ConfigurationError("invalid_callback")


def _build_scope[InputT, ResultT, StateT, EventT](
    case: Case[InputT, ResultT, StateT, EventT],
    canonical_input: JsonValue,
    operation_key: str | None,
) -> ScopeFingerprint:
    source_sha256 = _source_digest(case.source_files)
    lock_data = _read_fingerprint_file(
        case.dependency_lock,
        category="invalid_dependency_fingerprint",
    )
    dependency_lock_sha256 = _sha256_frame(
        b"effectprobe.experimental.dependency-lock.v1",
        lock_data,
    )
    input_sha256 = _sha256_frame(
        b"effectprobe.experimental.input.v1",
        _json_bytes(canonical_input),
    )
    operation_key_sha256 = (
        None
        if operation_key is None
        else _sha256_frame(
            b"effectprobe.experimental.operation-key.v1",
            operation_key.encode("utf-8"),
        )
    )
    contract_payload = _contract_payload(case)
    contract_sha256 = _sha256_frame(
        b"effectprobe.experimental.contract.v1",
        _json_bytes(contract_payload),
    )
    observer_payload = _observer_payload(case.coverage)
    observer_sha256 = _sha256_frame(
        b"effectprobe.experimental.observer.v1",
        _json_bytes(observer_payload),
    )
    runtime = _runtime_fingerprint()
    clean_assertions, retry_invariants = _contract_descriptors(case)
    scope_payload: dict[str, JsonValue] = {
        "case_name": case.case_name,
        "subject_name": case.subject_name,
        "operation_id": case.operation_id,
        "operation_key_present": operation_key is not None,
        "operation_key_sha256": operation_key_sha256,
        "source_sha256": source_sha256,
        "dependency_lock_sha256": dependency_lock_sha256,
        "input_sha256": input_sha256,
        "contract_sha256": contract_sha256,
        "observer_sha256": observer_sha256,
        "canonicalization": {
            "input": case.canonicalization.input,
            "state": case.canonicalization.state,
            "event": case.canonicalization.event,
        },
        "runtime": {
            "effectprobe_version": runtime.effectprobe_version,
            "python_implementation": runtime.python_implementation,
            "python_version": runtime.python_version,
            "sys_platform": runtime.sys_platform,
            "machine": runtime.machine,
        },
        "schedule": _SCHEDULE,
        "boundary": _BOUNDARY,
        "limitations": list(case.limitations),
    }
    scope_sha256 = _sha256_frame(
        b"effectprobe.experimental.scope.v1",
        _json_bytes(scope_payload),
    )
    return ScopeFingerprint(
        case_name=case.case_name,
        subject_name=case.subject_name,
        operation_id=case.operation_id,
        operation_key_present=operation_key is not None,
        source_sha256=source_sha256,
        dependency_lock_sha256=dependency_lock_sha256,
        input_sha256=input_sha256,
        operation_key_sha256=operation_key_sha256,
        contract_sha256=contract_sha256,
        observer_sha256=observer_sha256,
        runtime=runtime,
        canonicalization=case.canonicalization,
        clean_assertions=clean_assertions,
        retry_invariants=retry_invariants,
        schedule=_SCHEDULE,
        boundary=_BOUNDARY,
        scope_sha256=scope_sha256,
        coverage=case.coverage,
        limitations=case.limitations,
    )


def _private_requirement(value: EvidenceRequirement) -> _EvidenceRequirement:
    mapping = {
        EvidenceKind.STATE: _PrivateEvidenceKind.STATE,
        EvidenceKind.HISTORY: _PrivateEvidenceKind.HISTORY,
        EvidenceKind.COMPLETE_HISTORY: _PrivateEvidenceKind.COMPLETE_HISTORY,
        EvidenceKind.SUBJECT_RESULT: _PrivateEvidenceKind.SUBJECT_RESULT,
    }
    return _EvidenceRequirement(mapping[value.kind], value.surface)


def _public_observation[StateT, EventT](
    value: _SurfaceObservation[StateT, EventT],
) -> Observation[StateT, EventT]:
    return Observation(state=value.state, history=value.history)


def _private_observation[StateT, EventT](
    value: Observation[StateT, EventT],
) -> _SurfaceObservation[StateT, EventT]:
    return _SurfaceObservation(state=value.state, history=value.history)


def _validate_decision(value: object) -> Decision:
    if not isinstance(value, Decision):
        raise TypeError("contract evaluator returned an invalid decision")
    passed = _require_bool(value.passed)
    _validate_token(value.reason_code, category="invalid_reason_code")
    return Decision(passed=passed, reason_code=value.reason_code)


def _require_world_session(value: object) -> WorldSession[object, object, object, object]:
    if not isinstance(value, WorldSession):
        raise TypeError("world factory returned an invalid session")
    return cast("WorldSession[object, object, object, object]", value)


def _require_world(value: object) -> World[object, object, object, object]:
    if not isinstance(value, World):
        raise TypeError("world provision returned an invalid world")
    return cast("World[object, object, object, object]", value)


def _private_case[InputT, ResultT, StateT, EventT](
    case: Case[InputT, ResultT, StateT, EventT],
    operation_key: str | None,
) -> _CaseDefinition[InputT, ResultT, StateT, EventT, JsonValue, JsonValue]:
    def world_factory(
        trial_id: _PrivateTrialId,
    ) -> _PrivateWorldSession[InputT, ResultT, StateT, EventT]:
        session = cast(
            "WorldSession[InputT, ResultT, StateT, EventT]",
            _require_world_session(case.world_factory(TrialId(trial_id.value))),
        )
        public_world: World[InputT, ResultT, StateT, EventT] | None = None

        def provision() -> _PrivateWorld[InputT, ResultT, StateT, EventT]:
            nonlocal public_world
            provisioned = cast(
                "World[InputT, ResultT, StateT, EventT]",
                _require_world(session.provision()),
            )
            public_world = provisioned

            def observe() -> _SurfaceObservation[StateT, EventT]:
                return _private_observation(provisioned.observe())

            def validate_fixture(value: _SurfaceObservation[StateT, EventT]) -> None:
                provisioned.validate_fixture(_public_observation(value))

            return _PrivateWorld(
                invoke=provisioned.invoke,
                observe=observe,
                validate_fixture=validate_fixture,
            )

        def cleanup(
            _world: _PrivateWorld[InputT, ResultT, StateT, EventT] | None,
        ) -> None:
            session.cleanup(public_world)

        return _PrivateWorldSession(provision=provision, cleanup=cleanup)

    def canonicalize_state(value: StateT) -> JsonValue:
        canonical = case.canonicalize_state(value)
        _validate_json(canonical)
        return canonical

    def canonicalize_event(value: EventT) -> JsonValue:
        canonical = case.canonicalize_event(value)
        _validate_json(canonical)
        return canonical

    clean_assertions: list[_CleanAssertion[InputT, ResultT, StateT, EventT]] = []
    for item in case.clean_assertions:
        public_assertion = item

        def evaluate_clean(
            context: _CleanEvaluationContext[InputT, ResultT, StateT, EventT],
            assertion: CleanAssertion[InputT, ResultT, StateT, EventT] = public_assertion,
        ) -> _EvaluationDecision:
            decision = _validate_decision(
                assertion.evaluate(
                    CleanContext(
                        input=context.input,
                        baseline=_public_observation(context.baseline),
                        final=_public_observation(context.final),
                        history_delta=context.history_delta,
                        returned_result=context.returned_result,
                    )
                )
            )
            return _EvaluationDecision(decision.passed, decision.reason_code)

        clean_assertions.append(
            _CleanAssertion(
                name=item.name,
                requirements=tuple(_private_requirement(value) for value in item.requirements),
                evaluate=evaluate_clean,
            )
        )

    retry_invariants: list[_RetryInvariant[InputT, ResultT, StateT, EventT, JsonValue]] = []
    for item in case.retry_invariants:
        public_invariant = item

        def evaluate_retry(
            context: _RetryEvaluationContext[InputT, ResultT, StateT, EventT, JsonValue],
            invariant: RetryInvariant[InputT, ResultT, StateT, EventT] = public_invariant,
        ) -> _EvaluationDecision:
            clean = context.clean
            decision = _validate_decision(
                invariant.evaluate(
                    RetryContext(
                        input=context.input,
                        clean=CleanContext(
                            input=clean.input,
                            baseline=_public_observation(clean.baseline),
                            final=_public_observation(clean.final),
                            history_delta=clean.history_delta,
                            returned_result=clean.returned_result,
                        ),
                        perturbed_baseline=_public_observation(context.perturbed_baseline),
                        perturbed_final=_public_observation(context.perturbed_final),
                        perturbed_history_delta=context.perturbed_history_delta,
                        canonical_clean_history_delta=context.canonical_clean_history_delta,
                        canonical_perturbed_history_delta=context.canonical_perturbed_history_delta,
                        subject_result=context.run.subject_result,
                        attempt_count=len(context.run.attempts),
                        attempt_outcomes=tuple(value.outcome for value in context.run.attempts),
                        boundary_name=context.run.harness.boundary_name,
                        boundary_reached=bool(context.run.harness.reached_attempt_ids),
                        fault_injected=True,
                    )
                )
            )
            return _EvaluationDecision(decision.passed, decision.reason_code)

        retry_invariants.append(
            _RetryInvariant(
                name=item.name,
                requirements=tuple(_private_requirement(value) for value in item.requirements),
                evaluate=evaluate_retry,
            )
        )

    return _CaseDefinition(
        subject_name=case.subject_name,
        input=case.input,
        operation_id=_OperationId(case.operation_id),
        operation_key_selector=lambda _input: operation_key,
        world_factory=world_factory,
        coverage=_SurfaceCoverage(
            surface=case.coverage.surface,
            state=case.coverage.state,
            history=case.coverage.history,
            complete_history=case.coverage.complete_history,
            observation_interval=case.coverage.observation_interval,
            provenance=case.coverage.provenance,
            limitations=case.coverage.limitations,
        ),
        canonicalize_state=canonicalize_state,
        canonicalize_event=canonicalize_event,
        clean_assertions=tuple(clean_assertions),
        retry_invariants=tuple(retry_invariants),
        scope_limitations=case.limitations,
    )


def _axis_status(value: _PrivateAxisStatus) -> AxisStatus:
    return AxisStatus(value.value)


def _invariant_verdict(value: _PrivateInvariantVerdict) -> InvariantVerdict:
    return InvariantVerdict(value.value)


def _reason_code(value: _InvariantResult) -> str:
    explanation = value.explanation
    fixed = (
        ("a valid confirmation did not reproduce", "confirmation_not_reproduced"),
        ("confirmation infrastructure or evaluation failed", "confirmation_error"),
        ("candidate did not receive exactly two confirmations", "confirmation_count_error"),
        ("was not reached", "boundary_not_reached"),
        ("fault was caught before configured retry", "fault_not_propagated"),
    )
    for fragment, code in fixed:
        if fragment in explanation:
            return code
    candidate = explanation.split(";", maxsplit=1)[0]
    if _TOKEN.fullmatch(candidate) is not None:
        return candidate
    if value.missing_evidence:
        return "missing_evidence"
    fallback = {
        _PrivateInvariantVerdict.PASS: "contract_satisfied",
        _PrivateInvariantVerdict.FAIL: "confirmed_contradiction",
        _PrivateInvariantVerdict.INCONCLUSIVE: "insufficient_evidence",
        _PrivateInvariantVerdict.ERROR: "evaluation_error",
    }
    return fallback[value.verdict]


def _axis_report(value: _AxisResult) -> AxisReport:
    return AxisReport(
        status=_axis_status(value.status),
        invariants=tuple(
            InvariantReport(
                name=item.name,
                verdict=_invariant_verdict(item.verdict),
                reason_code=_reason_code(item),
                missing_evidence=item.missing_evidence,
            )
            for item in value.invariants
        ),
    )


def _cleanup_status(value: _PrivateCleanupStatus) -> CleanupStatus:
    return CleanupStatus(value.value)


def _error_category(phase: str) -> str:
    if phase.startswith(("clean_evaluator:", "retry_evaluator:")):
        return "contract_evaluator"
    mapping = {
        "world_factory": "world_factory",
        "provision": "world_provision",
        "cleanup_after_provision": "world_cleanup",
        "baseline_observer": "observer",
        "final_observer": "observer",
        "fixture_validation": "fixture",
        "subject_invocation": "subject",
        "baseline_comparison": "baseline",
        "lost_result_schedule": "fault_controller",
        "fault_propagation": "fault_controller",
        "history_validation": "history",
        "clean_evaluation": "contract_evaluator",
        "retry_evaluation": "contract_evaluator",
        "cleanup": "world_cleanup",
        "clean_reference": "clean_reference",
    }
    return mapping.get(phase, "infrastructure")


def _safe_phase(phase: str) -> str:
    if ":" in phase:
        prefix, suffix = phase.split(":", maxsplit=1)
        if prefix in {"clean_evaluator", "retry_evaluator"} and _TOKEN.fullmatch(suffix):
            return f"{prefix}:{suffix}"
    return phase if _TOKEN.fullmatch(phase) else "infrastructure"


def _trial_report(
    *,
    trial_id: str,
    baseline_history_count: int | None,
    attempts: tuple[AttemptReport, ...],
    cleanup: _PrivateCleanupStatus,
) -> TrialReport:
    return TrialReport(
        trial_id=trial_id,
        baseline_history_count=baseline_history_count,
        attempts=attempts,
        cleanup=_cleanup_status(cleanup),
    )


def _pair_report[ResultT, StateT, EventT](
    value: _PairResult[ResultT, StateT, EventT],
) -> PairReport:
    clean_baseline_history_count: int | None = None
    clean_attempts: tuple[AttemptReport, ...] = ()
    if value.clean is not None:
        clean_baseline_history_count = len(value.clean.baseline.history)
        attempt = value.clean.attempt
        clean_attempts = (
            AttemptReport(
                delivery_id=attempt.identity.delivery_id.value,
                attempt_id=attempt.identity.attempt_id.value,
                outcome=attempt.outcome,
                boundary_reached=False,
                fault_injected=False,
                observed_history_count=len(attempt.observation.history),
            ),
        )
    perturbed_baseline_history_count: int | None = None
    perturbed_attempts: tuple[AttemptReport, ...] = ()
    if value.perturbed is not None:
        perturbed_baseline_history_count = len(value.perturbed.baseline.history)
        reached = frozenset(value.perturbed.harness.reached_attempt_ids)
        injected = value.perturbed.harness.injected_attempt_id
        perturbed_attempts = tuple(
            AttemptReport(
                delivery_id=attempt.identity.delivery_id.value,
                attempt_id=attempt.identity.attempt_id.value,
                outcome=attempt.outcome,
                boundary_reached=attempt.identity.attempt_id in reached,
                fault_injected=attempt.identity.attempt_id == injected,
                observed_history_count=len(attempt.observation.history),
            )
            for attempt in value.perturbed.attempts
        )
    return PairReport(
        pair_id=value.pair_id,
        clean=_trial_report(
            trial_id=f"{value.pair_id}/clean",
            baseline_history_count=clean_baseline_history_count,
            attempts=clean_attempts,
            cleanup=value.clean_cleanup,
        ),
        perturbed=_trial_report(
            trial_id=f"{value.pair_id}/perturbed",
            baseline_history_count=perturbed_baseline_history_count,
            attempts=perturbed_attempts,
            cleanup=value.perturbed_cleanup,
        ),
        errors=tuple(
            ErrorSummary(
                axis=error.axis,
                phase=_safe_phase(error.phase),
                category=_error_category(error.phase),
            )
            for error in value.errors
        ),
    )


def _project[InputT, ResultT, StateT, EventT](
    scope: ScopeFingerprint,
    value: _CaseResult[InputT, ResultT, StateT, EventT],
) -> CaseReport:
    return CaseReport(
        scope=scope,
        applicability=ApplicabilityStatus.APPLICABLE,
        applicable_pair_count=1,
        clean_validity=_axis_report(value.clean_validity),
        retry_safety=_axis_report(value.retry_safety),
        primary=_pair_report(value.primary),
        confirmations=tuple(_pair_report(item) for item in value.confirmations),
        report_error=None,
    )


def _report_error(
    *,
    phase: Literal["configuration", "preflight", "applicability"],
    category: str,
    scope: ScopeFingerprint | None,
) -> CaseReport:
    return CaseReport(
        scope=scope,
        applicability=ApplicabilityStatus.NOT_EVALUATED,
        applicable_pair_count=0,
        clean_validity=None,
        retry_safety=None,
        primary=None,
        confirmations=(),
        report_error=ReportError(phase=phase, category=category),
    )


def _require_operation_key(value: object) -> str | None:
    if value is not None and not isinstance(value, str):
        raise _ConfigurationError("invalid_operation_key")
    if isinstance(value, str):
        if len(value) > _MAX_OPERATION_KEY_CHARS:
            raise _ConfigurationError("invalid_operation_key")
        try:
            value.encode("utf-8")
        except UnicodeError as error:
            raise _ConfigurationError("invalid_operation_key") from error
    return value


def _require_applicability(value: object) -> bool:
    return _require_bool(value)


def run_case[InputT, ResultT, StateT, EventT](
    case: Case[InputT, ResultT, StateT, EventT],
) -> CaseReport:
    """Run one trusted-local case and return only a redacted result projection."""

    scope: ScopeFingerprint | None = None
    try:
        _validate_case(case)
        try:
            canonical_input = case.canonicalize_input(case.input)
        except Exception as error:
            raise _ConfigurationError("invalid_input_projection") from error
        _validate_json(canonical_input)
        try:
            operation_key = case.operation_key_selector(case.input)
        except Exception as error:
            raise _ConfigurationError("invalid_operation_key") from error
        operation_key = _require_operation_key(operation_key)
        scope = _build_scope(case, canonical_input, operation_key)
    except _ConfigurationError as error:
        return _report_error(phase="configuration", category=error.category, scope=None)
    except Exception:
        return _report_error(phase="configuration", category="invalid_case", scope=None)

    try:
        case.preflight()
    except Exception:
        return _report_error(phase="preflight", category="preflight_callback", scope=scope)

    try:
        applicable = _require_applicability(case.applicable(case.input))
    except Exception:
        return _report_error(
            phase="applicability",
            category="applicability_callback",
            scope=scope,
        )

    if not applicable:
        clean_status = (
            AxisStatus.UNVERIFIED if not case.clean_assertions else AxisStatus.INCONCLUSIVE
        )
        return CaseReport(
            scope=scope,
            applicability=ApplicabilityStatus.NOT_APPLICABLE,
            applicable_pair_count=0,
            clean_validity=AxisReport(clean_status, ()),
            retry_safety=AxisReport(AxisStatus.INCONCLUSIVE, ()),
            primary=None,
            confirmations=(),
            report_error=None,
        )

    private_case = _private_case(case, operation_key)
    return _project(scope, _evaluate_case(private_case))


def render_terminal(report: CaseReport) -> str:
    """Render a bounded deterministic terminal projection without an overall status."""

    lines = ["EffectProbe experimental Python case"]
    if report.scope is not None:
        lines.extend(
            (
                f"case: {report.scope.case_name}",
                f"subject: {report.scope.subject_name}",
                f"operation: {report.scope.operation_id}",
                f"scope_fingerprint: {report.scope.scope_sha256}",
                f"schedule: {report.scope.schedule}",
                f"observer_surface: {report.scope.coverage.surface}",
            )
        )
    lines.append(f"applicability: {report.applicability.value}")
    if report.report_error is not None:
        lines.append(
            f"report_error: ERROR phase={report.report_error.phase} "
            f"category={report.report_error.category}"
        )
        lines.append("clean_validity: not_evaluated")
        lines.append("retry_safety: not_evaluated")
        return "\n".join(lines) + "\n"

    assert report.clean_validity is not None
    assert report.retry_safety is not None
    lines.append(f"applicable_pair_count: {report.applicable_pair_count}")
    for name, axis in (
        ("clean_validity", report.clean_validity),
        ("retry_safety", report.retry_safety),
    ):
        lines.append(f"{name}: {axis.status.value}")
        for item in axis.invariants:
            missing = f" missing={','.join(item.missing_evidence)}" if item.missing_evidence else ""
            lines.append(f"  {item.name}: {item.verdict.value} reason={item.reason_code}{missing}")
    lines.append(f"confirmation_pair_count: {len(report.confirmations)}")
    if report.primary is not None:
        all_pairs = (report.primary, *report.confirmations)
        trials = tuple(trial for pair in all_pairs for trial in (pair.clean, pair.perturbed))
        trial_count = sum(
            bool(trial.attempts) or trial.cleanup is not CleanupStatus.NOT_ATTEMPTED
            for trial in trials
        )
        attempt_count = sum(
            len(pair.clean.attempts) + len(pair.perturbed.attempts) for pair in all_pairs
        )
        cleanup_errors = sum(trial.cleanup is CleanupStatus.ERROR for trial in trials)
        lines.append(f"trial_count: {trial_count}")
        lines.append(f"attempt_count: {attempt_count}")
        lines.append(f"cleanup_error_count: {cleanup_errors}")
        primary_history_counts = (
            (
                str(report.primary.perturbed.baseline_history_count),
                *(
                    str(attempt.observed_history_count)
                    for attempt in report.primary.perturbed.attempts
                ),
            )
            if report.primary.perturbed.baseline_history_count is not None
            else ()
        )
        if primary_history_counts:
            lines.append(f"primary_perturbed_history_counts: {'/'.join(primary_history_counts)}")
        lines.extend(
            f"error: axis={error.axis or 'report'} phase={error.phase} category={error.category}"
            for pair in all_pairs
            for error in pair.errors
        )
    return "\n".join(lines) + "\n"


__all__ = (
    "ApplicabilityStatus",
    "AttemptReport",
    "AxisReport",
    "AxisStatus",
    "Canonicalization",
    "Case",
    "CaseReport",
    "CleanAssertion",
    "CleanContext",
    "CleanupStatus",
    "ContractDescriptor",
    "Decision",
    "DeliverResult",
    "ErrorSummary",
    "EvidenceKind",
    "EvidenceRequirement",
    "InvariantReport",
    "InvariantVerdict",
    "JsonValue",
    "Observation",
    "ObserverCoverage",
    "PairReport",
    "ReportError",
    "RetryContext",
    "RetryInvariant",
    "RuntimeFingerprint",
    "ScopeFingerprint",
    "TrialId",
    "TrialReport",
    "World",
    "WorldSession",
    "render_terminal",
    "run_case",
)
