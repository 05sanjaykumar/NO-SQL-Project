"""Tests for the MCP server.

These run offline: they check the tool surface and that a stopped stack
produces an actionable message rather than a connection traceback.
"""

from __future__ import annotations

import asyncio

import pytest

from mcp.server.mcpserver.exceptions import ToolError

from fraudsim.mcp_server.server import ApiDown, _call, server

EXPECTED = {
    "list_scenarios",
    "start_run",
    "stop_run",
    "get_status",
    "inject_ring",
    "list_rings",
    "explain_ring",
    "get_graph",
    "get_metrics",
    "get_oracle",
    "recent_events",
    "send_feedback",
}


def _tools() -> list:
    return asyncio.run(server.list_tools())


def test_every_tool_is_registered() -> None:
    assert {t.name for t in _tools()} == EXPECTED


def test_every_tool_is_described() -> None:
    # The description is what an assistant reads to decide when to call a tool,
    # so an undescribed tool is an unusable one.
    for tool in _tools():
        assert tool.description, f"{tool.name} has no description"


def test_a_stopped_stack_gives_an_actionable_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRAUDSIM_API", "http://127.0.0.1:9")  # nothing listens here
    import fraudsim.mcp_server.server as mod

    monkeypatch.setattr(mod, "API", "http://127.0.0.1:9")
    with pytest.raises(ApiDown) as excinfo:
        _call("GET", "/status")
    assert "./start.sh" in str(excinfo.value)


def test_feedback_rejects_an_unknown_action() -> None:
    # call_tool raises; the protocol layer turns this into an error result.
    with pytest.raises(ToolError, match="confirm"):
        asyncio.run(server.call_tool("send_feedback", {"ring_id": "R1", "action": "maybe"}))
