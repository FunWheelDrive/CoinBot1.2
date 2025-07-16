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

# --- Configuration ---
SETTINGS_PASSWORD = "bot"  # CHANGE for production!
STARTING_BALANCE = 1000.00
file_lock = threading.Lock()
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

BOTS = {
    "1.0": {
        "name": "Coinbot 1.0",
        "color": "#06D1BF",
        "data_file": os.path.join(DATA_DIR, "account_1.json")
    },
    "2.0": {
        "name": "Coinbot 2.0",
        "color": "#FACB39",
        "data_file": os.path.join(DATA_DIR, "account_2.json")
    },
    "3.0": {
        "name": "Coinbot 3.0",
        "color": "#FF4B57",
        "data_file": os.path.join(DATA_DIR, "account_3.json")
    }
}

def pretty_now():
    return datetime.now(ZoneInfo("America/Edmonton")).strftime('%Y-%m-%d %H:%M:%S %Z')

# --- Kraken API Setup ---
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

# --- Account Management ---
def load_account(bot_id):
    data_file = BOTS[bot_id]["data_file"]
    if not os.path.exists(data_file):
        return {
            "balance": STARTING_BALANCE,
            "positions": {},
            "trade_log": []
        }
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
                position["stop_loss_pct"] = float(position.get("stop_loss_pct", 2.5))
        return account
    except Exception as e:
        logger.error(f"Error loading account {bot_id}: {str(e)}")
        return {
            "balance": STARTING_BALANCE,
            "positions": {},
            "trade_log": []
        }

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

def load_bot_settings(bot_id):
    settings_file = os.path.join(DATA_DIR, f"settings_{bot_id}.json")
    if not os.path.exists(settings_file):
        return {"leverage": 5, "stop_loss_pct": 2.5, "take_profit_pct": 5.0}
    with open(settings_file, "r") as f:
        return json.load(f)

def save_bot_settings(bot_id, settings):
    settings_file = os.path.join(DATA_DIR, f"settings_{bot_id}.json")
    with open(settings_file, "w") as f:
        json.dump(settings, f, indent=2)

# --- Position Calculations ---
def calculate_position_stats(positions, prices):
    position_stats = []
    for symbol, position_list in positions.items():
        current_price = prices.get(symbol, 0)
        for position in position_list:
            entry = float(position.get("entry_price", 0))
            volume = float(position.get("volume", 0))
            leverage = int(position.get("leverage", 1))
            margin_used = float(position.get("margin_used", 0))
            stop_loss_pct = float(position.get("stop_loss_pct", 2.5))
            
            if not margin_used and entry and volume and leverage:
                margin_used = (entry * volume) / leverage
            
            position_size = margin_used * leverage
            pnl = (current_price - entry) * volume
            stop_loss_price = entry * (1 - stop_loss_pct/100)
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
                'pl_class': pl_class,
                'stop_loss_price': stop_loss_price,
                'stop_loss_pct': stop_loss_pct
            })
    return position_stats

# ... [Rest of your existing functions (calculate_coin_stats, get_bitcoin_price, etc.) ...]

# --- Webhook Endpoint with Kraken-style Stop Loss ---
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

        # Load settings
        settings = load_bot_settings(bot_id)
        leverage = settings.get("leverage", 5)
        stop_loss_pct = settings.get("stop_loss_pct", 2.5)
        margin_pct = 0.05  # 5% of balance per trade

        # Get current price
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

            volume = round((margin_used * leverage) / price, 6)
            if len(account["positions"].get(symbol, [])) >= 5:
                return jsonify({"status": "error", "message": "Position limit reached"}), 400

            # Create new position with stop loss info
            new_position = {
                "volume": volume,
                "entry_price": price,
                "timestamp": timestamp,
                "margin_used": margin_used,
                "leverage": leverage,
                "stop_loss_pct": stop_loss_pct,
                "stop_loss_price": price * (1 - stop_loss_pct/100)
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
                "stop_loss_pct": stop_loss_pct
            })
            
            save_account(bot_id, account)
            logger.info(f"BUY executed for {symbol} at {price} with SL {stop_loss_pct}% (bot {bot_id})")
            return jsonify({
                "status": "success",
                "action": "buy",
                "symbol": symbol,
                "price": price,
                "volume": volume,
                "stop_loss_price": new_position["stop_loss_price"]
            }), 200

        elif action == "sell":
            positions = account["positions"].get(symbol, [])
            if not positions:
                return jsonify({"status": "error", "message": "No positions to sell"}), 400

            total_volume = sum(float(p["volume"]) for p in positions)
            total_margin = sum(float(p["margin_used"]) for p in positions)
            avg_entry = sum(float(p["entry_price"]) * float(p["volume"]) for p in positions) / total_volume if total_volume > 0 else 0
            
            profit = (price - avg_entry) * total_volume
            pl_pct = ((price - avg_entry) / avg_entry * 100) if avg_entry > 0 else 0

            account["balance"] += total_margin + profit
            account["positions"][symbol] = []
            
            account["trade_log"].append({
                "timestamp": timestamp,
                "action": "sell",
                "symbol": symbol,
                "reason": reason,
                "price": price,
                "amount": total_volume,
                "profit": round(profit, 2),
                "pl_pct": round(pl_pct, 2),
                "balance": round(account["balance"], 2)
            })
            
            save_account(bot_id, account)
            logger.info(f"SELL executed for {symbol} at {price} (bot {bot_id})")
            return jsonify({"status": "success", "action": "sell", "symbol": symbol, "price": price}), 200

        return jsonify({"status": "error", "message": "Unhandled action"}), 400

    except Exception as e:
        logger.error(f"Webhook processing failed: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500

# --- Stop Loss Checker with Kraken-style Implementation ---
def check_and_trigger_stop_losses():
    while True:
        try:
            for bot_id in BOTS:
                account = load_account(bot_id)
                positions_modified = False
                
                for symbol, position_list in account["positions"].items():
                    current_price = get_kraken_price(symbol)
                    if current_price == 0:
                        continue
                        
                    new_positions = []
                    for position in position_list:
                        entry = float(position["entry_price"])
                        stop_loss_price = float(position.get("stop_loss_price", entry * 0.975))  # Default 2.5% if not set
                        
                        if current_price <= stop_loss_price:
                            # Trigger stop loss
                            volume = float(position["volume"])
                            margin_used = float(position["margin_used"])
                            leverage = int(position["leverage"])
                            stop_loss_pct = float(position.get("stop_loss_pct", 2.5))
                            
                            profit = (current_price - entry) * volume
                            account["balance"] += margin_used + profit
                            
                            account["trade_log"].append({
                                "timestamp": pretty_now(),
                                "action": "sell",
                                "symbol": symbol,
                                "reason": f"Stop Loss ({stop_loss_pct}%)",
                                "price": current_price,
                                "amount": volume,
                                "profit": round(profit, 2),
                                "balance": round(account["balance"], 2),
                                "leverage": leverage
                            })
                            positions_modified = True
                            logger.info(f"Stop loss triggered for {symbol} at {current_price}")
                        else:
                            new_positions.append(position)
                    
                    account["positions"][symbol] = new_positions
                
                if positions_modified:
                    save_account(bot_id, account)
                    
        except Exception as e:
            logger.error(f"Error in stop loss checker: {str(e)}", exc_info=True)
        
        time.sleep(2)  # Check every 2 seconds

# --- Dashboard and Settings Routes (remain unchanged from your original) ---
# ... [Include all your existing dashboard and settings routes here] ...

if __name__ == '__main__':
    # Start stop loss checker thread
    stop_loss_thread = threading.Thread(target=check_and_trigger_stop_losses, daemon=True)
    stop_loss_thread.start()
    
    logger.info("Starting Flask server on port 5000")
    app.run(host='0.0.0.0', port=5000, threaded=True)
