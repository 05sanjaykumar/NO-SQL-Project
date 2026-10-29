"""
Ask the live system questions in plain English.

The dashboard answers the questions it was built for. This answers the rest, by
handing the question to the Claude Code CLI already installed on the machine,
with the fraudsim MCP server attached so it can query the running detector and
report what it actually found.

No API key: the local CLI uses the operator's own Claude session. It is not
free - each question costs roughly 10-30 cents against that account - so the
endpoint answers one question at a time and refuses to queue them up.

The CLI runs locked down:

* ``--strict-mcp-config``  only the fraudsim server loads, not whatever MCP
  servers the operator happens to have configured;
* ``--allowed-tools``      an explicit list of fraudsim tools;
* ``--disallowed-tools``   the built-in file, shell and web tools are blocked.

That matters because this turns a web request into a local process. The API is
meant to be reachable from localhost only; do not expose it to a network.

Requires the API to run on the host, since the CLI lives there. Inside the
container ``claude`` is absent and the endpoint reports itself unavailable
instead of failing per request.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/assistant", tags=["assistant"])

TIMEOUT_S = 180.0
MAX_QUESTION = 1000

# Only these, and only these, are offered to the model.
ALLOWED_TOOLS = [
    "mcp__fraudsim__list_scenarios",
    "mcp__fraudsim__start_run",
    "mcp__fraudsim__stop_run",
    "mcp__fraudsim__get_status",
    "mcp__fraudsim__inject_ring",
    "mcp__fraudsim__list_rings",
    "mcp__fraudsim__explain_ring",
    "mcp__fraudsim__get_graph",
    "mcp__fraudsim__get_metrics",
    "mcp__fraudsim__get_oracle",
    "mcp__fraudsim__recent_events",
    "mcp__fraudsim__focus_graph",
    "mcp__fraudsim__clear_focus",
    "mcp__fraudsim__send_feedback",
]

# Claude Code's own tools. Blocked: a browser tab must not reach the filesystem.
BLOCKED_TOOLS = [
    "Bash", "Read", "Write", "Edit", "NotebookEdit",
    "WebFetch", "WebSearch", "Task", "Glob", "Grep",
]

SYSTEM = (
    "You are answering questions about a running fraud-ring detection simulator, "
    "using the fraudsim tools. Always call a tool rather than guessing; if a tool "
    "returns nothing useful, say so plainly. Keep answers short - a few sentences - "
    "and quote the actual numbers you got back. get_oracle is the simulator's "
    "answer key: use it to check whether the detector was right, never to decide "
    "what should be flagged. When the user asks to see or look at something, call "
    "focus_graph so the dashboard shows it while you explain."
)

_lock = asyncio.Lock()


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION)


class AskResponse(BaseModel):
    answer: str
    tools_used: list[str]
    cost_usd: float | None = None
    duration_s: float | None = None


def _cli() -> str | None:
    return shutil.which("claude")


def _mcp_config() -> dict[str, Any]:
    src = Path(__file__).resolve().parents[2]
    import sys

    return {
        "mcpServers": {
            "fraudsim": {
                "command": sys.executable,
                "args": ["-m", "fraudsim.mcp_server"],
                "env": {
                    "FRAUDSIM_API": os.environ.get("FRAUDSIM_SELF_URL", "http://localhost:8000"),
                    "PYTHONPATH": str(src),
                },
            }
        }
    }


@router.get("/status")
async def status() -> dict[str, Any]:
    """Whether questions can be answered here, and if not, why not."""
    cli = _cli()
    if cli is None:
        return {
            "available": False,
            "reason": (
                "The Claude Code CLI was not found. It runs on the host, so start the API "
                "on the host (make demo) rather than in the container."
            ),
        }
    return {"available": True, "reason": None, "busy": _lock.locked()}


@router.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest) -> AskResponse:
    """Answer one question about the live run by calling the fraudsim tools."""
    cli = _cli()
    if cli is None:
        raise HTTPException(status_code=503, detail=(await status())["reason"])
    if _lock.locked():
        raise HTTPException(status_code=429, detail="A question is already being answered.")

    async with _lock:
        with tempfile.TemporaryDirectory(prefix="fraudsim-assistant-") as tmp:
            config = Path(tmp) / "mcp.json"
            config.write_text(json.dumps(_mcp_config()))

            argv = [
                cli, "-p", req.question,
                "--mcp-config", str(config),
                "--strict-mcp-config",
                "--append-system-prompt", SYSTEM,
                "--allowed-tools", *ALLOWED_TOOLS,
                "--disallowed-tools", *BLOCKED_TOOLS,
                "--output-format", "stream-json",
                "--verbose",
            ]
            started = time.perf_counter()
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=tmp,  # not the project directory
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_S)
            except TimeoutError:
                proc.kill()
                raise HTTPException(
                    status_code=504, detail=f"No answer within {TIMEOUT_S:.0f}s."
                ) from None

    if proc.returncode != 0:
        detail = (err or b"").decode(errors="replace").strip()[:400] or "the CLI failed"
        raise HTTPException(status_code=502, detail=detail)

    answer, tools, cost = _parse(out.decode(errors="replace"))
    if not answer:
        raise HTTPException(status_code=502, detail="The CLI returned no answer.")

    return AskResponse(
        answer=answer,
        tools_used=tools,
        cost_usd=cost,
        duration_s=round(time.perf_counter() - started, 1),
    )


def _parse(stream: str) -> tuple[str, list[str], float | None]:
    """Pull the final answer, the fraudsim tools called, and the cost out of the stream."""
    answer, tools, cost = "", [], None
    for line in stream.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("type") == "assistant":
            for block in msg.get("message", {}).get("content", []):
                name = block.get("name", "")
                if block.get("type") == "tool_use" and name.startswith("mcp__fraudsim__"):
                    short = name.removeprefix("mcp__fraudsim__")
                    if short not in tools:
                        tools.append(short)
        elif msg.get("type") == "result":
            answer = str(msg.get("result") or "")
            cost = msg.get("total_cost_usd")
    return answer.strip(), tools, cost
