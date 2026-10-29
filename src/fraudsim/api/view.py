"""
Focus the dashboard's graph on part of itself.

The graph shows everything active, which is the right default and the wrong
thing to look at when investigating one ring. This lets the view be narrowed to
a set of accounts and their immediate surroundings, so the question "how did
this group get connected, and why did it alert?" has a picture to go with it.

The focus is server state and is broadcast over the existing WebSocket, so it
does not matter where the request came from: narrowing the view from a
conversation changes what everyone watching the dashboard sees.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from fraudsim.api.state import state

router = APIRouter(prefix="/view", tags=["view"])

MAX_HOPS = 3

_focus: dict[str, Any] = {"active": False, "user_ids": [], "ring_id": None, "note": "", "hops": 1}


class FocusRequest(BaseModel):
    ring_id: str | None = None
    user_ids: list[str] = Field(default_factory=list)
    note: str = ""
    hops: int = Field(default=1, ge=0, le=MAX_HOPS)


@router.get("/focus")
async def get_focus() -> dict[str, Any]:
    """What the graph is currently narrowed to, if anything."""
    return dict(_focus)


@router.post("/focus")
async def set_focus(req: FocusRequest) -> dict[str, Any]:
    """
    Narrow the graph to these accounts, or to the members of a ring.

    `hops` additionally keeps whatever they are directly connected to, so the
    shared device or IP that links them stays on screen - without it the
    picture would show the accounts but not the thing joining them.
    """
    ids = list(req.user_ids)
    if req.ring_id:
        ring = state.active_rings.get(req.ring_id)
        if ring is None:
            raise HTTPException(status_code=404, detail=f"Ring '{req.ring_id}' not found")
        ids += [m for m in (ring.get("members") or []) if m not in ids]
    if not ids:
        raise HTTPException(status_code=422, detail="Give a ring_id or at least one user_id.")

    _focus.update(
        active=True,
        user_ids=ids,
        ring_id=req.ring_id,
        note=req.note.strip(),
        hops=req.hops,
    )
    await state.broadcast_ws({"type": "VIEW_FOCUS", **_focus})
    return dict(_focus)


@router.post("/focus/clear")
async def clear_focus() -> dict[str, Any]:
    """Show the whole graph again."""
    _focus.update(active=False, user_ids=[], ring_id=None, note="", hops=1)
    await state.broadcast_ws({"type": "VIEW_FOCUS", **_focus})
    return dict(_focus)
