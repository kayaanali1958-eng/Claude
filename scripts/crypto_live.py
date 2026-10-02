"""Live crypto execution for crypto_desk.py, through Claude Code and the Robinhood connection.

Only used when settings.md says `CRYPTO_MODE: live`. Each function runs one short headless Claude Code
call that may use ONLY the Robinhood crypto tools listed below, always previews before placing, and
must end its answer with one line:  RESULT: {json}

Every live buy is followed immediately by a real stop order on Robinhood (good till canceled), so the
loss is capped by Robinhood itself even if the laptop is off. Exits cancel that stop and sell at market.
"""
import json, os, re, shutil, subprocess, uuid

TOOLS = ["get_accounts", "get_portfolio", "get_crypto_quotes", "get_crypto_positions", "get_crypto_orders",
         "preview_crypto_order", "place_crypto_order", "cancel_crypto_order"]
PREFIXES = ["mcp__robinhood-trading__", "mcp__RobinHood__", "mcp__claude_ai_RobinHood__"]
ACCOUNT = ("Use only the Agentic account: call get_accounts and pick the one account you are allowed to trade, "
           "and pass its rhs_account_number (never rhc_account_number).")


def _claude():
    return os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"


def run(task, tools=TOOLS):
    allowed = [p + t for p in PREFIXES for t in tools]
    prompt = (f"You are the crypto execution step of a trading desk. Do exactly this task and nothing else.\n"
              f"{ACCOUNT}\nAlways call preview_crypto_order before place_crypto_order and abort if the preview shows "
              f"any error or warning, or a price more than 1% away from the one given.\nTASK: {task}\n"
              f"End your answer with exactly one line: RESULT: <json>. On any problem, return "
              f'RESULT: {{"ok": false, "error": "<what happened>"}}.')
    try:
        out = subprocess.run([_claude(), "-p", prompt, "--allowedTools", *allowed, "--permission-mode", "dontAsk"],
                             capture_output=True, text=True, timeout=600).stdout
    except Exception as e:
        return {"ok": False, "error": f"claude call failed: {e}"}
    m = re.findall(r"RESULT:\s*(\{.*\})", out)
    if not m:
        return {"ok": False, "error": "no RESULT line", "raw": out[-800:]}
    try:
        return json.loads(m[-1])
    except json.JSONDecodeError:
        return {"ok": False, "error": "bad RESULT json", "raw": m[-1][:400]}


def buy(coin, dollars, stop):
    return run(
        f"Buy ${dollars:.2f} of {coin} with a market order (dollar_amount '{dollars:.2f}', ref_id '{uuid.uuid4()}'). "
        f"Wait until it is filled (check get_crypto_orders, up to 2 minutes). Then place a SELL stop order "
        f"(type stop_loss, time_in_force gtc) for the exact filled quantity at stop_price {stop:.6g}, ref_id "
        f"'{uuid.uuid4()}'. If the buy filled but the stop order fails, retry the stop once; if it still fails, "
        f"sell the whole filled quantity at market. "
        f'RESULT keys: ok, filled_qty, avg_price, buy_order_id, stop_order_id.')


def move_stop(coin, qty, old_stop_order_id, new_stop):
    return run(
        f"Raise the protective stop on {qty:.8g} {coin}: cancel open order {old_stop_order_id} (confirm it is canceled), "
        f"then place a SELL stop order (stop_loss, gtc) for {qty:.8g} {coin} at stop_price {new_stop:.6g}, "
        f"ref_id '{uuid.uuid4()}'. If the new stop can't be placed, place the old one again at its old price. "
        f"RESULT keys: ok, stop_order_id.")


def sell_all(coin, stop_order_id, reason):
    return run(
        f"Exit the {coin} position ({reason}). If order {stop_order_id} is still open, cancel it and confirm. "
        f"Then check get_crypto_positions: if any {coin} quantity is left, sell all of it with a market order "
        f"(quantity = the position's full available amount, ref_id '{uuid.uuid4()}') and wait for the fill. "
        f"If the stop order already filled, don't sell again. "
        f"RESULT keys: ok, sold_qty, avg_price (the price the position was actually closed at), how ('stop' or 'market').")


def balance():
    """The account's cash available for crypto, read only (no order tools)."""
    return run("Report how much of the account's own cash can be spent on crypto right now: settled cash only, "
               "never margin or borrowed buying power (no leverage). Do not place, preview or cancel anything. RESULT keys: ok, cash (a number in dollars).",
               tools=["get_accounts", "get_portfolio"])
