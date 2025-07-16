from flask import Flask, request, render_template_string, jsonify, session, redirect, url_for, flash
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import threading
import logging
import time

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("main.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key")

# --- Jinja price format filter ---
def format_price(price):
    try:
        price = float(price)
        if price >= 1:
            return f"${price:,.2f}"
        elif price >= 0.01:
            return f"${price:,.4f}"
        elif price > 0:
            return f"${price:,.8f}"
        else:
            return "$0.00"
    except Exception:
        return "--"

app.jinja_env.filters['format_price'] = format_price

SETTINGS_PASSWORD = "bot"
STARTING_BALANCE = 1000.00
file_lock = threading.Lock()

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

BOTS = {
    "1.0": {"name": "Coinbot 1.0", "color": "#06D1BF", "data_file": os.path.join(DATA_DIR, "account_1.json")},
    "2.0": {"name": "Coinbot 2.0", "color": "#FACB39", "data_file": os.path.join(DATA_DIR, "account_2.json")},
    "3.0": {"name": "Coinbot 3.0", "color": "#FF4B57", "data_file": os.path.join(DATA_DIR, "account_3.json")},
}

def pretty_now():
    return datetime.now(ZoneInfo("America/Edmonton")).strftime('%Y-%m-%d %H:%M:%S %Z')

kraken_pairs = {
    "BTCUSDT": "XBTUSDT",
    "ETHUSDT": "ETHUSDT",
    "SOLUSDT": "SOLUSDT",
    "DOGEUSDT": "DOGEUSDT",
    "AVAXUSDT": "AVAXUSDT",
    "MATICUSDT": "MATICUSDT",
    "ADAUSDT": "ADAUSDT",
    "LTCUSDT": "LTCUSDT",
    "DOTUSDT": "DOTUSDT",
    "PEPEUSD": "PEPEUSD",
}

latest_prices = {}
last_price_update = {'time': pretty_now(), 'prev_time': pretty_now()}

def fetch_latest_prices(symbols):
    symbols_to_fetch = set(sym for sym in symbols if sym in kraken_pairs)
    symbols_to_fetch.add("BTCUSDT")
    prices = {}
    got_one = False
    for sym in symbols_to_fetch:
        pair = kraken_pairs[sym]
        url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
        try:
            resp = requests.get(url, timeout=10)
            data = resp.json()
            if 'result' in data and data['result']:
                result = list(data['result'].values())[0]
                last = float(result['c'][0])
                prices[sym] = last
                got_one = True
        except Exception as e:
            logger.warning(f"Error fetching {sym} from Kraken: {e}")
    if got_one:
        latest_prices.update(prices)
        prev_time = last_price_update['time']
        last_price_update['prev_time'] = prev_time
        last_price_update['time'] = pretty_now()
        logger.info(f"Fetched Kraken prices at {last_price_update['time']} for: {', '.join(prices.keys())}")
    else:
        logger.warning("Kraken API returned no prices, using previous prices")
    return latest_prices.copy()

def get_kraken_price(symbol):
    return latest_prices.get(symbol, 0)

def load_account(bot_id):
    data_file = BOTS[bot_id]["data_file"]
    if not os.path.exists(data_file):
        return {"balance": STARTING_BALANCE, "positions": {}, "trade_log": []}
    try:
        with file_lock:
            with open(data_file, "r") as f:
                account = json.load(f)
        account["balance"] = float(account.get("balance", STARTING_BALANCE))
        account["positions"] = account.get("positions", {})
        account["trade_log"] = account.get("trade_log", [])
        for symbol in account["positions"]:
            for position in account["positions"][symbol]:
                position["volume"] = float(position.get("volume", 0))
                position["entry_price"] = float(position.get("entry_price", 0))
                position["leverage"] = int(position.get("leverage", 1))
                position["margin_used"] = float(position.get("margin_used", 0))
        return account
    except Exception as e:
        logger.error(f"Error loading account {bot_id}: {str(e)}")
        return {"balance": STARTING_BALANCE, "positions": {}, "trade_log": []}

def save_account(bot_id, account):
    data_file = BOTS[bot_id]["data_file"]
    try:
        with file_lock:
            with open(data_file, "w") as f:
                json.dump(account, f, indent=2, default=str)
        logger.info(f"Account data saved for bot {bot_id}")
    except Exception as e:
        logger.error(f"Error saving account {bot_id}: {str(e)}")
        raise

def calculate_position_stats(positions, prices):
    position_stats = []
    for symbol, position_list in positions.items():
        current_price = prices.get(symbol, 0)
        for position in position_list:
            entry = float(position.get("entry_price", 0))
            volume = float(position.get("volume", 0))
            leverage = int(position.get("leverage", 1))
            margin_used = float(position.get("margin_used", 0))
            if not margin_used and entry and volume and leverage:
                margin_used = (entry * volume) / leverage
            position_size = margin_used * leverage
            pnl = (current_price - entry) * volume if entry and current_price else 0
            pl_class = "profit" if pnl > 0 else "loss" if pnl < 0 else ""
            position_stats.append({
                'symbol': symbol,
                'volume': volume,
                'entry_price': entry,
                'current_price': current_price,
                'leverage': leverage,
                'margin_used': margin_used,
                'position_size': position_size,
                'pnl': pnl,
                'pl_class': pl_class
            })
    return position_stats

def load_bot_settings(bot_id):
    settings_file = os.path.join(DATA_DIR, f"settings_{bot_id}.json")
    if not os.path.exists(settings_file):
        return {"leverage": 5, "stop_loss_pct": 2.5}
    with open(settings_file, "r") as f:
        return json.load(f)

def save_bot_settings(bot_id, settings):
    settings_file = os.path.join(DATA_DIR, f"settings_{bot_id}.json")
    with open(settings_file, "w") as f:
        json.dump(settings, f, indent=2)

def calculate_coin_stats(trade_log):
    coin_stats = {}
    for log in trade_log:
        if 'symbol' in log and 'profit' in log and log['profit'] is not None:
            coin = log['symbol']
            profit = float(log['profit']) if log['profit'] is not None else 0
            coin_stats[coin] = coin_stats.get(coin, 0) + profit
    return coin_stats

def get_bitcoin_price():
    price = latest_prices.get("BTCUSDT")
    return f"${price:,.2f}" if price else "--"

# --- WEBHOOK ROUTE ---
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        logger.info(f"Webhook received: {data}")

        # Bot selection
        bot_raw = str(data.get("bot", "")).strip().lower()
        bot_id = bot_raw.replace("coinbot", "").replace(" ", "") if bot_raw.startswith("coinbot") else bot_raw
        if bot_id not in BOTS:
            return jsonify({"status": "error", "message": f"Unknown bot: {bot_id}"}), 400

        action = str(data.get("action", "")).lower()
        if action not in ["buy", "sell"]:
            return jsonify({"status": "error", "message": f"Invalid action: {action}"}), 400

        symbol = str(data.get("symbol", "")).upper()
        if not symbol:
            return jsonify({"status": "error", "message": "Missing symbol"}), 400

        # --- Fetch settings for this bot ---
        settings = load_bot_settings(bot_id)
        leverage = settings.get("leverage", 5)
        margin_pct = 0.05  # Still hard-coded. You could add this to settings if desired.

        # --- FIX: Fetch latest price on-demand if needed ---
        price = get_kraken_price(symbol)
        if not price or price <= 0:
            fetch_latest_prices([symbol])
            price = get_kraken_price(symbol)
            if not price or price <= 0:
                return jsonify({"status": "error", "message": f"No live Kraken price for {symbol}"}), 400

        account = load_account(bot_id)
        timestamp = pretty_now()
        reason = data.get("reason", "TradingView signal")

        if action == "buy":
            margin_used = account["balance"] * margin_pct

            if margin_used <= 0:
                return jsonify({"status": "error", "message": "Insufficient balance for allocation"}), 400

            # Calculate volume to buy based on margin_used, leverage, and Kraken price
            volume = round((margin_used * leverage) / price, 6)

            # Position limits check
            if len(account["positions"].get(symbol, [])) >= 5:
                return jsonify({"status": "error", "message": "Position limit reached"}), 400

            # Create new position
            new_position = {
                "volume": volume,
                "entry_price": price,
                "timestamp": timestamp,
                "margin_used": margin_used,
                "leverage": leverage,
            }
            account["positions"].setdefault(symbol, []).append(new_position)
            account["balance"] -= margin_used

            account["trade_log"].append({
                "timestamp": timestamp,
                "action": "buy",
                "symbol": symbol,
                "reason": reason,
                "price": price,
                "amount": volume,
                "balance": round(account["balance"], 2),
                "leverage": leverage,
            })
            save_account(bot_id, account)
            logger.info(f"BUY executed for {symbol} at {price} with volume {volume} (bot {bot_id})")
            return jsonify({"status": "success", "action": "buy", "symbol": symbol, "price": price, "volume": volume}), 200

        elif action == "sell":
            positions = account["positions"].get(symbol, [])
            if not positions:
                return jsonify({"status": "error", "message": "No positions to sell"}), 400
            total_volume = sum(float(p["volume"]) for p in positions)
            total_margin = sum(float(p.get("margin_used", (float(p.get("entry_price", 0)) * float(p.get("volume", 0)) / float(p.get("leverage", 1)))))
                              for p in positions)
            avg_entry = sum(float(p["entry_price"]) * float(p["volume"]) for p in positions) / total_volume if total_volume > 0 else 0
            # --- FIXED LOGIC: DO NOT multiply profit by leverage! ---
            profit = (price - avg_entry) * total_volume
            pl_pct = ((price - avg_entry) / avg_entry * 100) if avg_entry > 0 else 0

            account["balance"] += total_margin + profit
            account["positions"][symbol] = []  # Clear positions
            account["trade_log"].append({
                "timestamp": timestamp,
                "action": "sell",
                "symbol": symbol,
                "reason": reason,
                "price": price,
                "amount": total_volume,
                "profit": round(profit, 2),
                "pl_pct": round(pl_pct, 2),
                "balance": round(account["balance"], 2),
                "leverage": leverage,
                "avg_entry": round(avg_entry, 6),
            })
            save_account(bot_id, account)
            logger.info(f"SELL executed for {symbol} at {price} (bot {bot_id})")
            return jsonify({"status": "success", "action": "sell", "symbol": symbol, "price": price}), 200

        return jsonify({"status": "error", "message": "Unhandled action"}), 400

    except Exception as e:
        logger.error(f"Webhook processing failed: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500

# --- BACKGROUND STOP LOSS & PRICE CHECK THREAD ---
def check_and_trigger_stop_losses():
    while True:
        try:
            for bot_id in BOTS:
                account = load_account(bot_id)
                settings = load_bot_settings(bot_id)
                leverage = settings.get("leverage", 5)
                stop_loss_pct = settings.get("stop_loss_pct", 2.5)
                for symbol, positions in account["positions"].items():
                    kraken_price = get_kraken_price(symbol)
                    if kraken_price == 0:
                        logger.warning(f"No price for {symbol}, skipping stop loss check")
                        continue
                    new_positions = []
                    for position in positions:
                        entry = float(position["entry_price"])
                        lev = int(position.get("leverage", leverage))
                        stop_loss_price = entry * (1 - (stop_loss_pct / 100) / lev)
                        if kraken_price > 0 and kraken_price <= stop_loss_price:
                            logger.info(f"Stop loss triggered for {symbol} at price {kraken_price:.6f} (entry: {entry:.6f}, stop: {stop_loss_price:.6f})")
                            reason = "Stop Loss"
                            timestamp = pretty_now()
                            volume = float(position["volume"])
                            margin_used = float(position["margin_used"])
                            # --- FIXED LOGIC: DO NOT multiply profit by leverage! ---
                            profit = (kraken_price - entry) * volume
                            pl_pct = ((kraken_price - entry) / entry * 100) if entry > 0 else 0
                            account["balance"] += margin_used + profit
                            account["trade_log"].append({
                                "timestamp": timestamp,
                                "action": "sell",
                                "symbol": symbol,
                                "reason": reason,
                                "price": kraken_price,
                                "amount": volume,
                                "profit": round(profit, 2),
                                "pl_pct": round(pl_pct, 2),
                                "balance": round(account["balance"], 2),
                                "leverage": lev,
                                "avg_entry": round(entry, 6),
                            })
                        else:
                            new_positions.append(position)
                    account["positions"][symbol] = new_positions
                save_account(bot_id, account)
        except Exception as e:
            logger.error(f"Error in stop loss checker: {str(e)}", exc_info=True)
        needed_symbols = set()
        for bot_id in BOTS:
            account = load_account(bot_id)
            needed_symbols.update(account["positions"].keys())
        needed_symbols.add("BTCUSDT")
        fetch_latest_prices(list(needed_symbols))
        time.sleep(2)

stop_loss_thread = threading.Thread(target=check_and_trigger_stop_losses, daemon=True)
stop_loss_thread.start()

# You may have a dashboard or other endpoints below, add as needed

if __name__ == '__main__':
    logger.info("Starting Flask server on port 5000")
    app.run(host='0.0.0.0', port=5000, threaded=True)
