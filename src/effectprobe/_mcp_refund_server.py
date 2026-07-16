"""Private trusted MCP stdio refund fixture used by the transport slice."""

import argparse
import os
from pathlib import Path
from typing import Literal, TypedDict, cast

import anyio
from mcp import types
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.lowlevel import Server
from mcp.server.session import ServerSession
from mcp.server.stdio import stdio_server

from effectprobe._mcp_refund_store import commit_refund
from effectprobe._refund_comparison import RefundReceipt

type Mode = Literal["unsafe", "keyed"]
type Variant = Literal[
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


class RefundReceiptPayload(TypedDict):
    refund_id: str
    payment_id: str
    amount_minor_units: int


class WrongRefundReceiptPayload(TypedDict):
    refund_id: str
    payment_id: str
    amount_minor_units: str


def _build_server(  # pragma: no cover - exercised in managed MCP subprocesses
    *, database: Path, mode: Mode, variant: Variant
) -> FastMCP:
    server = FastMCP("EffectProbe private refund fixture")
    if variant == "no_tools":
        return server

    if variant == "wrong_schema":

        @server.tool(name="refund")
        def wrong_refund(payment_id: str) -> str:  # pyright: ignore[reportUnusedFunction]
            return payment_id

        return server

    if variant == "wrong_input_type":

        @server.tool(name="refund", structured_output=True)
        def wrong_input_refund(  # pyright: ignore[reportUnusedFunction]
            payment_id: str,
            amount_minor_units: str,
            operation_key: str,
        ) -> RefundReceiptPayload:
            del amount_minor_units, operation_key
            return RefundReceiptPayload(
                refund_id="refund/not-called",
                payment_id=payment_id,
                amount_minor_units=0,
            )

        return server

    if variant == "wrong_output_type":

        @server.tool(name="refund", structured_output=True)
        def wrong_output_refund(  # pyright: ignore[reportUnusedFunction]
            payment_id: str,
            amount_minor_units: int,
            operation_key: str,
        ) -> WrongRefundReceiptPayload:
            del operation_key
            return WrongRefundReceiptPayload(
                refund_id="refund/not-called",
                payment_id=payment_id,
                amount_minor_units=str(amount_minor_units),
            )

        return server

    if variant == "unstructured":

        @server.tool(name="refund", structured_output=False)
        def unstructured_refund(  # pyright: ignore[reportUnusedFunction]
            payment_id: str,
            amount_minor_units: int,
            operation_key: str,
        ) -> str:
            del payment_id, amount_minor_units, operation_key
            return "unstructured"

        return server

    @server.tool(name="refund", structured_output=True)
    async def refund(  # pyright: ignore[reportUnusedFunction]
        payment_id: str,
        amount_minor_units: int,
        operation_key: str,
        context: Context[ServerSession, None, types.CallToolRequest],
    ) -> RefundReceiptPayload:
        if variant == "tool_error":
            raise RuntimeError("configured MCP fixture tool error")
        if variant == "tool_exit":
            os._exit(4)
        if variant == "hang":
            await anyio.sleep_forever()
            raise AssertionError("unreachable")
        receipt: RefundReceipt = commit_refund(
            database,
            mode=mode,
            payment_id=payment_id,
            amount_minor_units=amount_minor_units,
            operation_key=operation_key,
            mcp_request_id=context.request_id,
        )
        return RefundReceiptPayload(
            refund_id=receipt.refund_id,
            payment_id=receipt.payment_id,
            amount_minor_units=receipt.amount_minor_units,
        )

    return server


def main() -> None:  # pragma: no cover - exercised in managed MCP subprocesses
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--mode", choices=("unsafe", "keyed"), required=True)
    parser.add_argument(
        "--variant",
        choices=(
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
        ),
        default="normal",
    )
    arguments = parser.parse_args()
    if arguments.variant == "startup_exit":
        raise SystemExit(3)
    if arguments.variant == "no_tools":
        server = Server("EffectProbe private server without tool capability")

        async def run_without_tools() -> None:
            async with stdio_server() as streams:
                await server.run(*streams, server.create_initialization_options())

        anyio.run(run_without_tools)
        return
    if arguments.variant in ("discovery_hang", "discovery_exit"):
        server = Server("EffectProbe private discovery failure server")

        @server.list_tools()
        async def fail_discovery() -> list[types.Tool]:  # pyright: ignore[reportUnusedFunction]
            if arguments.variant == "discovery_hang":
                await anyio.sleep_forever()
            os._exit(3)

        async def run_discovery_failure() -> None:
            async with stdio_server() as streams:
                await server.run(*streams, server.create_initialization_options())

        anyio.run(run_discovery_failure)
        return
    server = _build_server(
        database=arguments.database,
        mode=cast("Mode", arguments.mode),
        variant=cast("Variant", arguments.variant),
    )
    server.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover - exercised in managed MCP subprocesses
    main()
