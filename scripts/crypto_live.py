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
ACCOUNT = ("Use only the Agentic account (the account ending 0701): call get_accounts and pick that account; "
           "if it is not there, stop and return ok false. "
           "Pass its rhs_account_number (never rhc_account_number).")


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


def buy(coin, dollars, stop, ref_price):
    cap = ref_price * 1.003
    return run(
        f"Buy about ${dollars:.2f} of {coin} with a LIMIT order, not a market order (Robinhood's market orders fill "
        f"well above the real price). First call get_crypto_quotes for {coin}. If its ask price is above "
        f"{cap:.6g} (0.3% over the market price {ref_price:.6g}), place nothing and return ok false with error "
        f"'spread too wide'. Otherwise place a limit buy at limit price {cap:.6g}, quantity = {dollars:.2f} / {cap:.6g} "
        f"rounded down to the coin's allowed increment, time_in_force gtc, ref_id '{uuid.uuid4()}'. Wait up to "
        f"2 minutes for the fill (get_crypto_orders). If it is not filled, cancel it; if it is partly filled, cancel "
        f"the rest and keep the filled part. If nothing filled, return ok false with error 'not filled'. "
        f"Then place a SELL stop order "
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
    return run("Report how much of the account's own cash can be spent on crypto right now, including deposits "
               "Robinhood already lets the account use, but never margin or borrowed buying power (no leverage), and the account's total value (cash plus every stock, "
               "fund and crypto position). Do not place, preview or cancel anything. "
               "Also list what the account holds: every crypto position (coin code and quantity) and every stock or fund "
               "position (symbol and number of shares). "
               "RESULT keys: ok, cash (a number in dollars), total (a number in dollars), "
               "holdings (an object like {\"LTC\": 9.71, \"BITX\": 12}).",
               tools=["get_accounts", "get_portfolio", "get_crypto_positions", "get_equity_positions"])


# 2x crypto funds (stocks on Robinhood, market hours only). Whole shares, so a real stop order is allowed.
FUND_TOOLS = ["get_accounts", "get_portfolio", "get_equity_quotes", "get_equity_positions", "get_equity_orders",
              "review_equity_order", "place_equity_order", "cancel_equity_order"]


def buy_fund(symbol, shares, stop, ref_price):
    cap = ref_price * 1.003
    return run(
        f"Stock order, not crypto (ignore the crypto preview rule; use review_equity_order before every "
        f"place_equity_order instead). First call get_equity_quotes for {symbol}: if the ask is above {cap:.2f} "
        f"(0.3% over the market price {ref_price:.2f}), place nothing and return ok false with error 'spread too wide'. "
        f"Otherwise buy {shares} whole shares of {symbol} with a LIMIT order at {cap:.2f} during regular hours. "
        f"If it is not filled within 2 minutes, cancel it and return ok false with error 'spread too wide'. Wait until it is filled (check get_equity_orders, up to 2 minutes). Then place a SELL stop order "
        f"(stop loss, good till canceled, regular hours) for the filled shares at stop price {stop:.2f}. If the buy "
        f"filled but the stop order fails, retry once; if it still fails, sell the filled shares at market. "
        f"RESULT keys: ok, filled_qty, avg_price, buy_order_id, stop_order_id.", tools=FUND_TOOLS)


def sell_fund(symbol, stop_order_id, reason):
    return run(
        f"Stock order, not crypto (use review_equity_order before place_equity_order). Exit the {symbol} position "
        f"({reason}). If order {stop_order_id} is still open, cancel it and confirm. Then check get_equity_positions: "
        f"if any {symbol} shares are left, sell all of them with a market order and wait for the fill. If the stop "
        f"order already filled, don't sell again. RESULT keys: ok, sold_qty, avg_price (the price the position "
        f"was actually closed at), how ('stop' or 'market').", tools=FUND_TOOLS)
