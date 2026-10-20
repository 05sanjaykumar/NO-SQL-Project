"""
In-memory application state and simulation manager for the API.
"""

from __future__ import annotations

from typing import Any


class AppState:
    """Manages active run status, connected WebSocket clients, and recent detection state."""

    def __init__(self) -> None:
        self.run_id: str = "run_init"
        self.status: str = "idle"  # idle, running, paused, stopped
        self.scenario: str = "baseline"
        self.seed: int = 42
        self.speed_multiplier: float = 1.0
        self.events_emitted: int = 0
        self.rings_detected: int = 0
        self.active_rings: dict[str, dict[str, Any]] = {}
        self.ground_truth_rings: dict[str, dict[str, Any]] = {}
        self.recent_events: list[dict[str, Any]] = []
        self.ws_clients: set[Any] = set()
        self.neo4j_client: Any = None
        self.live: Any = None  # fraudsim.api.live.LiveRun while a run exists

    async def broadcast_ws(self, message: dict[str, Any]) -> None:
        """Broadcast message to all connected WebSocket clients."""
        if not self.ws_clients:
            return
        dead = set()
        for ws in self.ws_clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        self.ws_clients.difference_update(dead)


state = AppState()
