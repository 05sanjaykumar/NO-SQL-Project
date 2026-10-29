# Ask the system

A panel in the dashboard that answers questions about the live run in plain
English, by querying the running detector rather than guessing.

The rest of the dashboard answers the questions it was built for; every panel
exists because someone decided in advance to build it. This answers the ones
nobody anticipated - *"which devices appear in more than one ring?"*, *"why was
this one missed?"* - and shows which tools it called, so you can see it really
queried the system.

## Running it

The panel needs the **Claude Code CLI**, which lives on the host. So the API has
to run on the host too:

```bash
docker compose up -d                      # Neo4j and Redis only
cd web && npm run build && cd ..
python -m fraudsim.cli api --port 8000
```

or simply `make demo`, which does the same thing.

Then open http://localhost:8000/ui/ and the panel is under the alert queue.

Started with `docker compose --profile app up` instead, the API runs inside a
container where `claude` does not exist. The panel then shows exactly that and
disables itself; nothing else is affected.

**No API key.** The CLI uses the Claude session already on the machine.
It is not free, though: each question costs roughly $0.10-$0.30 against that
account, which is why the panel answers one question at a time and says what
each answer cost.

## How it works

```
browser  ->  POST /assistant/ask  ->  claude -p  ->  fraudsim MCP server  ->  live API
```

The endpoint shells out to the CLI with the fraudsim MCP server attached, reads
the streamed reply, and returns the answer plus the tools that were called.

## What it is allowed to do

A web request starting a local process deserves care, so the CLI is pinned down:

| Flag | Effect |
|---|---|
| `--strict-mcp-config` | only the fraudsim server loads, not the operator's other MCP servers |
| `--allowed-tools` | an explicit list of the twelve fraudsim tools |
| `--disallowed-tools` | Bash, Read, Write, Edit, WebFetch, WebSearch, Task, Glob, Grep blocked |
| `cwd` | a temporary directory, never the project |

It can therefore query and drive the simulator, and nothing else. Even so, the
API is meant for localhost. Do not expose it to a network with this enabled.

The system prompt also tells it that `get_oracle` is ground truth for *checking*
the detector and never for deciding what to flag - an assistant that reads the
answer key would make the evaluation meaningless.

## Worth knowing

`list_rings` returns a capped summary rather than every ring. On a busy run the
full list reached about 479,000 characters, too large to be read back; it is now
sorted by risk, capped, and reduced to one row per ring, with counts by status.
Call `explain_ring` for the detail of any single one.

## Scope

This is an **interface** contribution. MCP servers for databases exist, and so
does querying a graph in natural language. What is particular here is that the
investigation tools, the live simulator and the answer key sit behind one
question box, so a question can be asked, answered from live data, and checked
against ground truth in a single exchange.
