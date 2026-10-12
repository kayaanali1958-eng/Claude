#!/usr/bin/env python3
"""Trading desk dashboard: a read-only app page served from the laptop.

Phone (same Wi-Fi): http://<laptop-ip>:8765, then "Add to Home Screen".
Laptop: the "Trading Desk" desktop icon (scripts/make_app.ps1) opens it in its own app window.
Shows the account, progress to $2,000, the account value over time, the open trade between its
stop and target, recent activity and the practice accounts. Refreshes every minute. Never trades.
scripts/crypto_desk.py starts it automatically if it isn't running.
"""
import html, json, re, socket, sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
GOAL = 2000
ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")          # the owner's clock (Sugar Land, TX)


def read_json(name):
    try:
        return json.loads((ROOT / name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def journal(n):
    try:
        lines = [l[2:] for l in (ROOT / "crypto_live_journal.md").read_text(encoding="utf-8").splitlines()
                 if l.startswith("- ")]
        return lines[-n:][::-1]
    except Exception:
        return []


def last_run_minutes():
    try:
        line = (ROOT / "logs" / "crypto_desk.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1]
        t = datetime.strptime(line[:16], "%Y-%m-%d %H:%M")
        return int((datetime.now() - t).total_seconds() // 60)
    except Exception:
        return None


def live_px(sym):
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import crypto_desk as cd
        return cd.live_price(sym)
    except Exception:
        return None


def market_open():
    t = datetime.now(ET)
    m = t.hour * 60 + t.minute
    return t.weekday() < 5 and 570 <= m < 960


def money(x, sign=False):
    return f"{'+' if sign and x >= 0 else '-' if x < 0 else ''}${abs(x):,.2f}"


def chart(history):
    """Account value over time: one series, so no legend; crosshair + tooltip on hover."""
    pts = [(h[0], h[1]) for h in history if isinstance(h, list) and len(h) == 2][-500:]
    if len(pts) < 2:
        return '<p class=muted>The chart fills in as the desk runs (one point per hour).</p>'
    W, H, P = 640, 200, 6
    vals = [v for _, v in pts]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.1 or max(hi * 0.01, 1)
    lo, hi = lo - pad, hi + pad
    xs = [P + i * (W - 2 * P) / (len(pts) - 1) for i in range(len(pts))]
    ys = [H - P - (v - lo) / (hi - lo) * (H - 2 * P) for v in vals]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    area = f"{xs[0]:.1f},{H - P} {line} {xs[-1]:.1f},{H - P}"
    data = json.dumps([[datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(CT).strftime("%b %d, %I:%M %p"), v]
                       for t, v in pts])
    rows = "".join(f"<tr><td>{html.escape(d)}</td><td>{money(v)}</td></tr>" for d, v in json.loads(data)[::-1][:24])
    return f"""
<div class=chartwrap>
 <svg id=eq viewBox="0 0 {W} {H}" preserveAspectRatio=none role=img aria-label="Account value over time">
  <line x1=0 x2={W} y1={H - P} y2={H - P} class=axis />
  <polygon points="{area}" class=area />
  <polyline points="{line}" class=line />
  <line id=xh x1=0 x2=0 y1=0 y2={H} class=xh visibility=hidden />
  <circle id=dot r=5 class=dot visibility=hidden />
 </svg>
 <div id=tip class=tip hidden></div>
</div>
<div class="row muted small"><span>{json.loads(data)[0][0]}</span><span>{json.loads(data)[-1][0]}</span></div>
<details><summary class=muted>Show as table</summary><table>{rows}</table></details>
<script>
(() => {{
 const D = {data}, X = {json.dumps([round(x, 1) for x in xs])}, Y = {json.dumps([round(y, 1) for y in ys])};
 const svg = document.getElementById('eq'), tip = document.getElementById('tip');
 const xh = document.getElementById('xh'), dot = document.getElementById('dot');
 function show(ev) {{
  const r = svg.getBoundingClientRect(), cx = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left;
  const x = cx / r.width * {W}; let i = 0, best = 1e9;
  X.forEach((v, k) => {{ if (Math.abs(v - x) < best) {{ best = Math.abs(v - x); i = k; }} }});
  xh.setAttribute('x1', X[i]); xh.setAttribute('x2', X[i]); dot.setAttribute('cx', X[i]); dot.setAttribute('cy', Y[i]);
  xh.setAttribute('visibility', 'visible'); dot.setAttribute('visibility', 'visible');
  tip.hidden = false; tip.textContent = D[i][0] + '  ·  $' + D[i][1].toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
  tip.style.left = Math.min(Math.max(X[i] / {W} * r.width, 70), r.width - 70) + 'px';
 }}
 function hide() {{ tip.hidden = true; xh.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); }}
 svg.addEventListener('mousemove', show); svg.addEventListener('touchmove', show, {{passive: true}});
 svg.addEventListener('touchstart', show, {{passive: true}}); svg.addEventListener('mouseleave', hide);
}})();
</script>"""


def trade_card(p):
    px = live_px(p["coin"])
    if p.get("fund"):
        lev = p.get("lev", 2)
        to_fund = lambda c: p["fund_entry"] * (1 + lev * (c / p["entry"] - 1))
        name, qty, entry = p["fund"], p["fund_qty"], p["fund_entry"]
        stop = p.get("fund_stop") or to_fund(p["stop"])
        target = to_fund(p["target"]) if p.get("target") else None
        now = to_fund(px) if px else None
        sub = (f"{lev}x short {p['coin'][1:]}" if p["coin"].startswith("-") else f"{lev}x {p['coin']}") + f" · {qty} shares"
    else:
        name, qty, entry, stop, target, now = p["coin"], p["qty"], p["entry"], p["stop"], p.get("target"), px
        sub = f"{qty:.4f} coins"
    pnl = (now - entry) * qty if now else None
    up = (pnl or 0) >= 0
    pnl_html = (f'<div class="big {"up" if up else "down"}">{"▲" if up else "▼"} {money(pnl, True)}</div>'
                if pnl is not None else '<div class=muted>price loading…</div>')
    hi = target or max(entry + 3 * (entry - stop), now or entry)
    lo = min(stop, now or stop)
    pos = lambda v: max(0, min(100, (v - lo) / (hi - lo) * 100)) if hi > lo else 50
    marker = f'<div class=now style="left:{pos(now):.1f}%"></div>' if now else ""
    safe = "Stop at breakeven or higher: this trade can't lose" if p["stop"] >= p["entry"] else "Stop below entry: max loss " + money((entry - stop) * qty)
    exit_txt = f"Take profit {target:,.2f}" if target else "No cap: stop trails up behind the price"
    return f"""
<div class=card>
 <div class=row><div><div class=big>{html.escape(name)}</div><div class=muted>{html.escape(sub)}</div></div>{pnl_html}</div>
 <div class=range><div class=fill2 style="left:{pos(stop):.1f}%;width:{pos(hi) - pos(stop):.1f}%"></div>
  <div class=tick style="left:{pos(entry):.1f}%"></div>{marker}</div>
 <div class="row small"><span>Stop {stop:,.2f}</span><span>Bought {entry:,.2f}</span><span>{"Target " + f"{target:,.2f}" if target else "Trailing"}</span></div>
 <div class="pill {"good" if p["stop"] >= p["entry"] else ""}">{"✔" if p["stop"] >= p["entry"] else "🛡"} {html.escape(safe)}</div>
 <div class="muted small">{html.escape(exit_txt)} · {html.escape(p.get("strategy", ""))}</div>
</div>"""


def pretty(text):
    """Journal line -> (tag, sentence, P&L or None) in plain English."""
    m = re.search(r"BUY ([\d.]+) (\w+)(?: \((\d)x (\w+)\))? @ ([\d,.]+)", text)
    if m:
        qty, sym, lev, under, px = m.groups()
        what = f"{sym} ({lev}x {under})" if lev else sym
        return "buy", f"Bought {qty} {what} at ${px}", None
    m = re.search(r"SELL ([\d.]+) (\w+).*?@ ([\d,.]+) \(([^)]*)\).*?P&L \$([+-][\d,.]+)", text)
    if m:
        qty, sym, px, why, pnl = m.groups()
        v = float(pnl.replace(",", ""))
        return ("win" if v >= 0 else "loss"), f"Sold {qty} {sym} at ${px} · {why}", v
    if "blackout" in text.lower() or "news" in text.lower():
        return "news", text.split(":")[0], None
    return "info", text[:110], None


def activity():
    items = []
    for line in journal(15):
        stamp, _, text = line.partition(" · ")
        try:
            when = datetime.strptime(stamp.strip(), "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc).astimezone(CT).strftime("%a %b %d · %I:%M %p")
        except ValueError:
            when = stamp
        tag, sentence, pnl = pretty(text)
        chip = f'<span class="chip {"up" if pnl >= 0 else "down"}">{money(pnl, True)}</span>' if pnl is not None else ""
        items.append(f'<li><span class="tag {tag}">{tag.upper()}</span><div class=grow><div>{html.escape(sentence)}</div>'
                     f'<div class="muted small">{when}</div></div>{chip}</li>')
    return "".join(items) or '<li class=muted>Nothing yet. The first trade will show up here.</li>'


def stats(st):
    closed = [c for c in st.get("closed", []) if isinstance(c.get("pnl"), (int, float))]
    wins = sum(1 for c in closed if c["pnl"] > 0)
    total = sum(c["pnl"] for c in closed)
    best = max((c["pnl"] for c in closed), default=0)
    cell = lambda k, v, cls="": f'<div class=stat><div class="muted small">{k}</div><div class="sv {cls}">{v}</div></div>'
    return (cell("Trades", str(len(closed))) + cell("Win rate", f"{wins / len(closed) * 100:.0f}%" if closed else "–")
            + cell("Profit", money(total, True), "up" if total >= 0 else "down") + cell("Best", money(best, True) if closed else "–", "up"))


def ring(pct):
    r, c = 34, 2 * 3.14159 * 34
    return (f'<svg class=ring viewBox="0 0 84 84" aria-label="{pct:.0f}% of the goal"><circle cx=42 cy=42 r={r} class=rt />'
            f'<circle cx=42 cy=42 r={r} class=rf stroke-dasharray="{c:.1f}" stroke-dashoffset="{c * (1 - pct / 100):.1f}" />'
            f'<text x=42 y=40 class=rp>{pct:.0f}%</text><text x=42 y=55 class=rl>of $2K</text></svg>')


def page():
    st = read_json("crypto_live_state.json") or read_json("crypto_state.json")
    hist = st.get("history", [])
    total = st.get("account_total") or st.get("equity") or 0
    first = hist[0][1] if hist else st.get("start", total)
    change = total - first if first else 0
    goal_pct = max(0, min(100, total / GOAL * 100)) if total else 0
    mins = last_run_minutes()
    running = mins is not None and mins <= 70
    pills = [f'<span class="pill {"good" if running else "warn"}">● {"Running" if running else "Last run " + (f"{mins} min ago" if mins is not None else "unknown")}</span>',
             f'<span class=pill>{"Market open" if market_open() else "Market closed"}</span>']
    if st.get("paused"):
        pills.append('<span class="pill bad">⏸ Paused after a big drop</span>')
    if st.get("shock_until", 0) > datetime.now(timezone.utc).timestamp():
        pills.append('<span class="pill warn">📰 Breaking-news pause</span>')
    if st.get("waiting"):
        pills.append(f'<span class="pill warn">⏳ Waiting for a fair price: {", ".join(st["waiting"])}</span>')
    trades = "".join(trade_card(p) for p in st.get("positions", [])) or \
        '<div class="card muted">No open trade. Watching Bitcoin, Ether, XRP, S&amp;P, Nasdaq and chips for a tested setup, up (buys) or down (inverse funds), 9 AM–2 PM Central.</div>'
    lt, fp = read_json("ai_portfolio_state.json"), read_json("futures_paper_state.json")
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta http-equiv=refresh content=60><link rel=manifest href=/manifest.json>
<meta name=apple-mobile-web-app-capable content=yes><meta name=apple-mobile-web-app-title content="Desk">
<meta name=theme-color content="#0e1117"><title>Trading Desk</title>
<link rel=icon href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%230e1117'/%3E%3Cpolyline points='10,44 24,30 34,38 54,16' fill='none' stroke='%233987e5' stroke-width='6' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<style>
:root{{color-scheme:dark;--bg:#0e1117;--surface-1:#171b23;--surface-2:#1f2530;--text-primary:#f5f6f8;--text-secondary:#a7adb8;
--muted:#7d8592;--series-1:#3987e5;--good:#2fbf71;--bad:#f0605d;--warn:#e5a33a;--line:#2a313d}}
*{{box-sizing:border-box}} body{{margin:0;min-height:100vh;color:var(--text-primary);background:var(--bg);
font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
body::before{{content:"";position:fixed;inset:-20%;z-index:-1;pointer-events:none;filter:blur(40px);
background:radial-gradient(40% 30% at 15% 10%,rgba(57,135,229,.35),transparent 70%),
radial-gradient(35% 30% at 90% 20%,rgba(144,133,233,.30),transparent 70%),
radial-gradient(45% 35% at 50% 100%,rgba(47,191,113,.16),transparent 70%);animation:drift 24s ease-in-out infinite alternate}}
@keyframes drift{{to{{transform:translate3d(3%,-2%,0) scale(1.06)}}}}
@media (prefers-reduced-motion:reduce){{body::before{{animation:none}}}}
main{{max-width:560px;margin:auto;padding:max(16px,env(safe-area-inset-top)) 16px 32px}}
.card{{backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px)}}
.hero{{background:linear-gradient(145deg,rgba(57,135,229,.18),rgba(23,27,35,.75) 55%);border-color:rgba(57,135,229,.35);
box-shadow:0 10px 40px rgba(57,135,229,.15)}}
.ring{{width:96px;height:96px;flex:0 0 auto}} .rt{{fill:none;stroke:var(--surface-2);stroke-width:8}}
.rf{{fill:none;stroke:var(--series-1);stroke-width:8;stroke-linecap:round;transform:rotate(-90deg);transform-origin:42px 42px;
transition:stroke-dashoffset 1.2s ease}} .rp{{fill:var(--text-primary);font-size:17px;font-weight:700;text-anchor:middle}}
.rl{{fill:var(--muted);font-size:9px;text-anchor:middle}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:8px 0}}
.stat{{background:rgba(23,27,35,.75);border:1px solid var(--line);border-radius:14px;padding:10px;text-align:center;backdrop-filter:blur(14px)}}
.sv{{font-weight:700;font-size:16px;margin-top:2px}} .grow{{flex:1}}
.chip{{flex:0 0 auto;font-weight:700;font-size:13px;align-self:center}}
.tag.news{{color:var(--warn)}}
h1{{font-size:18px;margin:0;font-weight:650;letter-spacing:.01em}} h2{{font-size:12px;color:var(--muted);margin:22px 0 8px;
text-transform:uppercase;letter-spacing:.08em;font-weight:600}}
.card{{background:rgba(23,27,35,.72);border:1px solid var(--line);border-radius:16px;padding:16px;margin:8px 0}}
.hero .val{{font-size:38px;font-weight:700;letter-spacing:-.02em}} .big{{font-size:20px;font-weight:650}}
.up{{color:var(--good)}} .down{{color:var(--bad)}} .muted{{color:var(--muted)}} .small{{font-size:12.5px}}
.row{{display:flex;justify-content:space-between;align-items:center;gap:8px}}
.pills{{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 4px}}
.pill{{display:inline-block;background:var(--surface-2);border:1px solid var(--line);border-radius:999px;padding:4px 10px;
font-size:12.5px;color:var(--text-secondary);margin-top:10px}} .pills .pill{{margin:0}}
.pill.good{{color:var(--good)}} .pill.warn{{color:var(--warn)}} .pill.bad{{color:var(--bad)}}
.bar{{background:var(--surface-2);border-radius:999px;height:10px;overflow:hidden;margin:8px 0 4px}}
.bar>div{{background:var(--series-1);height:10px;border-radius:999px}}
.chartwrap{{position:relative}} svg{{width:100%;height:180px;display:block;touch-action:pan-y}}
.line{{fill:none;stroke:var(--series-1);stroke-width:2;vector-effect:non-scaling-stroke;stroke-linejoin:round}}
.area{{fill:var(--series-1);opacity:.12}} .axis{{stroke:var(--line);stroke-width:1;vector-effect:non-scaling-stroke}}
.xh{{stroke:var(--muted);stroke-width:1;vector-effect:non-scaling-stroke}} .dot{{fill:var(--series-1);stroke:var(--surface-1);stroke-width:2}}
.tip{{position:absolute;top:-6px;transform:translateX(-50%);background:var(--surface-2);border:1px solid var(--line);
border-radius:8px;padding:4px 8px;font-size:12.5px;white-space:nowrap;pointer-events:none}}
.range{{position:relative;height:10px;background:var(--surface-2);border-radius:999px;margin:16px 0 6px}}
.fill2{{position:absolute;top:0;height:10px;background:linear-gradient(90deg,var(--bad),var(--muted) 35%,var(--good));opacity:.45;border-radius:999px}}
.tick{{position:absolute;top:-4px;width:2px;height:18px;background:var(--text-secondary)}}
.now{{position:absolute;top:-5px;width:20px;height:20px;margin-left:-10px;border-radius:50%;background:var(--series-1);border:3px solid var(--surface-1)}}
ul{{list-style:none;padding:0;margin:0}} li{{display:flex;gap:10px;padding:10px 0;border-top:1px solid var(--line);font-size:13.5px}}
li:first-child{{border-top:0}} .tag{{flex:0 0 auto;font-size:11px;font-weight:700;border-radius:6px;padding:2px 6px;height:fit-content;background:var(--surface-2);color:var(--text-secondary)}}
.tag.buy{{color:var(--series-1)}} .tag.win{{color:var(--good)}} .tag.loss{{color:var(--bad)}}
table{{width:100%;font-size:12.5px;border-collapse:collapse;margin-top:6px}} td{{padding:3px 0;border-top:1px solid var(--line)}} td+td{{text-align:right}}
details summary{{cursor:pointer;font-size:12.5px;margin-top:6px}}
</style></head><body><main>
<div class=row><h1>Trading Desk</h1><span class="muted small">{datetime.now(CT):%I:%M %p} CT</span></div>
<div class=pills>{"".join(pills)}</div>
<div class="card hero"><div class=row><div><div class=muted>Account value</div>
 <div class=val data-count="{total:.2f}">{money(total)}</div>
 <div class="{"up" if change >= 0 else "down"}">{"▲" if change >= 0 else "▼"} {money(change, True)} since tracking started</div></div>{ring(goal_pct)}</div>
 <div class=bar><div style="width:{goal_pct:.1f}%"></div></div>
 <div class="row small muted"><span>Cash {money(st.get("cash", 0))}</span><span>{money(max(GOAL - total, 0))} to the $2,000 goal</span></div></div>
<div class=stats>{stats(st)}</div>
<h2>Account value</h2><div class=card>{chart(hist)}</div>
<h2>Open trade</h2>{trades}
<h2>Activity</h2><div class=card><ul>{activity()}</ul></div>
<h2>Practice accounts · paper money</h2><div class=card>
 <div class=row><span>Long-term funds + AI stocks</span><b>{money(lt["value"]) if lt.get("value") else "starts after the close"}</b></div>
 <div class=row style="margin-top:6px"><span>Futures (MES / MNQ)</span><b>{money(fp["cash"]) if fp.get("cash") else "starts on the next run"}</b></div></div>
<p class="muted small" style="text-align:center;margin-top:18px">Read only · refreshes every minute</p>
<script>
document.querySelectorAll('[data-count]').forEach(el => {{
 const end = parseFloat(el.dataset.count), t0 = performance.now(), d = 900;
 if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
 (function f(t) {{ const k = Math.min(1, (t - t0) / d), e = 1 - Math.pow(1 - k, 3);
  el.textContent = '$' + (end * e).toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
  if (k < 1) requestAnimationFrame(f); }})(t0);
}});
</script>
</main></body></html>"""


MANIFEST = {"name": "Trading Desk", "short_name": "Desk", "start_url": "/", "display": "standalone",
            "background_color": "#0e1117", "theme_color": "#0e1117", "icons": []}


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
