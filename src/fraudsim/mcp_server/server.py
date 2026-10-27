"""
An MCP server over the running fraudsim API.

It turns the live detection system into tools an assistant can call, so an
investigation can be carried out by asking questions instead of clicking
through pre-built panels. The dashboard answers the questions it was designed
for; this answers the ones nobody anticipated.

Every tool is a thin call to the API the dashboard already uses, so the
assistant and the dashboard drive the *same* live run: inject a ring from a
conversation and it appears on screen immediately.

Start the stack first (./start.sh), then point an MCP client at:

    python -m fraudsim.mcp_server
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

API = os.environ.get("FRAUDSIM_API", "http://localhost:8000")
TIMEOUT = 20.0

INSTRUCTIONS = """Tools for investigating a live fraud-ring detection system.

Typical flow: start_run -> inject_ring -> list_rings -> explain_ring -> get_graph.

When you fetch a graph or an explanation, draw the result for the user rather
than printing raw JSON - these are small graphs meant to be looked at.

get_oracle returns the simulator's answer key. It is ground truth for checking
whether the detector was right; never use it to decide what to flag, or the
evaluation is meaningless."""

server = MCPServer(name="fraudsim", instructions=INSTRUCTIONS)


class ApiDown(RuntimeError):
    """Raised with an actionable message when the stack is not running."""


def _call(method: str, path: str, **kw: Any) -> Any:
    url = f"{API}{path}"
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.request(method, url, **kw)
    except httpx.ConnectError as exc:
        raise ApiDown(
            f"The fraudsim API is not reachable at {API}. Start it with ./start.sh "
            f"and try again."
        ) from exc
    if response.status_code == 409 or (
        response.status_code == 400 and "no live run" in response.text.lower()
    ):
        raise RuntimeError("No run is active. Call start_run first.")
    response.raise_for_status()
    return response.json()


@server.tool()
def list_scenarios() -> list[dict[str, Any]]:
    """The simulated worlds available to run, with a description and size for each."""
    return _call("GET", "/scenarios")


@server.tool()
def start_run(
    scenario: str = "adversarial",
    seed: int = 42,
    speed_multiplier: float = 1.0,
    hub_weight_enabled: bool = True,
    hub_degree_cap: float | None = None,
) -> dict[str, Any]:
    """
    Start a live simulation and begin detecting on it.

    hub_weight_enabled=False with a hub_degree_cap reproduces the original
    degree-capped behaviour, which is useful for showing the contrast.
    """
    body = {
        "scenario": scenario,
        "seed": seed,
        "speed_multiplier": speed_multiplier,
        "hub_weight_enabled": hub_weight_enabled,
    }
    if hub_degree_cap is not None:
        body["hub_degree_cap"] = hub_degree_cap
    return _call("POST", "/runs/start", json=body)


@server.tool()
def stop_run() -> dict[str, Any]:
    """Stop the live run."""
    return _call("POST", "/runs/stop")


@server.tool()
def get_status() -> dict[str, Any]:
    """Whether a run is active, which scenario and seed, and how far it has got."""
    return _call("GET", "/status")


@server.tool()
def inject_ring(ring_type: str = "shared_device", evasion: str = "none", size: int = 4) -> dict[str, Any]:
    """
    Plant a fraud ring into the running world right now.

    ring_type: shared_device, shared_card, shared_ip, money_cycle,
               smurfing_fan_in, smurfing_fan_out, layered_chain, mixed_attribute
    evasion:   none, hub_hiding, low_and_slow, identity_rotation, decoy_edges, adaptive

    Injecting the same ring with evasion 'none' and then 'low_and_slow' is the
    clearest way to show what evasion does to detection.
    """
    return _call(
        "POST", "/runs/inject",
        json={"ring_type": ring_type, "evasion": evasion, "size": size},
    )


@server.tool()
def list_rings() -> list[dict[str, Any]]:
    """Every ring the detector currently holds, with its risk score and status."""
    return _call("GET", "/rings")


@server.tool()
def explain_ring(ring_id: str) -> dict[str, Any]:
    """
    Why one ring was flagged: its members, features, plain-language reason,
    feature contributions, counterfactual and evidence subgraph.
    """
    return _call("GET", f"/rings/{ring_id}")


@server.tool()
def get_graph(max_users: int = 120, window_s: float = 60.0, oracle: bool = False) -> dict[str, Any]:
    """
    A snapshot of the transaction graph as nodes and edges, for drawing.

    Keep max_users small enough to stay readable. oracle=True tags each node
    with whether it is genuinely fraudulent.
    """
    return _call(
        "GET", "/graph",
        params={"max_users": max_users, "window_s": window_s, "oracle": str(oracle).lower()},
    )


@server.tool()
def get_metrics() -> dict[str, Any]:
    """Live precision, recall and F1 of the detector against the simulator's ground truth."""
    return _call("GET", "/metrics/summary")


@server.tool()
def get_oracle() -> dict[str, Any]:
    """
    The simulator's answer key: which rings are real, which flags were correct,
    which were false alarms and which real rings were missed.
    """
    return _call("GET", "/oracle")


@server.tool()
def recent_events(limit: int = 30) -> list[dict[str, Any]]:
    """The tail of the observable event stream - what the detector is seeing."""
    return _call("GET", "/events/recent", params={"limit": limit})


@server.tool()
def send_feedback(ring_id: str, action: str) -> dict[str, Any]:
    """
    Act as the analyst on an alert. action is 'confirm' or 'dismiss'.

    This adapts the alert threshold, exactly as the dashboard buttons do, and is
    recorded in the audit trail.
    """
    if action not in ("confirm", "dismiss"):
        raise ToolError("action must be 'confirm' or 'dismiss'")
    return _call("POST", f"/rings/{ring_id}/feedback", json={"action": action})


def main() -> None:
    server.run(transport="stdio")
