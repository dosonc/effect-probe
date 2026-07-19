"""Installed facade for the registered controlled MCP refund case."""

import argparse
import os
import stat
import sys
import tempfile
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr, redirect_stdout, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TextIO, cast

from effectprobe import __version__
from effectprobe._mcp_evidence_replay import (
    McpEvidenceMode,
    record_mcp_evidence,
    replay_mcp_evidence,
)
from effectprobe._mcp_reports import (
    McpReport,
    build_mcp_report,
    render_json_report,
    render_junit_report,
    render_terminal_report,
)

type _ReportFormat = Literal["terminal", "json", "junit"]
type _FailureCategory = Literal["run", "report", "replay", "output"]


class _CliFailure(RuntimeError):
    def __init__(self, category: _FailureCategory) -> None:
        self.category = category
        super().__init__(category)


@dataclass(frozen=True, slots=True)
class _Services:
    record: Callable[[McpEvidenceMode, Path], object]
    replay: Callable[[Path, Path], object]
    build_report: Callable[[Path], McpReport]
    render_terminal: Callable[[McpReport], str]
    render_json: Callable[[McpReport], str]
    render_junit: Callable[[McpReport], str]


_DEFAULT_SERVICES = _Services(
    record=record_mcp_evidence,
    replay=replay_mcp_evidence,
    build_report=build_mcp_report,
    render_terminal=render_terminal_report,
    render_json=render_json_report,
    render_junit=render_junit_report,
)


@dataclass(frozen=True, slots=True)
class _RunCommand:
    mode: McpEvidenceMode
    artifact: Path
    report_format: _ReportFormat
    output: Path


@dataclass(frozen=True, slots=True)
class _ReportCommand:
    artifact: Path
    report_format: _ReportFormat
    output: Path


@dataclass(frozen=True, slots=True)
class _ReplayCommand:
    source: Path
    artifact: Path
    report_format: _ReportFormat
    output: Path


type _Command = _RunCommand | _ReportCommand | _ReplayCommand


def _add_report_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=("terminal", "json", "junit"),
        default="terminal",
        dest="report_format",
        help="report projection to emit (default: terminal)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("-"),
        metavar="PATH|-",
        help="exclusive report destination, or - for stdout (default: -)",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="effectprobe",
        description="Run and report the registered controlled MCP refund case.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run one registered controlled case")
    cases = run.add_subparsers(dest="case", required=True)
    refund = cases.add_parser(
        "controlled-mcp-refund",
        help="run the harness-controlled SQLite MCP refund fixture",
    )
    refund.add_argument("--mode", choices=("unsafe", "keyed"), required=True)
    refund.add_argument("--artifact", type=Path, required=True, metavar="PATH")
    _add_report_arguments(refund)

    report = commands.add_parser("report", help="render one eligible artifact without execution")
    report.add_argument("artifact", type=Path, metavar="ARTIFACT")
    _add_report_arguments(report)

    replay = commands.add_parser("replay", help="strictly replay one compatible artifact")
    replay.add_argument("source", type=Path, metavar="ARTIFACT")
    replay.add_argument("--artifact", type=Path, required=True, metavar="PATH")
    _add_report_arguments(replay)
    return parser


def _parse(argv: Sequence[str] | None) -> _Command:
    values = _parser().parse_args(argv)
    command = cast("str", values.command)
    report_format = cast("_ReportFormat", values.report_format)
    output = cast("Path", values.output)
    if command == "run":
        return _RunCommand(
            mode=cast("McpEvidenceMode", values.mode),
            artifact=cast("Path", values.artifact),
            report_format=report_format,
            output=output,
        )
    if command == "report":
        return _ReportCommand(
            artifact=cast("Path", values.artifact),
            report_format=report_format,
            output=output,
        )
    return _ReplayCommand(
        source=cast("Path", values.source),
        artifact=cast("Path", values.artifact),
        report_format=report_format,
        output=output,
    )


def _path_key(path: Path) -> str:
    return os.path.abspath(os.fspath(path))


def _require_artifact_path(path: Path) -> None:
    if os.fspath(path) == "-":
        raise _CliFailure("output")


def _preflight_destination(path: Path) -> None:
    _require_artifact_path(path)
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise _CliFailure("output") from error
    else:
        raise _CliFailure("output")

    try:
        parent = path.parent.lstat()
    except OSError as error:
        raise _CliFailure("output") from error
    if not stat.S_ISDIR(parent.st_mode):
        raise _CliFailure("output")


def _require_distinct(*paths: Path) -> None:
    keys = tuple(_path_key(path) for path in paths)
    if len(set(keys)) != len(keys):
        raise _CliFailure("output")


def _preflight_report_output(output: Path, *other_paths: Path) -> None:
    if os.fspath(output) == "-":
        return
    _require_distinct(output, *other_paths)
    _preflight_destination(output)


def _write_report(path: Path, rendered: str) -> None:
    data = rendered.encode("utf-8")
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(data):
                offset += os.write(descriptor, data[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary_path, path, follow_symlinks=False)
        except FileExistsError as error:
            raise _CliFailure("output") from error
        except OSError as error:
            raise _CliFailure("output") from error
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except _CliFailure:
        raise
    except OSError as error:
        raise _CliFailure("output") from error
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)


def _render(value: McpReport, report_format: _ReportFormat, services: _Services) -> str:
    try:
        if report_format == "terminal":
            return services.render_terminal(value)
        if report_format == "json":
            return services.render_json(value)
        return services.render_junit(value)
    except Exception as error:
        raise _CliFailure("report") from error


def _emit(rendered: str, output: Path, stdout: TextIO) -> None:
    try:
        if os.fspath(output) == "-":
            stdout.write(rendered)
            stdout.flush()
        else:
            _write_report(output, rendered)
    except _CliFailure:
        raise
    except (OSError, UnicodeError) as error:
        raise _CliFailure("output") from error


def _build_report(path: Path, services: _Services) -> McpReport:
    try:
        return services.build_report(path)
    except Exception as error:
        raise _CliFailure("report") from error


def _execute(command: _Command, services: _Services, stdout: TextIO) -> None:
    if isinstance(command, _RunCommand):
        _preflight_destination(command.artifact)
        _preflight_report_output(command.output, command.artifact)
        try:
            services.record(command.mode, command.artifact)
        except Exception as error:
            raise _CliFailure("run") from error
        report = _build_report(command.artifact, services)
        rendered = _render(report, command.report_format, services)
        _emit(rendered, command.output, stdout)
        return

    if isinstance(command, _ReportCommand):
        _require_artifact_path(command.artifact)
        _preflight_report_output(command.output, command.artifact)
        report = _build_report(command.artifact, services)
        rendered = _render(report, command.report_format, services)
        _emit(rendered, command.output, stdout)
        return

    _require_artifact_path(command.source)
    _require_distinct(command.source, command.artifact)
    _preflight_destination(command.artifact)
    _preflight_report_output(command.output, command.source, command.artifact)
    try:
        services.replay(command.source, command.artifact)
    except Exception as error:
        raise _CliFailure("replay") from error
    report = _build_report(command.artifact, services)
    rendered = _render(report, command.report_format, services)
    _emit(rendered, command.output, stdout)


def _diagnostic(stderr: TextIO, message: str) -> None:
    with suppress(OSError, UnicodeError):
        stderr.write(f"effectprobe: {message}\n")
        stderr.flush()


def main(
    argv: Sequence[str] | None = None,
    *,
    _services: _Services = _DEFAULT_SERVICES,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the installed CLI and return command status, not an evaluative verdict."""

    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr
    try:
        with redirect_stdout(output_stream), redirect_stderr(error_stream):
            try:
                command = _parse(argv)
            except SystemExit as error:
                return cast("int", error.code)
        _execute(command, _services, output_stream)
    except KeyboardInterrupt:
        _diagnostic(error_stream, "interrupted")
        return 130
    except _CliFailure as error:
        _diagnostic(error_stream, f"{error.category} failed")
        return 1
    except Exception:
        _diagnostic(error_stream, "internal failure")
        return 1
    return 0
