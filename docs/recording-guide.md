# Screen-recording guide — 2 minutes

A two-minute video is about 300 spoken words. That is the whole budget, so the
narration below *is* the script: it carries the gap, the method, both of our
additions and the limitations. The clicking is only there to give the words
something to point at.

The long walkthrough that used to be in this file is in git history
(`git show a9f1171:docs/recording-guide.md`) if you ever need the full version.
`docs/final-demo.md` is the spoken script for the live viva.

---

## Set up before you hit record

Scenario **demo_story (120 users, 90s)**, seed **7**, speed **5×**, tick
**Oracle view**. If a focus bar is showing from an earlier question, click
**show everything**. Reload so the graph starts empty.

Start the stack with `make demo` — not `./start.sh`. The Ask panel needs it.
Check `http://localhost:8000/assistant/status` says `"available": true` first.

---

## The script

Say it close to word-for-word. It is timed; improvising will overrun.

| Time | On screen | Say |
|---|---|---|
| **0:00** | Press **Start**, graph fills | "A fraud ring is one person running many fake accounts. Each looks normal alone — what gives them away is a shared device, card or IP. But offices and public wifi share those too. Existing work either links shared attributes hard, or caps degree and throws the busy ones away — which throws away any ring hiding behind them." |
| **0:20** | Point at the **Scoreboard** | "This is a live simulator — rings form, grow and try to evade. Every ring was planted, so there's an answer key. Those are precision, recall and time-to-detect against ground truth, not a demo number." |
| **0:35** | Point at the dropdown reading **1/ln(1+deg)** | "The core method replaces that degree cap with a continuous weight: a shared attribute contributes one over log of its degree, with time decay. A busy hub counts for almost nothing alone, but a group sharing that hub *and* trading among itself still surfaces. F1 0.73, against 0.21 for degree-capped rules." |
| **0:55** | Click **Ask the system**, type the question below, **keep talking while it thinks** | "Our first addition is this. Every panel here exists because we decided in advance to build it — and you cannot pre-build every question an analyst will ask. This box queries the live detector through MCP tools and then redraws the graph. It runs against the local Claude Code CLI, so there's no API key." |
| **1:15** | Graph narrows; point at the tool chips | "The chips are the tools it actually called — so you can see it queried the system rather than inventing a plausible number." |
| **1:30** | Nothing — talk over the still screen | "Our second addition is a red-team search. The evaluation tests five evasion strategies, but we wrote those five ourselves, so it searches the space around them for behaviour the detector misses. It runs; we are not quoting a rate from it yet." |
| **1:45** | Nothing | "Limitations. All of this is synthetic — there is no public dataset with real shared-attribute links. Slow rings and identity rotation still get past us. And the detector itself is the project's core work; the question box and the red-team search are what we added on top of it." |

The question to type at 0:55:

```
Show me the highest-risk confirmed ring on the graph, hiding everything
else, and explain in two sentences why it was flagged.
```

---

## The five things that must be said

Tick these off after watching it back. If one is missing, the video is a product
demo, not a project submission.

| | Where it is |
|---|---|
| **The gap** — shared attributes are the cheap signal, but hubs make them unusable, and capping degree hides rings | 0:00 |
| **Evidence it works** — ground truth, not a screenshot | 0:20 |
| **The method** — continuous `1/ln(1+deg)` weight instead of a cap, with the measured F1 | 0:35 |
| **The novelty** — a question box that queries the live detector through MCP and redraws the graph, no API key | 0:55 |
| **Honesty** — synthetic data, known misses, and whose work is whose | 1:30 and 1:45 |

---

## Three things that will cost you the take

- **The answer takes 15–30 s.** That is why you talk across it. Don't wait in
  silence, and don't retry if it fails — say the 1:15 line and move on.
- **Don't toggle the weighting dropdown on camera.** On a finished run it does
  nothing; mid-run the numbers move because the run is still growing as well as
  because of the weighting. The clean comparison needs two runs at one seed.
  Pointing at it and saying the measured number is accurate and takes five
  seconds.
- **Each question costs 15–25 cents.** Ask one.

---

## If you only have one minute

Keep **0:00**, **0:55**, **1:15** and **1:45** — the gap, the novelty, the proof
it really queried, and the limitations. Drop the scoreboard and the weighting
rows and say the F1 number inside the first line instead.

The question box is the novelty, so it is the one thing that cannot be cut.
