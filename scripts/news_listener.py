#!/usr/bin/env python3
"""Live news listener for the trading desk.

Runs in the background on your laptop and appends every new item to news/live.jsonl,
which policy-watch and news-analyst read on every desk run.

Sources (each one is skipped if its keys are missing):
  - Alpaca news WebSocket (real-time, Benzinga-sourced): ALPACA_KEY_ID, ALPACA_SECRET
  - X accounts (polled every X_POLL_SECONDS):          X_CONSUMER_KEY, X_CONSUMER_SECRET

Keys are read from the environment or from a .env file next to this repo's root.
Never commit .env; it is in .gitignore.

Requires: pip install websockets
"""
import asyncio, base64, json, os, sys, time, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "news" / "live.jsonl"
ALPACA_URL = "wss://stream.data.alpaca.markets/v1beta1/news"
X_ACCOUNTS = ["WhiteHouse", "POTUS", "realDonaldTrump", "federalreserve", "USTreasury"]
X_POLL_SECONDS = int(os.environ.get("X_POLL_SECONDS", "60"))


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def write_item(source, headline, url="", symbols=None, created_at=None, extra=None):
    OUT.parent.mkdir(exist_ok=True)
    item = {
        "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "created_at": created_at,
        "source": source,
        "headline": headline,
        "symbols": symbols or [],
        "url": url,
    }
    if extra:
        item.update(extra)
    with OUT.open("a") as f:
        f.write(json.dumps(item) + "\n")


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ---------- Alpaca news stream ----------
async def alpaca_stream():
    key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET")
    if not (key and secret):
        log("Alpaca: no ALPACA_KEY_ID/ALPACA_SECRET, skipping")
        return
    import websockets
    while True:
        try:
            async with websockets.connect(ALPACA_URL, ping_interval=20) as ws:
                await ws.send(json.dumps({"action": "auth", "key": key, "secret": secret}))
                await ws.send(json.dumps({"action": "subscribe", "news": ["*"]}))
                log("Alpaca: connected")
                async for raw in ws:
                    for msg in json.loads(raw):
                        t = msg.get("T")
                        if t == "n":
                            write_item("alpaca/" + (msg.get("source") or "news"), msg.get("headline", ""),
                                       msg.get("url", ""), msg.get("symbols"), msg.get("created_at"),
                                       {"summary": msg.get("summary", "")[:400]})
                        elif t == "error":
                            log(f"Alpaca error: {msg}")
                            if msg.get("code") in (402, 404, 406, 409):  # auth / subscription problems
                                return
        except Exception as e:  # reconnect on any drop
            log(f"Alpaca: disconnected ({e}); retrying in 10s")
            await asyncio.sleep(10)


# ---------- X polling ----------
def x_bearer(key, secret):
    creds = base64.b64encode(f"{urllib.parse.quote(key)}:{urllib.parse.quote(secret)}".encode()).decode()
    req = urllib.request.Request(
        "https://api.x.com/oauth2/token", data=b"grant_type=client_credentials", method="POST",
        headers={"Authorization": f"Basic {creds}",
                 "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)["access_token"]


def x_get(path, token):
    req = urllib.request.Request("https://api.x.com/2/" + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


async def x_poll():
    key, secret = os.environ.get("X_CONSUMER_KEY"), os.environ.get("X_CONSUMER_SECRET")
    if not (key and secret):
        log("X: no X_CONSUMER_KEY/X_CONSUMER_SECRET, skipping")
        return
    try:
        token = await asyncio.to_thread(x_bearer, key, secret)
        users = await asyncio.to_thread(x_get, "users/by?usernames=" + ",".join(X_ACCOUNTS), token)
        ids = {u["id"]: u["username"] for u in users.get("data", [])}
        log(f"X: watching {', '.join(ids.values())}")
    except Exception as e:
        log(f"X: setup failed ({e}). Reading posts needs a paid X API tier (Basic or higher).")
        return
    since = {}
    while True:
        for uid, name in ids.items():
            try:
                q = "users/%s/tweets?max_results=5&tweet.fields=created_at" % uid
                if uid in since:
                    q += "&since_id=" + since[uid]
                data = await asyncio.to_thread(x_get, q, token)
                tweets = data.get("data", [])
                if tweets:
                    if uid in since:  # skip the backlog on the first poll
                        for tw in reversed(tweets):
                            write_item("x/@" + name, tw["text"], f"https://x.com/{name}/status/{tw['id']}",
                                       created_at=tw.get("created_at"))
                    since[uid] = tweets[0]["id"]
            except urllib.error.HTTPError as e:
                log(f"X: @{name} HTTP {e.code}" + (" (rate limited)" if e.code == 429 else ""))
            except Exception as e:
                log(f"X: @{name} {e}")
        await asyncio.sleep(X_POLL_SECONDS)


async def main():
    load_env()
    log(f"writing to {OUT}")
    await asyncio.gather(alpaca_stream(), x_poll())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
