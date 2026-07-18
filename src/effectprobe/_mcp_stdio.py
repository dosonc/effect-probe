"""Private synchronous bridge to the stable MCP Python SDK stdio client."""

from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from pathlib import Path
from tempfile import TemporaryFile
from typing import Any, TextIO, cast

from anyio.from_thread import BlockingPortal, start_blocking_portal
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool


class McpStdioError(RuntimeError):
    """Base error for private MCP stdio infrastructure."""


class McpLifecycleError(McpStdioError):
    """The MCP session or managed subprocess lifecycle malfunctioned."""

    def __init__(self, stage: str, cause: BaseException) -> None:
        self.stage = stage
        self.cause = cause
        super().__init__(f"MCP stdio {stage} failed: {type(cause).__name__}: {cause}")


class McpCapabilityError(McpStdioError):
    """The server cannot satisfy the configured private tool contract."""


class McpToolCallError(McpStdioError):
    """The MCP tool returned an explicit error response."""


class McpResultError(McpStdioError):
    """The MCP tool result lacks the required structured evidence."""


class McpPreflightError(McpStdioError):
    """A disposable MCP capability preflight failed before world provisioning."""

    def __init__(self, cause: McpStdioError) -> None:
        self.cause = cause
        super().__init__(f"MCP preflight failed: {cause}")


@dataclass(frozen=True, slots=True)
class McpPreflightEvidence:
    """Allowlisted protocol evidence from one disposable initialized session."""

    protocol_version: str


@dataclass(frozen=True, slots=True)
class McpStdioToolConfig:
    """Private configuration for one trusted local MCP stdio tool."""

    command: str
    args: tuple[str, ...]
    tool_name: str
    required_input_schema: tuple[tuple[str, str], ...]
    required_output_schema: tuple[tuple[str, str], ...]
    cwd: Path | None = None
    env: tuple[tuple[str, str], ...] = ()
    timeout_seconds: float = 10.0


def _schema_fields_and_types(
    schema: Mapping[str, Any] | None,
) -> tuple[frozenset[str], dict[str, str]]:
    if schema is None:
        return frozenset(), {}
    required_value: object = schema.get("required")
    properties_value: object = schema.get("properties")
    if not isinstance(required_value, list) or not isinstance(properties_value, dict):
        return frozenset(), {}
    required = cast("list[object]", required_value)
    properties = cast("dict[object, object]", properties_value)
    fields = frozenset(
        field for field in required if isinstance(field, str) and field in properties
    )
    field_types: dict[str, str] = {}
    for field in fields:
        definition = properties[field]
        if isinstance(definition, dict):
            typed_definition = cast("dict[object, object]", definition)
            type_value = typed_definition.get("type")
            if isinstance(type_value, str):
                field_types[field] = type_value
    return fields, field_types


def _validate_schema(
    *,
    schema: Mapping[str, Any] | None,
    expected: tuple[tuple[str, str], ...],
    label: str,
) -> None:
    required_fields, field_types = _schema_fields_and_types(schema)
    expected_types = dict(expected)
    missing = expected_types.keys() - required_fields
    if missing:
        fields = ", ".join(sorted(missing))
        raise McpCapabilityError(f"tool schema lacks required {label} fields: {fields}")
    incompatible = tuple(
        field
        for field, expected_type in expected_types.items()
        if field_types.get(field) != expected_type
    )
    if incompatible:
        fields = ", ".join(sorted(incompatible))
        raise McpCapabilityError(f"tool schema has incompatible {label} types: {fields}")


def _validate_tool_contract(tool: Tool, config: McpStdioToolConfig) -> None:
    try:
        _validate_schema(
            schema=tool.inputSchema,
            expected=config.required_input_schema,
            label="input",
        )
        _validate_schema(
            schema=tool.outputSchema,
            expected=config.required_output_schema,
            label="output",
        )
    except McpCapabilityError as error:
        raise McpCapabilityError(f"tool {config.tool_name!r} is incompatible: {error}") from error


def parse_structured_tool_result(
    result: CallToolResult,
    *,
    required_fields: frozenset[str],
) -> dict[str, Any]:
    """Validate a completed MCP tool result before the fault boundary."""

    if result.isError:
        raise McpToolCallError("MCP tool returned an error result")
    structured = result.structuredContent
    if not isinstance(structured, dict):
        raise McpResultError("MCP tool result has no structured content")
    missing = required_fields - structured.keys()
    if missing:
        fields = ", ".join(sorted(missing))
        raise McpResultError(f"MCP tool result lacks required fields: {fields}")
    return structured


class McpStdioToolClient:
    """Own one initialized MCP stdio session from synchronous kernel code."""

    def __init__(self, config: McpStdioToolConfig) -> None:
        self._config = config
        self._stack: ExitStack | None = None
        self._portal: BlockingPortal | None = None
        self._session: ClientSession | None = None
        self._stderr: TextIO | None = None
        self._protocol_version: str | None = None

    def __enter__(self) -> "McpStdioToolClient":
        stack = ExitStack()
        self._stack = stack
        try:
            stderr = stack.enter_context(TemporaryFile(mode="w+", encoding="utf-8"))
            portal = stack.enter_context(start_blocking_portal(backend="asyncio"))
            parameters = StdioServerParameters(
                command=self._config.command,
                args=list(self._config.args),
                env=dict(self._config.env) or None,
                cwd=self._config.cwd,
            )
            streams = stack.enter_context(
                portal.wrap_async_context_manager(stdio_client(parameters, errlog=stderr))
            )
            session = stack.enter_context(
                portal.wrap_async_context_manager(
                    ClientSession(
                        *streams,
                        read_timeout_seconds=timedelta(seconds=self._config.timeout_seconds),
                    )
                )
            )
            initialization = portal.call(session.initialize)
        except Exception as error:
            try:
                stack.close()
            except Exception as cleanup_error:
                raise McpLifecycleError("startup_rollback", cleanup_error) from error
            raise McpLifecycleError("startup", error) from error
        self._stderr = stderr
        self._portal = portal
        self._session = session
        self._protocol_version = str(initialization.protocolVersion)
        return self

    @property
    def protocol_version(self) -> str:
        """Return the revision negotiated by the active initialized session."""

        if self._protocol_version is None:
            raise McpLifecycleError("use", RuntimeError("MCP stdio client is not active"))
        return self._protocol_version

    def validate_capabilities(self) -> None:
        """Require the configured tool contract from the initialized server."""

        portal, session = self._active()
        capabilities = session.get_server_capabilities()
        if capabilities is None or capabilities.tools is None:
            raise McpCapabilityError("server did not declare the tools capability")
        try:
            response = portal.call(session.list_tools)
        except Exception as error:
            raise McpLifecycleError("discovery", error) from error
        tool = next((item for item in response.tools if item.name == self._config.tool_name), None)
        if tool is None:
            raise McpCapabilityError(f"server did not list tool {self._config.tool_name!r}")
        _validate_tool_contract(tool, self._config)

    def call_tool(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Call and validate one configured tool result."""

        portal, session = self._active()
        try:
            result = portal.call(
                partial(session.call_tool, self._config.tool_name, arguments=dict(arguments))
            )
        except Exception as error:
            raise McpLifecycleError("tool_call", error) from error
        return parse_structured_tool_result(
            result,
            required_fields=frozenset(
                field for field, _type in self._config.required_output_schema
            ),
        )

    def _active(self) -> tuple[BlockingPortal, ClientSession]:
        if self._portal is None or self._session is None:
            raise McpLifecycleError("use", RuntimeError("MCP stdio client is not active"))
        return self._portal, self._session

    def __exit__(self, *_exc_info: object) -> None:
        stack = self._stack
        self._stack = None
        self._portal = None
        self._session = None
        self._stderr = None
        self._protocol_version = None
        if stack is None:
            return
        try:
            stack.close()
        except Exception as error:
            raise McpLifecycleError("cleanup", error) from error


def preflight_mcp_tool(config: McpStdioToolConfig) -> McpPreflightEvidence:
    """Probe MCP capabilities without creating an evaluative effect world."""

    try:
        with McpStdioToolClient(config) as client:
            client.validate_capabilities()
            return McpPreflightEvidence(protocol_version=client.protocol_version)
    except McpStdioError as error:
        raise McpPreflightError(error) from error
