"""Installed command facade tests for the registered controlled MCP case."""

import json
import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from io import StringIO
from pathlib import Path
from typing import Never, cast

import pytest

from effectprobe import __all__, __version__
from effectprobe._cli import (
    _DEFAULT_SERVICES,  # pyright: ignore[reportPrivateUsage]
    _Services,  # pyright: ignore[reportPrivateUsage]
    main,
)
from effectprobe._evidence_artifact import (
    JsonValue,
    artifact_payload,
    evidence_artifact_from_payload,
    write_evidence_artifact,
)
from effectprobe._mcp_evidence_replay import (
    McpEvidenceMode,
    capture_mcp_evidence,
    inspect_mcp_evidence,
    read_mcp_evidence,
    replay_mcp_evidence,
)
from effectprobe._mcp_refund_comparison import (
    McpRefundCaseResult,
    McpRefundWorldTracker,
    build_mcp_refund_case,
)
from effectprobe._mcp_reports import (
    McpReport,
    build_mcp_report,
    render_json_report,
    render_junit_report,
    render_terminal_report,
)
from effectprobe._semantic_kernel import evaluate_case


@dataclass(frozen=True, slots=True)
class _Evaluation:
    result: McpRefundCaseResult
    tracker: McpRefundWorldTracker


@dataclass(frozen=True, slots=True)
class _Recorded:
    keyed: Path
    unsafe: Path
    keyed_evaluation: _Evaluation


@dataclass(frozen=True, slots=True)
class _Invocation:
    status: int
    stdout: str
    stderr: str


@pytest.fixture(scope="module")
def recorded(tmp_path_factory: pytest.TempPathFactory) -> _Recorded:
    directory = tmp_path_factory.mktemp("cli-evidence")
    artifacts: dict[McpEvidenceMode, Path] = {}
    keyed_evaluation: _Evaluation | None = None
    for mode in cast("tuple[McpEvidenceMode, ...]", ("keyed", "unsafe")):
        case, tracker = build_mcp_refund_case(keyed=mode == "keyed")
        evaluation = _Evaluation(evaluate_case(case), tracker)
        path = directory / f"{mode}.json"
        write_evidence_artifact(
            path,
            capture_mcp_evidence(mode=mode, result=evaluation.result, tracker=tracker),
        )
        artifacts[mode] = path
        if mode == "keyed":
            keyed_evaluation = evaluation
    assert keyed_evaluation is not None
    return _Recorded(artifacts["keyed"], artifacts["unsafe"], keyed_evaluation)


def _invoke(
    *args: str,
    services: _Services = _DEFAULT_SERVICES,
    stdout: StringIO | None = None,
    stderr: StringIO | None = None,
) -> _Invocation:
    output = StringIO() if stdout is None else stdout
    errors = StringIO() if stderr is None else stderr
    status = main(args, _services=services, stdout=output, stderr=errors)
    return _Invocation(status, output.getvalue(), errors.getvalue())


def _forbidden(*_args: object) -> Never:
    raise AssertionError("evaluative service was called")


def _object(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def test_help_version_usage_and_installed_entry_point() -> None:
    help_result = _invoke("--help")
    version_result = _invoke("--version")
    usage_result = _invoke("report")
    invalid_result = _invoke("report", "artifact.json", "--format", "other")

    assert help_result.status == 0
    assert "run" in help_result.stdout and "report" in help_result.stdout
    assert help_result.stderr == ""
    assert version_result == _Invocation(0, f"effectprobe {__version__}\n", "")
    assert usage_result.status == invalid_result.status == 2
    assert usage_result.stdout == invalid_result.stdout == ""
    assert "usage:" in usage_result.stderr
    assert "invalid choice" in invalid_result.stderr
    assert __all__ == ("__version__",)

    executable = shutil.which("effectprobe")
    assert executable is not None
    installed = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0
    assert installed.stdout == f"effectprobe {__version__}\n"
    assert installed.stderr == ""


@pytest.mark.parametrize(
    ("report_format", "renderer"),
    [
        ("terminal", render_terminal_report),
        ("json", render_json_report),
        ("junit", render_junit_report),
    ],
)
def test_report_is_data_only_and_uses_the_existing_renderers(
    recorded: _Recorded,
    report_format: str,
    renderer: Callable[[McpReport], str],
) -> None:
    services = replace(_DEFAULT_SERVICES, record=_forbidden, replay=_forbidden)
    result = _invoke("report", str(recorded.keyed), "--format", report_format, services=services)
    expected = renderer(build_mcp_report(recorded.keyed))

    assert result == _Invocation(0, expected, "")


@pytest.mark.parametrize(
    ("mode", "retry_status"),
    [("keyed", "PASS"), ("unsafe", "FAIL")],
)
def test_run_records_both_modes_without_collapsing_axes(
    tmp_path: Path, mode: str, retry_status: str
) -> None:
    artifact = tmp_path / f"{mode}.json"
    result = _invoke(
        "run",
        "controlled-mcp-refund",
        "--mode",
        mode,
        "--artifact",
        str(artifact),
        "--format",
        "json",
    )
    report = json.loads(result.stdout)

    assert result.status == 0
    assert result.stderr == ""
    assert report["axes"]["clean_validity"]["status"] == "PASS"
    assert report["axes"]["retry_safety"]["status"] == retry_status
    assert inspect_mcp_evidence(artifact).retry_safety == retry_status
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600


def test_report_file_is_exclusive_private_and_leaves_stdout_empty(
    tmp_path: Path, recorded: _Recorded
) -> None:
    output = tmp_path / "report.json"
    first = _invoke(
        "report",
        str(recorded.keyed),
        "--format",
        "json",
        "--output",
        str(output),
    )
    original = output.read_bytes()
    second = _invoke(
        "report",
        str(recorded.unsafe),
        "--format",
        "json",
        "--output",
        str(output),
    )

    assert first == _Invocation(0, "", "")
    assert json.loads(original)["axes"]["retry_safety"]["status"] == "PASS"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert second == _Invocation(1, "", "effectprobe: output failed\n")
    assert output.read_bytes() == original


def test_malformed_report_input_has_bounded_diagnostic(
    tmp_path: Path,
) -> None:
    secret = "SECRET-ABSOLUTE-PATH-/private/input.json"
    artifact = tmp_path / "malformed.json"
    artifact.write_text(f"not-json {secret}", encoding="utf-8")

    result = _invoke("report", str(artifact), "--format", "json")

    assert result == _Invocation(1, "", "effectprobe: report failed\n")
    assert secret not in result.stderr
    assert str(artifact) not in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("condition", ["existing", "collision", "dash", "missing-parent"])
def test_run_refuses_unsafe_artifact_destinations_before_evaluation(
    tmp_path: Path, condition: str
) -> None:
    calls = 0

    def record(_mode: McpEvidenceMode, _destination: Path) -> None:
        nonlocal calls
        calls += 1

    artifact = tmp_path / "artifact.json"
    output = Path("-")
    if condition == "existing":
        artifact.write_text("owned", encoding="utf-8")
    elif condition == "collision":
        output = artifact
    elif condition == "dash":
        artifact = Path("-")
    else:
        artifact = tmp_path / "absent" / "artifact.json"
    services = replace(_DEFAULT_SERVICES, record=record)

    result = _invoke(
        "run",
        "controlled-mcp-refund",
        "--mode",
        "keyed",
        "--artifact",
        str(artifact),
        "--output",
        str(output),
        services=services,
    )

    assert result == _Invocation(1, "", "effectprobe: output failed\n")
    assert calls == 0


def test_symlink_paths_and_replay_collisions_are_refused(
    tmp_path: Path, recorded: _Recorded
) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    symlinked_output = linked_parent / "report.txt"
    source_link = tmp_path / "source.json"
    source_link.symlink_to(recorded.keyed)

    linked_result = _invoke("report", str(recorded.keyed), "--output", str(symlinked_output))
    source_result = _invoke("report", str(source_link))
    replay_result = _invoke("replay", str(recorded.keyed), "--artifact", str(recorded.keyed))

    assert linked_result == _Invocation(1, "", "effectprobe: output failed\n")
    assert source_result == _Invocation(1, "", "effectprobe: report failed\n")
    assert replay_result == _Invocation(1, "", "effectprobe: output failed\n")
    assert not symlinked_output.exists()


def test_compatible_replay_writes_and_reports_a_fresh_child(
    tmp_path: Path, recorded: _Recorded
) -> None:
    child = tmp_path / "child.json"
    result = _invoke(
        "replay",
        str(recorded.keyed),
        "--artifact",
        str(child),
        "--format",
        "json",
    )
    report = json.loads(result.stdout)
    inspection = inspect_mcp_evidence(child)

    assert result.status == 0
    assert result.stderr == ""
    assert report["source"]["lineage"] == "verified_replay"
    assert report["source"]["reproduction_matched"] is True
    assert inspection.reproduction_matched is True
    assert inspection.source_sha256 == read_mcp_evidence(recorded.keyed).sha256


def test_replay_drift_refuses_before_evaluation_and_creates_no_child(
    tmp_path: Path, recorded: _Recorded
) -> None:
    payload = artifact_payload(read_mcp_evidence(recorded.keyed).artifact)
    compatibility = _object(payload["compatibility"])
    runtime = _object(compatibility["runtime"])
    runtime["python_version"] = "0.0-drift"
    drifted = tmp_path / "drifted.json"
    write_evidence_artifact(drifted, evidence_artifact_from_payload(payload))
    child = tmp_path / "child.json"
    evaluations = 0

    def fail_if_evaluated(
        _mode: McpEvidenceMode,
    ) -> tuple[McpRefundCaseResult, McpRefundWorldTracker]:
        nonlocal evaluations
        evaluations += 1
        raise AssertionError("replay evaluated after compatibility drift")

    def replay(source: Path, destination: Path) -> object:
        return replay_mcp_evidence(source, destination, run=fail_if_evaluated)

    result = _invoke(
        "replay",
        str(drifted),
        "--artifact",
        str(child),
        services=replace(_DEFAULT_SERVICES, replay=replay),
    )

    assert result == _Invocation(1, "", "effectprobe: replay failed\n")
    assert evaluations == 0
    assert not child.exists()


def test_reproduction_mismatch_remains_axis_independent_command_success(
    tmp_path: Path, recorded: _Recorded
) -> None:
    child = tmp_path / "mismatch.json"

    def replay_with_mismatch(source: Path, destination: Path) -> object:
        replay = replay_mcp_evidence(
            source,
            destination,
            run=lambda _mode: (
                recorded.keyed_evaluation.result,
                recorded.keyed_evaluation.tracker,
            ),
        )
        payload = artifact_payload(read_mcp_evidence(destination).artifact)
        _object(payload["reproduction"])["matched"] = False
        replacement = destination.with_suffix(".replacement")
        write_evidence_artifact(replacement, evidence_artifact_from_payload(payload))
        destination.unlink()
        replacement.rename(destination)
        return replay

    result = _invoke(
        "replay",
        str(recorded.keyed),
        "--artifact",
        str(child),
        "--format",
        "json",
        services=replace(_DEFAULT_SERVICES, replay=replay_with_mismatch),
    )
    report = json.loads(result.stdout)

    assert result.status == 0
    assert result.stderr == ""
    assert report["source"]["reproduction_matched"] is False
    assert report["axes"]["clean_validity"]["status"] == "PASS"
    assert report["axes"]["retry_safety"]["status"] == "PASS"


def test_report_failure_after_recording_preserves_the_artifact(
    tmp_path: Path, recorded: _Recorded
) -> None:
    artifact = tmp_path / "recorded-before-report-failure.json"

    def record(_mode: McpEvidenceMode, destination: Path) -> object:
        return write_evidence_artifact(destination, read_mcp_evidence(recorded.keyed).artifact)

    def fail_render(_report: object) -> Never:
        raise RuntimeError("SECRET-RENDERER-DETAIL")

    services = replace(_DEFAULT_SERVICES, record=record, render_terminal=fail_render)
    result = _invoke(
        "run",
        "controlled-mcp-refund",
        "--mode",
        "keyed",
        "--artifact",
        str(artifact),
        services=services,
    )

    assert result == _Invocation(1, "", "effectprobe: report failed\n")
    assert inspect_mcp_evidence(artifact).retry_safety == "PASS"
    assert "SECRET" not in result.stderr


def test_failed_report_publication_cleans_temporary_file(
    tmp_path: Path, recorded: _Recorded, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.txt"

    def fail_link(*_args: object, **_kwargs: object) -> Never:
        raise OSError("SECRET-LINK-FAILURE")

    monkeypatch.setattr(os, "link", fail_link)
    result = _invoke("report", str(recorded.keyed), "--output", str(output))

    assert result == _Invocation(1, "", "effectprobe: output failed\n")
    assert not output.exists()
    assert tuple(tmp_path.iterdir()) == ()


def test_interrupt_and_unexpected_internal_failure_have_bounded_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact.json"

    def interrupt(_mode: McpEvidenceMode, _destination: Path) -> Never:
        raise KeyboardInterrupt

    interrupted = _invoke(
        "run",
        "controlled-mcp-refund",
        "--mode",
        "keyed",
        "--artifact",
        str(artifact),
        services=replace(_DEFAULT_SERVICES, record=interrupt),
    )

    def fail_execute(*_args: object) -> Never:
        raise RuntimeError("SECRET-INTERNAL-DETAIL")

    monkeypatch.setattr("effectprobe._cli._execute", fail_execute)
    internal = _invoke("report", "absent.json")

    assert interrupted == _Invocation(130, "", "effectprobe: interrupted\n")
    assert internal == _Invocation(1, "", "effectprobe: internal failure\n")
