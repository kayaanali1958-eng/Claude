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

    if exit_code != 0:
        state["fails"] += 1
        if state["fails"] in (1, 3, 10):          # don't spam: 1st, 3rd and 10th failure in a row
            push("Desk run FAILED", f"Exit code {exit_code}, {state['fails']} failed run(s) in a row. "
                 "Check logs\\desk-<date>.log on the laptop.", "high")
    else:
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
