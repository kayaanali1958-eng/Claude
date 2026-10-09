#!/usr/bin/env python3
"""Phone alerts for the desk, via ntfy.sh (free app for iPhone/Android, no account needed).

Called by the timer after every desk run:
    python scripts/notify.py <claude exit code>
Sends a push when the run failed, or when journal.md gained an entry with a trade, a closed desk,
or the daily recap. Remembers what it already sent in news/notify_state.json.

Setup: install the ntfy app, subscribe to a topic name only you know (e.g. desk-kayaa-7f3k9),
and put NTFY_TOPIC=<that name> in .env.
"""
import json, os, re, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "news" / "notify_state.json"
KEYWORDS = re.compile(r"APPROVED|MANAGED|PAPER (BUY|SELL)|FILLED|desk_closed|DESK CLOSED|^## Recap", re.M)


def load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def push(title, body, priority="default"):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    title = title.encode("ascii", "replace").decode()          # HTTP headers must be plain ASCII
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body[:3500].encode("utf-8"), method="POST",
                                 headers={"Title": title, "Priority": priority, "Tags": "chart_with_upwards_trend"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"notify failed: {e}")


def last_run_output(lines=8):
    """The last lines the most recent desk run printed (usually the error), from logs/desk-*.log."""
    logs = sorted((ROOT / "logs").glob("desk-*.log"))
    if not logs:
        return "(no desk log found)"
    text = logs[-1].read_text(encoding="utf-8", errors="replace")
    run = text[text.rfind("===== run"):]
    out = [l for l in run.splitlines()[1:] if l.strip() and not l.startswith("===== exit")]
    return "\n".join(out[-lines:]) or "(the run printed nothing)"


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    load_env()
    exit_code = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].lstrip("-").isdigit() else 0
    STATE.parent.mkdir(exist_ok=True)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"journal_len": 0, "fails": 0}
    journal = (ROOT / "journal.md").read_text(encoding="utf-8") if (ROOT / "journal.md").exists() else ""

    last = last_run_output() if exit_code != 0 else ""
    if exit_code != 0 and re.search(r"session limit|usage limit|rate limit", last, re.I):
        if not state.get("limited"):              # once per limit, not every run
            push("Claude usage limit hit", "The stocks desk pauses and retries every hour until the limit resets. "
                 f"Crypto stops on Robinhood stay in place.\n{last}", "high")
        state["limited"] = True
    elif exit_code != 0:
        state["fails"] += 1
        if state["fails"] in (1, 3, 10):          # don't spam: 1st, 3rd and 10th failure in a row
            push("Desk run FAILED", f"Exit code {exit_code}, {state['fails']} failed run(s) in a row.\n"
                 f"Last output:\n{last}", "high")
    else:
        state["limited"] = False
        if state["fails"] >= 3:
            push("Desk recovered", "Runs are working again.")
        state["fails"] = 0

    new = journal[state["journal_len"]:] if len(journal) >= state["journal_len"] else journal
    for entry in re.split(r"(?=^### |^## Recap)", new, flags=re.M):
        if entry.strip() and KEYWORDS.search(entry):
            first = entry.strip().splitlines()[0].lstrip("# ").strip()
            title = "Daily recap" if entry.startswith("## Recap") else "Desk: " + first
            push(title, entry.strip())
    state["journal_len"] = len(journal)
    STATE.write_text(json.dumps(state), encoding="utf-8")


if __name__ == "__main__":
    main()
