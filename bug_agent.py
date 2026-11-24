"""
Bug hunting + alert triage agent (starter), built on the Claude Agent SDK.

Setup:
    pip install claude-agent-sdk
    export ANTHROPIC_API_KEY=...
    # optional, for alert monitoring (Sentry example):
    export SENTRY_TOKEN=... SENTRY_ORG=... SENTRY_PROJECT=...

Usage:
    python bug_agent.py hunt  /path/to/repo [focus area]
    python bug_agent.py watch /path/to/repo [poll_seconds]

The agent is READ-ONLY (Read/Grep/Glob). It reports findings; it never edits code.
Check option names against the current Agent SDK docs, since the API evolves.
"""
import json
import os
import sys
import urllib.request

import anyio
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

HUNT_PROMPT = """You are a careful bug hunter reviewing a codebase you own access to.
Look for real defects: logic errors, unhandled errors, race conditions, resource leaks,
off-by-one errors, bad input handling, and misuse of APIs.
For each finding report: file:line, what is wrong, how to trigger it, severity
(high/medium/low), and a suggested fix. Only report issues you can point to in the code;
say 'unverified' if you are inferring. Do not pad the list with style nits."""

TRIAGE_PROMPT = """You triage production alerts for the codebase in the working directory.
Given an alert, find the likely root cause in the code (stack trace, culprit, recent logic),
rate severity and likely blast radius, and propose a minimal fix.
Say clearly how confident you are and what you could not verify."""


def options(system_prompt: str, repo: str) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["Read", "Grep", "Glob"],  # read-only by design
        cwd=repo,
        max_turns=30,
    )


async def run(prompt: str, opts: ClaudeAgentOptions) -> str:
    out = []
    async for msg in query(prompt=prompt, options=opts):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    out.append(block.text)
    return "\n".join(out)


def fetch_alerts() -> list[dict]:
    """Example source: Sentry unresolved issues. Swap for Datadog, PagerDuty, logs, etc."""
    org, project, token = (os.environ.get(k) for k in ("SENTRY_ORG", "SENTRY_PROJECT", "SENTRY_TOKEN"))
    if not (org and project and token):
        return []
    url = f"https://sentry.io/api/0/projects/{org}/{project}/issues/?query=is:unresolved"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


async def hunt(repo: str, focus: str = "the whole repo") -> None:
    report = await run(f"Hunt for bugs in: {focus}. Start with the riskiest code paths.", options(HUNT_PROMPT, repo))
    print(report)


async def watch(repo: str, poll_seconds: int = 300) -> None:
    seen: set[str] = set()
    while True:
        try:
            for alert in fetch_alerts():
                if alert["id"] in seen:
                    continue
                seen.add(alert["id"])
                summary = {k: alert.get(k) for k in ("title", "culprit", "count", "firstSeen", "permalink")}
                triage = await run(f"New alert:\n{json.dumps(summary, indent=2)}", options(TRIAGE_PROMPT, repo))
                print(f"\n=== {summary['title']} ===\n{triage}\n")  # replace with Slack/email/ticket
        except Exception as exc:  # keep the monitor alive
            print(f"watch error: {exc}", file=sys.stderr)
        await anyio.sleep(poll_seconds)


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("hunt", "watch"):
        sys.exit(__doc__)
    mode, repo, extra = sys.argv[1], sys.argv[2], sys.argv[3:]
    if mode == "hunt":
        anyio.run(hunt, repo, *(extra[:1] or []))
    else:
        anyio.run(watch, repo, int(extra[0]) if extra else 300)