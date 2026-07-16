"""Capability, parsing, and failure-path tests for private MCP stdio support."""

import os
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.types import CallToolResult

import effectprobe._mcp_refund_comparison as mcp_comparison
from effectprobe._lost_result import (
    MCP_CLIENT_RESULT_LOSS,
    BoundaryNotReached,
    FaultNotPropagated,
    OperationId,
    run_commit_then_lose_first_provider_result,
)
from effectprobe._mcp_refund_comparison import (
    ServerVariant,
    build_mcp_refund_case,
    mcp_refund_server_config,
)
from effectprobe._mcp_stdio import (
    McpCapabilityError,
    McpLifecycleError,
    McpPreflightError,
    McpResultError,
    McpStdioToolClient,
    McpStdioToolConfig,
    McpToolCallError,
    parse_structured_tool_result,
    preflight_mcp_tool,
)
from effectprobe._semantic_kernel import AxisStatus, CasePreflightError, evaluate_case


def _preflight_config(
    variant: ServerVariant = "normal",
    *,
    database: Path = Path(os.devnull),
) -> McpStdioToolConfig:
    return mcp_refund_server_config(
        database=database,
        mode="keyed",
        variant=variant,
    )


def test_mcp_preflight_accepts_the_declared_tool_contract_without_effect_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / "must-not-be-created.sqlite3"
    config = _preflight_config(database=database)

    preflight_mcp_tool(config)

    assert not database.exists()


@pytest.mark.parametrize(
    ("variant", "message"),
    [
        ("no_tools", "tools capability"),
        ("wrong_schema", "required input fields"),
        ("wrong_input_type", "incompatible input types"),
        ("wrong_output_type", "incompatible output types"),
        ("unstructured", "required output fields"),
    ],
)
def test_mcp_preflight_rejects_unsupported_capabilities(
    variant: ServerVariant, message: str
) -> None:
    with pytest.raises(McpPreflightError, match=message) as error:
        preflight_mcp_tool(_preflight_config(variant))

    assert isinstance(error.value.cause, McpCapabilityError)


def test_mcp_preflight_rejects_a_missing_named_tool() -> None:
    config = replace(_preflight_config(), tool_name="missing_refund")

    with pytest.raises(McpPreflightError, match="did not list tool"):
        preflight_mcp_tool(config)


@pytest.mark.parametrize(
    ("variant", "timeout_seconds"),
    [("discovery_hang", 1.0), ("discovery_exit", 10.0)],
)
def test_mcp_preflight_normalizes_discovery_timeout_and_process_exit(
    variant: ServerVariant,
    timeout_seconds: float,
) -> None:
    config = replace(_preflight_config(variant), timeout_seconds=timeout_seconds)

    with pytest.raises(McpPreflightError, match="discovery failed") as error:
        preflight_mcp_tool(config)

    assert isinstance(error.value.cause, McpLifecycleError)
    assert error.value.cause.stage == "discovery"


@pytest.mark.parametrize(
    "config",
    [
        replace(_preflight_config(), command="effectprobe-command-that-does-not-exist"),
        replace(
            _preflight_config(),
            command=sys.executable,
            args=("-c", "raise SystemExit(3)"),
        ),
    ],
)
def test_mcp_preflight_maps_executable_and_initialization_failures(
    config: McpStdioToolConfig,
) -> None:
    with pytest.raises(McpPreflightError, match="startup failed"):
        preflight_mcp_tool(config)


def test_case_preflight_failure_creates_no_effect_world_or_axes() -> None:
    case, tracker = build_mcp_refund_case(keyed=True, preflight_variant="no_tools")

    with pytest.raises(CasePreflightError) as error:
        evaluate_case(case)

    assert isinstance(error.value.cause, McpPreflightError)
    assert tracker.preflight_attempts == 1
    assert tracker.provision_attempts == 0
    assert tracker.cleanup_attempts == 0
    assert tracker.worlds == []


def test_unreached_mcp_client_result_boundary_is_not_a_retry_pass() -> None:
    with pytest.raises(BoundaryNotReached, match="mcp_client_result_delivery") as error:
        run_commit_then_lose_first_provider_result(
            operation_id=OperationId("operation/test"),
            invoke=lambda _deliver: "returned-without-boundary",
            observe=lambda: 0,
            schedule=MCP_CLIENT_RESULT_LOSS,
        )

    assert error.value.boundary_name == "mcp_client_result_delivery"


def test_swallowed_mcp_client_result_signal_is_an_infrastructure_error() -> None:
    def swallow_fault(deliver: Callable[[str], str]) -> str:
        try:
            return deliver("receipt")
        except Exception:
            return "fallback"

    with pytest.raises(FaultNotPropagated, match="mcp_client_result_delivery") as error:
        run_commit_then_lose_first_provider_result(
            operation_id=OperationId("operation/test"),
            invoke=swallow_fault,
            observe=lambda: 0,
            schedule=MCP_CLIENT_RESULT_LOSS,
        )

    assert error.value.boundary_name == "mcp_client_result_delivery"


def test_structured_result_parser_rejects_tool_errors_and_missing_evidence() -> None:
    with pytest.raises(McpToolCallError):
        parse_structured_tool_result(
            CallToolResult(content=[], isError=True),
            required_fields=frozenset(("receipt",)),
        )

    with pytest.raises(McpResultError, match="no structured content"):
        parse_structured_tool_result(
            CallToolResult(content=[], isError=False),
            required_fields=frozenset(("receipt",)),
        )

    with pytest.raises(McpResultError, match="lacks required fields"):
        parse_structured_tool_result(
            CallToolResult(content=[], structuredContent={}, isError=False),
            required_fields=frozenset(("receipt",)),
        )


@pytest.mark.parametrize(
    ("variant", "timeout_seconds"),
    [("tool_error", 10.0), ("tool_exit", 10.0), ("hang", 1.0)],
)
def test_mcp_tool_error_or_timeout_maps_to_axis_error(
    variant: ServerVariant,
    timeout_seconds: float,
) -> None:
    case, tracker = build_mcp_refund_case(
        keyed=True,
        world_variant=variant,
        timeout_seconds=timeout_seconds,
    )

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.ERROR
    assert result.retry_safety.status is AxisStatus.ERROR
    assert result.primary.clean is None
    assert tracker.cleanup_attempts == 1
    assert tracker.worlds[0].cleaned
    assert tracker.worlds[0].database_removed


def test_mcp_startup_exit_after_world_allocation_rolls_back_once() -> None:
    case, tracker = build_mcp_refund_case(keyed=True, world_variant="startup_exit")

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.ERROR
    assert result.retry_safety.status is AxisStatus.ERROR
    assert tracker.provision_attempts == 1
    assert tracker.cleanup_attempts == 1
    assert tracker.worlds[0].cleaned
    assert tracker.worlds[0].database_removed


def test_actual_mcp_client_cleanup_error_is_mapped_after_resources_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_exit = McpStdioToolClient.__exit__

    def fail_after_client_cleanup(
        client: McpStdioToolClient,
        *exc_info: object,
    ) -> None:
        original_exit(client, *exc_info)
        raise McpLifecycleError("cleanup", RuntimeError("configured client cleanup failure"))

    def skip_preflight(_config: McpStdioToolConfig) -> None:
        return

    monkeypatch.setattr(mcp_comparison, "preflight_mcp_tool", skip_preflight)
    monkeypatch.setattr(
        McpStdioToolClient,
        "__exit__",
        fail_after_client_cleanup,
    )
    case, tracker = build_mcp_refund_case(keyed=True)

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.ERROR
    assert result.retry_safety.status is AxisStatus.ERROR
    assert any(error.phase == "cleanup" for error in result.primary.errors)
    assert tracker.cleanup_attempts == 2
    assert all(world.database_removed for world in tracker.worlds)


def test_mcp_observer_failure_maps_to_clean_error_and_cleans_up() -> None:
    case, tracker = build_mcp_refund_case(
        keyed=True,
        observer_failure_trials=frozenset(("primary/clean",)),
    )

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.ERROR
    assert result.retry_safety.status is AxisStatus.ERROR
    assert any(error.phase == "final_observer" for error in result.primary.errors)
    assert tracker.cleanup_attempts == 1
    assert tracker.worlds[0].database_removed


def test_mcp_partial_provision_failure_rolls_back_once() -> None:
    case, tracker = build_mcp_refund_case(
        keyed=True,
        provision_failure_trials=frozenset(("primary/clean",)),
    )

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.ERROR
    assert tracker.provision_attempts == 1
    assert tracker.cleanup_attempts == 1
    assert tracker.worlds[0].cleaned
    assert tracker.worlds[0].database_removed


def test_mcp_cleanup_failure_prevents_retry_pass() -> None:
    case, tracker = build_mcp_refund_case(
        keyed=True,
        cleanup_failure_trials=frozenset(("primary/perturbed",)),
    )

    result = evaluate_case(case)

    assert result.clean_validity.status is AxisStatus.PASS
    assert result.retry_safety.status is AxisStatus.ERROR
    assert any(error.phase == "cleanup" for error in result.primary.errors)
    assert tracker.cleanup_attempts == 2
    assert all(world.database_removed for world in tracker.worlds)
