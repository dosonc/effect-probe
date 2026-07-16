"""Private MCP stdio vulnerable and keyed refund comparison."""

import os
import sys
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from effectprobe._lost_result import MCP_CLIENT_RESULT_LOSS, OperationId, TrialId
from effectprobe._mcp_refund_store import (
    McpDelivery,
    McpRefundEvent,
    McpRefundObservation,
    initialize_refund_database,
    observe_refunds,
    read_mcp_deliveries,
)
from effectprobe._mcp_stdio import McpStdioToolClient, McpStdioToolConfig, preflight_mcp_tool
from effectprobe._refund_comparison import RefundCommand, RefundReceipt, RefundState
from effectprobe._semantic_kernel import (
    CaseDefinition,
    CaseResult,
    CleanAssertion,
    CleanEvaluationContext,
    EvaluationDecision,
    EvidenceKind,
    EvidenceRequirement,
    RetryEvaluationContext,
    RetryInvariant,
    SurfaceCoverage,
    World,
    WorldSession,
    evaluate_case,
)

_PAYMENT_ID = "payment/refund-001"
_PAYMENT_MINOR_UNITS = 10_000
_REFUND_MINOR_UNITS = 2_500
_OPERATION_ID = OperationId("operation/refund-001")
_OPERATION_KEY = "refund-key-001"
_TOOL_NAME = "refund"
_INPUT_SCHEMA = (
    ("payment_id", "string"),
    ("amount_minor_units", "integer"),
    ("operation_key", "string"),
)
_OUTPUT_SCHEMA = (
    ("refund_id", "string"),
    ("payment_id", "string"),
    ("amount_minor_units", "integer"),
)

type Mode = Literal["unsafe", "keyed"]
type ServerVariant = Literal[
    "normal",
    "no_tools",
    "wrong_schema",
    "wrong_input_type",
    "wrong_output_type",
    "discovery_hang",
    "discovery_exit",
    "startup_exit",
    "tool_error",
    "tool_exit",
    "unstructured",
    "hang",
]
type CanonicalMcpRefundState = tuple[str, int, int, int]
type CanonicalMcpRefundEvent = tuple[str, int, str | None]
type McpRefundCase = CaseDefinition[
    RefundCommand,
    RefundReceipt,
    RefundState,
    McpRefundEvent,
    CanonicalMcpRefundState,
    CanonicalMcpRefundEvent,
]
type McpRefundCaseResult = CaseResult[RefundCommand, RefundReceipt, RefundState, McpRefundEvent]


@dataclass(slots=True)
class McpRefundWorldRecord:
    """Test-visible lifecycle and transport evidence for one fresh world."""

    trial_id: TrialId
    database_path: Path
    deliveries: tuple[McpDelivery, ...] = ()
    cleaned: bool = False
    database_removed: bool = False


@dataclass(slots=True)
class McpRefundWorldTracker:
    """Track preflight, provisioning, delivery, and cleanup counts."""

    worlds: list[McpRefundWorldRecord] = field(default_factory=lambda: list[McpRefundWorldRecord]())
    preflight_attempts: int = 0
    provision_attempts: int = 0
    cleanup_attempts: int = 0


def mcp_refund_server_config(
    *,
    database: Path,
    mode: Mode,
    variant: ServerVariant = "normal",
    timeout_seconds: float = 10.0,
    command: str = sys.executable,
) -> McpStdioToolConfig:
    """Build private stdio configuration for the trusted local fixture."""

    return McpStdioToolConfig(
        command=command,
        args=(
            "-m",
            "effectprobe._mcp_refund_server",
            "--database",
            os.fspath(database),
            "--mode",
            mode,
            "--variant",
            variant,
        ),
        tool_name=_TOOL_NAME,
        required_input_schema=_INPUT_SCHEMA,
        required_output_schema=_OUTPUT_SCHEMA,
        timeout_seconds=timeout_seconds,
    )


def _receipt_from_payload(payload: dict[str, object]) -> RefundReceipt:
    refund_id = payload.get("refund_id")
    payment_id = payload.get("payment_id")
    amount = payload.get("amount_minor_units")
    if not isinstance(refund_id, str) or not isinstance(payment_id, str):
        raise ValueError("MCP refund receipt identifiers must be strings")
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise ValueError("MCP refund receipt amount must be an integer")
    return RefundReceipt(refund_id, payment_id, amount)


def _validate_fixture(observation: McpRefundObservation) -> None:
    expected = RefundState(_PAYMENT_ID, _PAYMENT_MINOR_UNITS, 0, 0)
    if observation.state != expected or observation.history:
        raise ValueError("MCP refund fixture is not fresh and empty")


def _canonicalize_state(state: RefundState) -> CanonicalMcpRefundState:
    return (
        state.payment_id,
        state.payment_minor_units,
        state.refunded_minor_units,
        state.key_index_size,
    )


def _canonicalize_event(event: McpRefundEvent) -> CanonicalMcpRefundEvent:
    return (event.payment_id, event.amount_minor_units, event.operation_key)


def _clean_refund_matches(
    context: CleanEvaluationContext[RefundCommand, RefundReceipt, RefundState, McpRefundEvent],
) -> EvaluationDecision:
    state_delta = (
        context.final.state.refunded_minor_units - context.baseline.state.refunded_minor_units
    )
    only_event = context.history_delta[0] if len(context.history_delta) == 1 else None
    receipt = context.returned_result
    passed = (
        only_event is not None
        and only_event.refund_id == receipt.refund_id
        and only_event.payment_id == context.input.payment_id == receipt.payment_id
        and only_event.amount_minor_units
        == context.input.amount_minor_units
        == receipt.amount_minor_units
        and state_delta == context.input.amount_minor_units
    )
    return EvaluationDecision(
        passed,
        "clean MCP refund committed and returned once"
        if passed
        else "clean MCP refund evidence differed from one requested commit",
    )


def _retry_refund_matches(
    context: RetryEvaluationContext[
        RefundCommand,
        RefundReceipt,
        RefundState,
        McpRefundEvent,
        CanonicalMcpRefundEvent,
    ],
) -> EvaluationDecision:
    clean_delta = (
        context.clean.final.state.refunded_minor_units
        - context.clean.baseline.state.refunded_minor_units
    )
    perturbed_delta = (
        context.perturbed_final.state.refunded_minor_units
        - context.perturbed_baseline.state.refunded_minor_units
    )
    only_event = (
        context.perturbed_history_delta[0] if len(context.perturbed_history_delta) == 1 else None
    )
    result = context.run.subject_result
    passed = (
        context.canonical_clean_history_delta == context.canonical_perturbed_history_delta
        and clean_delta == perturbed_delta == context.input.amount_minor_units
        and only_event is not None
        and result.refund_id == only_event.refund_id
        and result.payment_id == context.input.payment_id
        and result.amount_minor_units == context.input.amount_minor_units
    )
    return EvaluationDecision(
        passed,
        "MCP retry preserved the clean committed-effect and receipt semantics"
        if passed
        else "MCP retry produced an additional or inconsistent committed refund",
    )


def build_mcp_refund_case(
    *,
    keyed: bool,
    preflight_variant: ServerVariant = "normal",
    world_variant: ServerVariant = "normal",
    timeout_seconds: float = 10.0,
    preflight_timeout_seconds: float = 10.0,
    preflight_command: str = sys.executable,
    provision_failure_trials: frozenset[str] = frozenset(),
    observer_failure_trials: frozenset[str] = frozenset(),
    cleanup_failure_trials: frozenset[str] = frozenset(),
) -> tuple[McpRefundCase, McpRefundWorldTracker]:
    """Build one private MCP refund case and lifecycle tracker."""

    mode: Mode = "keyed" if keyed else "unsafe"
    tracker = McpRefundWorldTracker()
    command = RefundCommand(_PAYMENT_ID, _REFUND_MINOR_UNITS, _OPERATION_KEY)

    def preflight() -> None:
        tracker.preflight_attempts += 1
        config = mcp_refund_server_config(
            database=Path(os.devnull),
            mode=mode,
            variant=preflight_variant,
            timeout_seconds=preflight_timeout_seconds,
            command=preflight_command,
        )
        preflight_mcp_tool(config)

    def world_factory(
        trial_id: TrialId,
    ) -> WorldSession[RefundCommand, RefundReceipt, RefundState, McpRefundEvent]:
        resources = ExitStack()
        record: McpRefundWorldRecord | None = None
        observations = 0

        def provision() -> World[RefundCommand, RefundReceipt, RefundState, McpRefundEvent]:
            nonlocal record, observations
            tracker.provision_attempts += 1
            directory = Path(resources.enter_context(TemporaryDirectory()))
            database = directory / "refunds.sqlite3"
            initialize_refund_database(database)
            record = McpRefundWorldRecord(trial_id=trial_id, database_path=database)
            tracker.worlds.append(record)
            if trial_id.value in provision_failure_trials:
                raise RuntimeError(f"configured MCP provision failure: {trial_id.value}")
            client = resources.enter_context(
                McpStdioToolClient(
                    mcp_refund_server_config(
                        database=database,
                        mode=mode,
                        variant=world_variant,
                        timeout_seconds=timeout_seconds,
                    )
                )
            )

            def invoke(
                input_value: RefundCommand,
                deliver_result: Callable[[RefundReceipt], RefundReceipt],
            ) -> RefundReceipt:
                payload = client.call_tool(
                    {
                        "payment_id": input_value.payment_id,
                        "amount_minor_units": input_value.amount_minor_units,
                        "operation_key": input_value.operation_key,
                    }
                )
                return deliver_result(_receipt_from_payload(payload))

            def observe() -> McpRefundObservation:
                nonlocal observations
                observations += 1
                if trial_id.value in observer_failure_trials and observations >= 2:
                    raise RuntimeError(f"configured MCP observer failure: {trial_id.value}")
                return observe_refunds(database)

            return World(invoke=invoke, observe=observe, validate_fixture=_validate_fixture)

        def cleanup(
            _world: World[RefundCommand, RefundReceipt, RefundState, McpRefundEvent] | None,
        ) -> None:
            tracker.cleanup_attempts += 1
            try:
                if record is not None and record.database_path.exists():
                    record.deliveries = read_mcp_deliveries(record.database_path)
            finally:
                try:
                    resources.close()
                finally:
                    if record is not None:
                        record.cleaned = True
                        record.database_removed = not record.database_path.exists()
            if trial_id.value in cleanup_failure_trials:
                raise RuntimeError(f"configured MCP cleanup failure: {trial_id.value}")

        return WorldSession(provision=provision, cleanup=cleanup)

    surface = "refunds"
    state_requirement = EvidenceRequirement(EvidenceKind.STATE, surface)
    history_requirement = EvidenceRequirement(EvidenceKind.COMPLETE_HISTORY, surface)
    result_requirement = EvidenceRequirement(EvidenceKind.SUBJECT_RESULT)
    case = CaseDefinition(
        subject_name=f"mcp_{mode}_refund_tool",
        input=command,
        operation_id=_OPERATION_ID,
        operation_key_selector=lambda value: value.operation_key,
        world_factory=world_factory,
        coverage=SurfaceCoverage(
            surface=surface,
            state=True,
            history=True,
            complete_history=True,
            observation_interval="baseline_to_final",
            provenance="harness_controlled_sqlite_mcp_fixture",
            limitations=("does not validate production-provider semantics",),
        ),
        canonicalize_state=_canonicalize_state,
        canonicalize_event=_canonicalize_event,
        clean_assertions=(
            CleanAssertion(
                "one_mcp_refund_matches_request",
                (state_requirement, history_requirement, result_requirement),
                _clean_refund_matches,
            ),
        ),
        retry_invariants=(
            RetryInvariant(
                "no_additional_mcp_refund_after_retry",
                (state_requirement, history_requirement),
                _retry_refund_matches,
            ),
        ),
        scope_limitations=(
            "private provisional MCP stdio outcome",
            "trusted local harness-controlled server and SQLite provider",
            "MCP request identity is transport evidence, not a domain operation key",
            "code, dependency, runtime, environment, and evidence-schema fingerprints are deferred",
        ),
        schedule=MCP_CLIENT_RESULT_LOSS,
        preflight=preflight,
    )
    return case, tracker


def evaluate_mcp_vulnerable_refund() -> McpRefundCaseResult:
    case, _tracker = build_mcp_refund_case(keyed=False)
    return evaluate_case(case)


def evaluate_mcp_keyed_refund() -> McpRefundCaseResult:
    case, _tracker = build_mcp_refund_case(keyed=True)
    return evaluate_case(case)
