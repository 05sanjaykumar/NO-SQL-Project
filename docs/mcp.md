# Investigating by conversation (MCP)

## What this is

The dashboard answers the questions it was built to answer. Every panel exists
because someone decided in advance to build it, and you cannot pre-build every
question an analyst will ask.

This exposes the running detection system as MCP tools, so an assistant can be
asked things nobody anticipated - *"which devices appear in more than one ring?"*,
*"did this account share an IP with a confirmed fraudster before the ring formed?"*
- and answer them by querying the live system and drawing the result.

It drives the **same live run as the dashboard**. Inject a ring from a
conversation and it appears on screen immediately.

## Setup

1. Start the stack:

   ```bash
   ./start.sh
   ```

2. Add the server to your MCP client. For Claude Desktop, edit
   `~/Library/Application Support/Claude/claude_desktop_config.json`:

   ```json
   {
     "mcpServers": {
       "fraudsim": {
         "command": "/Users/sanjaykumar/Documents/vscode projects/NO_SQL/.venv/bin/python",
         "args": ["-m", "fraudsim.mcp_server"],
         "env": { "FRAUDSIM_API": "http://localhost:8000" }
       }
     }
   }
   ```

3. Restart the client. Twelve `fraudsim` tools should appear.

No API key is needed - the assistant you already use calls the tools.

## The tools

| Tool | What it does |
|---|---|
| `list_scenarios` | the simulated worlds available |
| `start_run` / `stop_run` | control the live simulation |
| `get_status` | scenario, seed, simulated time, events so far |
| `inject_ring` | plant a fraud ring of a given type and evasion, right now |
| `list_rings` | every ring the detector holds, with risk scores |
| `explain_ring` | members, features, reason, contributions, counterfactual, evidence |
| `get_graph` | nodes and edges of the live graph, for drawing |
| `get_metrics` | live precision, recall and F1 against ground truth |
| `get_oracle` | the answer key: real rings, correct flags, false alarms, misses |
| `recent_events` | the tail of the event stream the detector sees |
| `send_feedback` | confirm or dismiss an alert, as an analyst would |

`get_oracle` is ground truth. It is there to check whether the detector was
right, never to decide what to flag - the server's instructions say so, because
an assistant that peeks at the answer key makes the evaluation meaningless.

## Things to ask it

```
Start the adversarial scenario, inject a shared-device ring with no evasion,
wait, then tell me whether it was caught and draw the ring.

Now inject the same ring with low_and_slow evasion. Compare what happened.

Which of the current alerts are false alarms? Check against the oracle.

Explain the highest-scoring ring in plain English, and draw its evidence subgraph.
```

The demo that lands is the one you do not rehearse: ask someone in the room to
invent a question. A pre-built dashboard cannot survive that; this can.

## Honest scope

This is an **interface** contribution, not an algorithmic one. MCP servers for
graph databases already exist, and so does asking questions of a graph in natural
language. What is new here is not the idea of conversing with a database - it is
that the investigation tools, the live simulator and the answer key are in one
place, so a question can be asked, answered and checked against ground truth in
a single exchange.

Claim it as a usability and demonstration feature. The research claims belong
elsewhere.
