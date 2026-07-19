"""Private bounded report contract for the registered controlled MCP case."""

import json
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from effectprobe._evidence_artifact import (
    ArtifactCompatibilityError,
    JsonValue,
    artifact_payload,
    evidence_artifact_from_payload,
    write_evidence_artifact,
)
from effectprobe._mcp_evidence_replay import (
    inspect_mcp_evidence,
    read_mcp_evidence,
    record_mcp_evidence,
    replay_mcp_evidence,
    validate_mcp_artifact,
)
from effectprobe._mcp_refund_comparison import build_mcp_refund_case
from effectprobe._mcp_reports import (
    McpReport,
    McpReportInputError,
    McpReportRenderingError,
    ReportAxis,
    ReportInvariant,
    build_mcp_report,
    render_json_report,
    render_junit_report,
    render_terminal_report,
)
from effectprobe._semantic_kernel import evaluate_case


class _Recorded:
    def __init__(self, keyed: Path, unsafe: Path) -> None:
        self.keyed = keyed
        self.unsafe = unsafe


@pytest.fixture(scope="module")
def recorded(tmp_path_factory: pytest.TempPathFactory) -> _Recorded:
    directory = tmp_path_factory.mktemp("mcp-reports")
    keyed = directory / "keyed.json"
    unsafe = directory / "unsafe.json"
    record_mcp_evidence("keyed", keyed)
    record_mcp_evidence("unsafe", unsafe)
    return _Recorded(keyed, unsafe)


@pytest.fixture(scope="module")
def reports(recorded: _Recorded) -> tuple[McpReport, McpReport]:
    return build_mcp_report(recorded.keyed), build_mcp_report(recorded.unsafe)


def _object(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return cast("dict[str, JsonValue]", value)


def _array(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return cast("list[JsonValue]", value)


def _write_payload(path: Path, payload: dict[str, JsonValue]) -> None:
    artifact = evidence_artifact_from_payload(payload, validator=validate_mcp_artifact)
    write_evidence_artifact(path, artifact)


def _mutated_artifact(recorded: _Recorded, path: Path, mutation: str) -> Path:
    payload = artifact_payload(read_mcp_evidence(recorded.unsafe).artifact)
    compatibility = _object(payload["compatibility"])
    scope = _object(payload["scope"])
    evidence = _object(payload["evidence"])
    primary = _object(evidence["primary"])
    perturbed = _object(primary["perturbed"])
    if mutation == "not_reportable":
        scope["reportable"] = False
    elif mutation == "old_runner":
        scope["reportable"] = False
        _object(payload["producer"])["version"] = 1
        runner = _object(compatibility["runner"])
        runner["version"] = 1
        runner["components"] = [
            item
            for item in _array(runner["components"])
            if not str(_object(item)["path"]).endswith("/_mcp_reports.py")
        ]
    elif mutation == "cleanup":
        cleanup = _object(perturbed["cleanup"])
        cleanup["status"] = "error"
        cleanup["resource_closed"] = False
    elif mutation == "fault":
        attempts = _array(perturbed["attempts"])
        second_identity = _object(_object(attempts[1])["identity"])
        _object(perturbed["harness_truth"])["injected_attempt_id"] = second_identity["attempt_id"]
    elif mutation == "identity":
        attempts = _array(perturbed["attempts"])
        first_identity = _object(_object(attempts[0])["identity"])
        first_identity["delivery_id"] = first_identity["attempt_id"]
    elif mutation == "trial_identity":
        attempts = _array(perturbed["attempts"])
        first_identity = _object(_object(attempts[0])["identity"])
        first_identity["delivery_id"] = perturbed["trial_id"]
    elif mutation == "cross_pair_identity":
        primary_clean = _object(primary["clean"])
        primary_delivery = _object(_object(primary_clean["attempt"])["identity"])["delivery_id"]
        confirmation = _object(_array(evidence["confirmations"])[0])
        confirmation_clean = _object(confirmation["clean"])
        _object(_object(confirmation_clean["attempt"])["identity"])["delivery_id"] = (
            primary_delivery
        )
    elif mutation == "request_later_identity":
        primary_clean = _object(primary["clean"])
        attempts = _array(perturbed["attempts"])
        later_attempt_id = _object(_object(attempts[1])["identity"])["attempt_id"]
        clean_transport = _array(primary_clean["transport_truth"])
        _object(clean_transport[0])["mcp_request_id"] = later_attempt_id
    elif mutation == "request_confirmation_identity":
        primary_clean = _object(primary["clean"])
        confirmation = _object(_array(evidence["confirmations"])[0])
        confirmation_attempt = _object(_object(confirmation["clean"])["attempt"])
        confirmation_attempt_id = _object(confirmation_attempt["identity"])["attempt_id"]
        clean_transport = _array(primary_clean["transport_truth"])
        _object(clean_transport[0])["mcp_request_id"] = confirmation_attempt_id
    elif mutation == "operation_key":
        scope["operation_key"] = "different-selected-key"
    elif mutation == "result":
        attempts = _array(perturbed["attempts"])
        second_view = _object(_object(attempts[1])["subject_view"])
        _object(second_view["returned_result"])["refund_id"] = "refund/uncommitted"
        _object(perturbed["subject_result"])["refund_id"] = "refund/uncommitted"
    elif mutation == "confirmation":
        evidence["confirmations"] = _array(evidence["confirmations"])[:1]
    elif mutation == "aggregation":
        retry = _object(_object(payload["outcomes"])["retry_safety"])
        retry["status"] = "PASS"
        invariant = _object(_array(retry["invariants"])[0])
        invariant["verdict"] = "PASS"
        invariant["explanation"] = (
            "MCP retry preserved the clean committed-effect and receipt semantics"
        )
    elif mutation == "limitations":
        scope["limitations"] = _array(scope["limitations"])[:-1]
    elif mutation == "redaction":
        _object(payload["redaction"])["omitted_categories"] = []
    elif mutation == "provenance":
        _object(payload["provenance"])["provider"] = "unregistered provider"
    elif mutation == "control_text":
        _object(compatibility["runtime"])["operating_system_release"] = (
            "\x1b[31mhostile-terminal-control"
        )
    else:
        raise AssertionError(f"unknown mutation: {mutation}")
    _write_payload(path, payload)
    return path


def test_capture_promotes_only_the_completed_artifact(recorded: _Recorded) -> None:
    case, _tracker = build_mcp_refund_case(keyed=True)
    raw = evaluate_case(case)
    payload = artifact_payload(read_mcp_evidence(recorded.keyed).artifact)

    assert raw.scope.reportable is False
    assert _object(payload["scope"])["reportable"] is True
    assert _object(payload["producer"])["version"] == 2
    assert _object(_object(payload["compatibility"])["runner"])["version"] == 2


def test_report_models_preserve_axes_scope_history_confirmation_and_identity(
    reports: tuple[McpReport, McpReport],
) -> None:
    keyed, unsafe = reports

    assert (keyed.clean_validity.status, keyed.retry_safety.status) == ("PASS", "PASS")
    assert (unsafe.clean_validity.status, unsafe.retry_safety.status) == ("PASS", "FAIL")
    assert keyed.applicable_pair_count == unsafe.applicable_pair_count == 1
    assert keyed.confirmation_count == 0
    assert unsafe.confirmation_count == 2
    assert tuple(item.history_delta_count for item in keyed.trials) == (1, 1)
    assert tuple(item.history_delta_count for item in unsafe.trials) == (1, 2, 1, 2, 1, 2)
    assert all(item.cleanup_status == "pass" for item in (*keyed.trials, *unsafe.trials))
    assert all(
        item.fault_boundary == "mcp_client_result_delivery"
        and item.fault_reached
        and item.injected_attempt_ordinal == 1
        for item in (*keyed.trials, *unsafe.trials)
        if item.kind == "perturbed"
    )
    assert keyed.scope.logical_operation_id == "operation/refund-001"
    assert keyed.scope.operation_key == "refund-key-001"
    assert keyed.scope.observer.complete_history is True
    assert keyed.scope.contract.retry_invariants[0].requirements[1].kind == "complete_history"
    assert keyed.compatibility.producer_version == 2
    assert keyed.compatibility.subject_components
    assert keyed.compatibility.runner_components
    assert keyed.compatibility.installed_locked_distributions


def test_terminal_json_and_junit_preserve_bounded_axis_meanings(
    reports: tuple[McpReport, McpReport],
) -> None:
    keyed, unsafe = reports
    keyed_terminal = render_terminal_report(keyed)
    unsafe_terminal = render_terminal_report(unsafe)

    assert "clean_validity=PASS" in keyed_terminal
    assert "retry_safety=PASS: Every declared retry invariant evaluated PASS" in keyed_terminal
    assert "1 applicable recorded input and scenario pairs" in keyed_terminal
    assert "limited to the recorded subject, contract, inputs, environment" in keyed_terminal
    assert "retry_safety=FAIL" in unsafe_terminal
    assert "reproduced in two confirmations" in unsafe_terminal
    assert "history_delta_count=2" in unsafe_terminal
    assert "overall" not in keyed_terminal.lower()
    assert "exactly once" not in keyed_terminal.lower()

    keyed_json = json.loads(render_json_report(keyed))
    unsafe_json = json.loads(render_json_report(unsafe))
    assert keyed_json["schema"] == {"name": "effectprobe.private.report", "version": 1}
    assert keyed_json["axes"]["clean_validity"]["status"] == "PASS"
    assert keyed_json["axes"]["retry_safety"]["status"] == "PASS"
    assert unsafe_json["axes"]["retry_safety"]["status"] == "FAIL"
    assert "overall" not in keyed_json
    assert keyed_json["scope"]["logical_operation_id"] == "operation/refund-001"
    assert keyed_json["scope"]["operation_key"] == "refund-key-001"
    assert keyed_json["compatibility"]["producer"]["version"] == 2
    assert keyed_json["compatibility"]["dependencies"]["lock_sha256"]
    assert keyed_json["compatibility"]["runtime"]["python_version"]

    keyed_junit = ET.fromstring(render_junit_report(keyed))
    unsafe_junit = ET.fromstring(render_junit_report(unsafe))
    assert keyed_junit.attrib == {
        "name": "effectprobe.controlled_mcp",
        "tests": "2",
        "failures": "0",
        "errors": "0",
        "skipped": "0",
        "time": "0",
    }
    assert unsafe_junit.attrib["tests"] == "2"
    assert unsafe_junit.attrib["failures"] == "1"
    assert unsafe_junit.find(".//failure") is not None
    properties = {
        item.attrib["name"]: item.attrib["value"]
        for item in keyed_junit.findall("./properties/property")
    }
    assert properties["effectprobe.clean_validity"] == "PASS"
    assert properties["effectprobe.retry_safety"] == "PASS"
    assert properties["effectprobe.dependency_lock_sha256"]
    assert properties["effectprobe.runtime.mcp_protocol_revision"]


def test_all_formats_are_deterministic_for_one_immutable_artifact(
    reports: tuple[McpReport, McpReport],
) -> None:
    for report in reports:
        assert (
            tuple(render_terminal_report(report) for _ in range(10))
            == (render_terminal_report(report),) * 10
        )
        assert (
            tuple(render_json_report(report) for _ in range(10))
            == (render_json_report(report),) * 10
        )
        assert (
            tuple(render_junit_report(report) for _ in range(10))
            == (render_junit_report(report),) * 10
        )
        rendered = render_json_report(report)
        assert (
            rendered
            == json.dumps(
                json.loads(rendered),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "not_reportable",
        "old_runner",
        "cleanup",
        "fault",
        "identity",
        "trial_identity",
        "cross_pair_identity",
        "request_later_identity",
        "request_confirmation_identity",
        "operation_key",
        "result",
        "confirmation",
        "aggregation",
        "limitations",
        "redaction",
        "provenance",
        "control_text",
    ],
)
def test_report_builder_refuses_ineligible_or_inconsistent_artifacts(
    tmp_path: Path, recorded: _Recorded, mutation: str
) -> None:
    path = _mutated_artifact(recorded, tmp_path / f"{mutation}.json", mutation)

    with pytest.raises(McpReportInputError):
        build_mcp_report(path)


def test_runner_v1_artifact_remains_inspectable_but_report_and_replay_refuse_before_execution(
    tmp_path: Path, recorded: _Recorded
) -> None:
    old = _mutated_artifact(recorded, tmp_path / "runner-v1.json", "old_runner")
    executed = False

    def fail_if_executed(_mode: str) -> tuple[object, object]:
        nonlocal executed
        executed = True
        raise AssertionError("replay executed despite runner mismatch")

    assert inspect_mcp_evidence(old).retry_safety == "FAIL"
    old_payload = artifact_payload(read_mcp_evidence(old).artifact)
    old_components = _array(_object(_object(old_payload["compatibility"])["runner"])["components"])
    assert all(
        not str(_object(item)["path"]).endswith("/_mcp_reports.py") for item in old_components
    )
    with pytest.raises(McpReportInputError, match=r"runner|reportable"):
        build_mcp_report(old)
    with pytest.raises(ArtifactCompatibilityError):
        replay_mcp_evidence(old, tmp_path / "child.json", run=fail_if_executed)  # type: ignore[arg-type]
    assert executed is False


def test_renderers_map_inconclusive_error_and_unverified_without_collapsing_axes(
    reports: tuple[McpReport, McpReport],
) -> None:
    keyed, _unsafe = reports
    inconclusive = replace(
        keyed,
        retry_safety=ReportAxis(
            "INCONCLUSIVE",
            (
                ReportInvariant(
                    "missing_history",
                    "INCONCLUSIVE",
                    "required retry evidence is unavailable",
                    ("refunds:complete_history",),
                ),
            ),
        ),
    )
    errored = replace(
        keyed,
        retry_safety=ReportAxis(
            "ERROR",
            (ReportInvariant("observer_error", "ERROR", "observer malfunctioned"),),
        ),
    )
    unverified = replace(keyed, clean_validity=ReportAxis("UNVERIFIED", ()))

    assert "retry_safety=INCONCLUSIVE: refunds:complete_history" in render_terminal_report(
        inconclusive
    )
    assert "retry_safety=ERROR: observer malfunctioned" in render_terminal_report(errored)
    assert "clean_validity=UNVERIFIED: No clean functional assertions" in render_terminal_report(
        unverified
    )
    assert ET.fromstring(render_junit_report(inconclusive)).attrib["skipped"] == "1"
    assert ET.fromstring(render_junit_report(errored)).attrib["errors"] == "1"
    unverified_xml = ET.fromstring(render_junit_report(unverified))
    assert unverified_xml.attrib["skipped"] == "1"
    assert (
        unverified_xml.find("./testcase[@name='clean_contract_not_declared']/skipped") is not None
    )
    assert (
        json.loads(render_json_report(inconclusive))["axes"]["clean_validity"]["status"] == "PASS"
    )
    assert (
        json.loads(render_json_report(inconclusive))["axes"]["retry_safety"]["status"]
        == "INCONCLUSIVE"
    )


def test_json_and_xml_escape_bounded_report_text(reports: tuple[McpReport, McpReport]) -> None:
    keyed, _unsafe = reports
    escaped = replace(
        keyed,
        retry_safety=ReportAxis(
            "ERROR",
            (ReportInvariant('name<&"', "ERROR", 'message <unsafe> & "quoted"'),),
        ),
        limitations=(*keyed.limitations, 'limitation <&"'),
    )

    json_value = json.loads(render_json_report(escaped))
    xml_value = ET.fromstring(render_junit_report(escaped))

    assert json_value["axes"]["retry_safety"]["invariants"][0]["name"] == 'name<&"'
    error = xml_value.find(".//error")
    assert error is not None
    assert error.attrib["message"] == 'message <unsafe> & "quoted"'
    assert xml_value.find(".//testcase[@name='name<&\"']") is not None


@pytest.mark.parametrize("mode", ["keyed", "unsafe"])
def test_report_builder_rejects_non_monotonic_history_between_attempts(
    tmp_path: Path, recorded: _Recorded, mode: str
) -> None:
    source = recorded.keyed if mode == "keyed" else recorded.unsafe
    payload = artifact_payload(read_mcp_evidence(source).artifact)
    primary = _object(_object(payload["evidence"])["primary"])
    perturbed = _object(primary["perturbed"])
    first = _object(_array(perturbed["attempts"])[0])
    first_history = _array(_object(first["observer_truth"])["history"])
    _object(first_history[0])["refund_id"] = "refund/replaced-before-final"
    _object(_object(perturbed["harness_truth"])["undelivered_result"])["refund_id"] = (
        "refund/replaced-before-final"
    )
    changed = tmp_path / f"{mode}-non-monotonic.json"
    _write_payload(changed, payload)

    with pytest.raises(McpReportInputError, match="append-only"):
        build_mcp_report(changed)


def test_renderers_reject_xml_and_terminal_control_characters(
    reports: tuple[McpReport, McpReport],
) -> None:
    keyed, _unsafe = reports
    controlled = replace(keyed, limitations=(*keyed.limitations, "illegal\x00control"))

    for render in (render_terminal_report, render_json_report, render_junit_report):
        with pytest.raises(McpReportRenderingError, match="unsafe text"):
            render(controlled)


def test_reports_omit_harness_transport_and_resource_canaries(
    tmp_path: Path, recorded: _Recorded
) -> None:
    payload = artifact_payload(read_mcp_evidence(recorded.unsafe).artifact)
    primary = _object(_object(payload["evidence"])["primary"])
    perturbed = _object(primary["perturbed"])
    request_canaries = (
        "SECRET-PID-481516",
        "SECRET-COMMAND--token=value",
        "SECRET-EXCEPTION-traceback",
        "SECRET-ENV-NAME=value",
        "SECRET-ABSOLUTE-PATH-/private/report.sqlite3",
    )
    evidence = _object(payload["evidence"])
    pairs = [primary, *(_object(item) for item in _array(evidence["confirmations"]))]
    transport_groups: list[list[JsonValue]] = []
    for pair in pairs:
        for trial_name in ("clean", "perturbed"):
            transport_groups.append(_array(_object(pair[trial_name])["transport_truth"]))
    for group, canary in zip(transport_groups, request_canaries, strict=False):
        _object(group[0])["mcp_request_id"] = canary
    undelivered_refund_id = str(
        _object(_object(perturbed["harness_truth"])["undelivered_result"])["refund_id"]
    )
    source = tmp_path / "request-canaries.json"
    _write_payload(source, payload)
    report = build_mcp_report(source)
    rendered = (
        render_terminal_report(report),
        render_json_report(report),
        render_junit_report(report),
    )

    for output in rendered:
        assert str(source) not in output
        assert undelivered_refund_id not in output
        assert all(canary not in output for canary in request_canaries)
        assert "undelivered_result" not in output
        assert "mcp_request_id" not in output
