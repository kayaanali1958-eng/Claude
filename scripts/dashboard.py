#!/usr/bin/env python3
"""Phone dashboard for the desk: a small read-only web page served from the laptop.

Open http://<laptop-ip>:8765 on a phone on the same Wi-Fi, then "Add to Home Screen" to use it like
an app. It shows the live book (equity, cash, the open trade with its stop and P&L), progress to the
$2,000 goal, the latest trades, and the practice accounts. The page refreshes itself every minute.
It never places orders. scripts/crypto_desk.py starts it automatically if it isn't running.
"""
import html, json, socket, sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
GOAL = 2000


def read_json(name):
    try:
        return json.loads((ROOT / name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def tail(name, n):
    try:
        lines = [l for l in (ROOT / name).read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
        return lines[-n:][::-1]
    except Exception:
        return []


def last_run():
    try:
        line = (ROOT / "logs" / "crypto_desk.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1]
        return line[:16]
    except Exception:
        return "unknown"


def live_px(sym):
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import crypto_desk as cd
        return cd.live_price(sym)
    except Exception:
        return None


def page():
    st = read_json("crypto_live_state.json") or read_json("crypto_state.json")
    total = st.get("account_total") or st.get("equity") or 0
    pos_html = "<p class=muted>No open trade. Waiting for a tested setup.</p>"
    for p in st.get("positions", []):
        px = live_px(p["coin"])
        if p.get("fund"):
            lev = p.get("lev", 2)
            fund_now = p["fund_entry"] * (1 + lev * (px / p["entry"] - 1)) if px else None
            pnl = (fund_now - p["fund_entry"]) * p["fund_qty"] if fund_now else None
            name = f'{p["fund_qty"]} {p["fund"]} ({lev}x {p["coin"]})'
            entry, stop = p["fund_entry"], p.get("fund_stop")
        else:
            pnl = (px - p["entry"]) * p["qty"] if px else None
            name = f'{p["qty"]:.4f} {p["coin"]}'
            entry, stop = p["entry"], p["stop"]
        cls = "up" if (pnl or 0) >= 0 else "down"
        be = " · stop at breakeven or higher" if p["stop"] >= p["entry"] else ""
        pos_html = (f'<div class=card><div class=big>{html.escape(name)}</div>'
                    f'<div>Bought {entry:,.2f} · stop {stop:,.2f}{be}</div>'
                    f'<div class="big {cls}">{"" if pnl is None else f"${pnl:+,.2f}"}</div>'
                    f'<div class=muted>{html.escape(p.get("strategy", ""))}</div></div>')
    goal_pct = max(0, min(100, total / GOAL * 100)) if total else 0
    trades = "".join(f"<li>{html.escape(l[2:])}</li>" for l in tail("crypto_live_journal.md", 12)) or "<li>None yet</li>"
    lt = read_json("ai_portfolio_state.json")
    fp = read_json("futures_paper_state.json")
    flags = []
    if st.get("paused"):
        flags.append("PAUSED (big drop): tell Claude to review")
    if st.get("waiting"):
        flags.append("Waiting for a fair price on: " + ", ".join(st["waiting"]))
    flags_html = "".join(f"<div class=warn>{html.escape(f)}</div>" for f in flags)
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=60><link rel=manifest href=/manifest.json>
<meta name=apple-mobile-web-app-capable content=yes><meta name=theme-color content="#0b1220">
<title>Desk</title><style>
body{{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0b1220;color:#e6edf6}}
main{{max-width:520px;margin:auto;padding:16px}} h1{{font-size:20px;margin:4px 0 12px}}
h2{{font-size:15px;color:#8aa0bd;margin:20px 0 8px;text-transform:uppercase;letter-spacing:.05em}}
.card{{background:#131c2e;border-radius:14px;padding:14px;margin:8px 0}} .big{{font-size:22px;font-weight:700}}
.up{{color:#3ddc97}} .down{{color:#ff6b6b}} .muted{{color:#8aa0bd;font-size:13px}}
.bar{{background:#1f2a40;border-radius:8px;height:12px;overflow:hidden}} .fill{{background:#3ddc97;height:12px}}
.warn{{background:#4a2a12;color:#ffcf99;border-radius:10px;padding:10px;margin:8px 0}}
ul{{padding-left:18px;font-size:13px;line-height:1.5}} .row{{display:flex;justify-content:space-between}}
</style></head><body><main>
<h1>Trading desk</h1>{flags_html}
<div class=card><div class=row><div><div class=muted>Account</div><div class=big>${total:,.2f}</div></div>
<div><div class=muted>Cash</div><div class=big>${st.get("cash", 0):,.2f}</div></div></div>
<div class=muted style="margin-top:10px">Goal ${GOAL:,}: {goal_pct:.0f}%</div>
<div class=bar><div class=fill style="width:{goal_pct:.0f}%"></div></div></div>
<h2>Open trade</h2>{pos_html}
<h2>Latest activity</h2><div class=card><ul>{trades}</ul></div>
<h2>Practice accounts (paper)</h2><div class=card>
<div class=row><span>Long-term funds + AI</span><b>${lt.get("value", lt.get("start", 0)):,.2f}</b></div>
<div class=row><span>Futures</span><b>${fp.get("cash", fp.get("start", 0)):,.2f}</b></div></div>
<p class=muted>Last desk run {html.escape(last_run())} · refreshed {datetime.now():%H:%M} · read only</p>
</main></body></html>"""


MANIFEST = {"name": "Trading desk", "short_name": "Desk", "start_url": "/", "display": "standalone",
            "background_color": "#0b1220", "theme_color": "#0b1220", "icons": []}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/manifest.json"):
            body, kind = json.dumps(MANIFEST).encode(), "application/manifest+json"
        else:
            try:
                body = page().encode("utf-8")
            except Exception as e:
                body = f"<p>Dashboard error: {html.escape(str(e))}</p>".encode()
            kind = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))                 # no data is sent; this only picks the Wi-Fi address
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    print(f"Dashboard on http://{lan_ip()}:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
