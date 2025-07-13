from flask import Flask, request, render_template_string, jsonify
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import threading
import time
import logging
import krakenex
from dotenv import load_dotenv

# --- Logging ---
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
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key")  # Change for production

STARTING_BALANCE = 1000.00
file_lock = threading.Lock()

# Ensure data directory exists
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

# --- Load Kraken API Keys ---
load_dotenv(dotenv_path=".env")
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET")
kraken_api = krakenex.API(key=KRAKEN_API_KEY, secret=KRAKEN_API_SECRET)

def create_new_account():
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

def load_account(bot_id):
    if bot_id not in BOTS:
        logger.warning(f"Invalid bot_id: {bot_id}")
        return create_new_account()

    data_file = BOTS[bot_id]["data_file"]
    
    if not os.path.exists(data_file):
        logger.info(f"Creating new account for bot {bot_id}")
        return create_new_account()

    try:
        with file_lock:
            with open(data_file, "r") as f:
                account = json.load(f)
        
        # Ensure all required fields exist with proper types
        account["balance"] = float(account.get("balance", STARTING_BALANCE))
        account["positions"] = account.get("positions", {})
        account["trade_log"] = account.get("trade_log", [])
        
        # Clean up positions data
        for symbol in account["positions"]:
            for position in account["positions"][symbol]:
                position["volume"] = float(position.get("volume", 0))
                position["entry_price"] = float(position.get("entry_price", 0))
                position["leverage"] = int(position.get("leverage", 1))
                position["margin_used"] = float(position.get("margin_used", 0))
                
        logger.info(f"Successfully loaded account for bot {bot_id}")
        return account
        
    except Exception as e:
        logger.error(f"Error loading account {bot_id}: {str(e)}")
        return create_new_account()

def pretty_now():
    return datetime.now(ZoneInfo("America/Edmonton")).strftime('%Y-%m-%d %H:%M:%S %Z')

# --- Kraken price fetch ---
def get_kraken_price(symbol):
    symbol_map = {
        "BTCUSDT": "XXBTZUSD",
        "ETHUSDT": "XETHZUSD",
        "SOLUSDT": "SOLUSD",
        "DOGEUSDT": "XDGUSD",
        "AVAXUSDT": "AVAXUSD",
        "MATICUSDT": "MATICUSD",
        "ADAUSDT": "ADAUSD",
        "LTCUSDT": "XLTCZUSD",
        "DOTUSDT": "DOTUSD",
        "PEPEUSDT": "PEPEUSD",
    }
    kraken_pair = symbol_map.get(symbol)
    if not kraken_pair:
        logger.error(f"No Kraken symbol mapping for {symbol}")
        return None
    try:
        resp = kraken_api.query_public('Ticker', {'pair': kraken_pair})
        price = float(resp['result'][kraken_pair]['c'][0])
        return price
    except Exception as e:
        logger.error(f"Failed to fetch price for {symbol} from Kraken: {str(e)}")
        return None

# --- Stop Loss Logic ---
def check_and_trigger_stop_losses(bot_id, account):
    updated = False
    for symbol, positions in list(account["positions"].items()):
        price = get_kraken_price(symbol)
        if not price:
            continue
        for position in list(positions):
            entry = float(position["entry_price"])
            stop_loss_price = entry * 0.975  # 2.5% below entry
            if price <= stop_loss_price:
                leverage = position.get("leverage", 5)
                volume = float(position["volume"])
                margin_used = float(position.get("margin_used", 0))
                profit = (price - entry) * volume * leverage
                pl_pct = ((price - entry) / entry * leverage * 100) if entry > 0 else 0
                timestamp = pretty_now()
                avg_entry = entry
                account["balance"] += margin_used + profit
                # Clear position
                account["positions"][symbol] = []
                # Log stop loss
                account["trade_log"].append({
                    "timestamp": timestamp,
                    "action": "sell",
                    "symbol": symbol,
                    "reason": "Stop Loss",
                    "price": price,
                    "amount": volume,
                    "profit": round(profit, 2),
                    "pl_pct": round(pl_pct, 2),
                    "balance": round(account["balance"], 2),
                    "leverage": leverage,
                    "avg_entry": round(avg_entry, 2),
                })
                logger.info(f"Stop loss triggered for {symbol} at {price}")
                updated = True
    if updated:
        save_account(bot_id, account)

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
            pnl = (current_price - entry) * volume * leverage if entry and current_price else 0
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

def calculate_coin_stats(trade_log):
    coin_stats = {}
    for log in trade_log:
        if 'symbol' in log and 'profit' in log and log['profit'] is not None:
            coin = log['symbol']
            profit = float(log['profit']) if log['profit'] is not None else 0
            coin_stats[coin] = coin_stats.get(coin, 0) + profit
    return coin_stats

@app.route('/')
def dashboard():
    active_bot = request.args.get("active", "1.0")
    if active_bot not in BOTS:
        active_bot = "1.0"

    dashboards = {}
    for bot_id, bot_cfg in BOTS.items():
        account = load_account(bot_id)
        # check_and_trigger_stop_losses is now handled by background thread!
        
        # Get symbols from positions
        symbols = list(account["positions"].keys())
        prices = {}
        for sym in symbols:
            kraken_price = get_kraken_price(sym)
            if kraken_price is not None:
                prices[sym] = kraken_price
        
        # Calculate equity and P/L
        equity = float(account["balance"])
        total_pl = 0
        
        # Process positions
        positions_html = ""
        position_stats = calculate_position_stats(account["positions"], prices)
        for pos in position_stats:
            equity += pos['margin_used'] + pos['pnl']
            total_pl += pos['pnl']
            
            positions_html += (
                f"<tr><td>{pos['symbol']}</td>"
                f"<td>{pos['volume']:.6f}</td>"
                f"<td>${pos['entry_price']:.2f}</td>"
                f"<td>${pos['current_price']:.2f}</td>"
                f"<td>{pos['leverage']}x</td>"
                f"<td>${pos['margin_used']:.2f}</td>"
                f"<td>${pos['position_size']:.2f}</td>"
                f"<td class='{pos['pl_class']}'>{pos['pnl']:+.2f}</td></tr>"
            )
        
        if not positions_html:
            positions_html = "<tr><td colspan='8'>No open positions</td></tr>"
        
        # Process trade log
        trade_log_html = ""
        for log in reversed(account["trade_log"]):
            profit = log.get('profit')
            pl_class = "profit" if profit and profit > 0 else "loss" if profit and profit < 0 else ""
            avg_entry_val = log.get('avg_entry')
            avg_entry_str = f"{float(avg_entry_val):.2f}" if avg_entry_val not in (None, '') else ''

            trade_log_html += (
                f"<tr><td>{log.get('timestamp', '')}</td>"
                f"<td>{log.get('action', '')}</td>"
                f"<td>{log.get('symbol', '')}</td>"
                f"<td>{log.get('reason', '')}</td>"
                f"<td>${float(log.get('price', 0)):.2f}</td>"
                f"<td>{float(log.get('amount', 0)):.6f}</td>"
                f"<td class='{pl_class}'>{f'{float(profit):+.2f}' if profit is not None else ''}</td>"
                f"<td class='{pl_class}'>{log.get('pl_pct', '')}</td>"
                f"<td>${float(log.get('balance', 0)):.2f}</td>"
                f"<td>{log.get('leverage', '')}</td>"
                f"<td>{avg_entry_str}</td>"
                f"</tr>"
            )
        
        if not trade_log_html:
            trade_log_html = "<tr><td colspan='11'>No trades yet</td></tr>"
        
        # Calculate coin stats
        coin_stats = calculate_coin_stats(account["trade_log"])
        coin_stats_html = ""
        for coin, pl in sorted(coin_stats.items()):
            pl_class = "profit" if pl > 0 else "loss" if pl < 0 else ""
            coin_stats_html += (
                f"<tr><td>{coin}</td>"
                f"<td class='{pl_class}'>{pl:+.2f}</td></tr>"
            )
        
        if not coin_stats_html:
            coin_stats_html = "<tr><td colspan='2'>No trades yet</td></tr>"
        
        dashboards[bot_id] = {
            'bot': bot_cfg,
            'account': account,
            'equity': equity,
            'available_cash': account["balance"],
            'total_pl': total_pl,
            'coin_stats_html': coin_stats_html,
            'positions_html': positions_html,
            'trade_log_html': trade_log_html
        }

    # -- KEEP YOUR EXISTING HTML TEMPLATE --
    html = ''' ... (keep your full dashboard HTML as is, unchanged) ... '''
    return render_template_string(html, dashboards=dashboards, active=active_bot, now=pretty_now())

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

        # Parse bot ID
        bot_raw = str(data.get("bot", "")).strip().lower()
        if bot_raw.startswith("coinbot"):
            bot_id = bot_raw.replace("coinbot", "").replace(" ", "")
        else:
            bot_id = bot_raw

        if bot_id not in BOTS:
            logger.warning(f"Unknown bot_id in webhook: {bot_id}")
            return jsonify({"status": "error", "message": "Unknown bot"}), 400

        # Validate request
        action = data.get("action", "").lower()
        if action not in ["buy", "sell"]:
            return jsonify({"status": "error", "message": "Invalid action"}), 400

        symbol = data.get("symbol", "").upper()
        if not symbol:
            return jsonify({"status": "error", "message": "Missing symbol"}), 400

        # --- USE LIVE KRAKEN PRICE! ---
        price = get_kraken_price(symbol)
        if not price or price <= 0:
            return jsonify({"status": "error", "message": "Could not fetch live price from Kraken"}), 400

        # Process trade
        account = load_account(bot_id)
        reason = data.get("reason", "TradingView signal")
        timestamp = pretty_now()
        leverage = 5  # Could be configurable per bot
        margin_pct = 0.05  # 5% of balance per trade

        if action == "buy":
            margin_used = account["balance"] * margin_pct
            volume = round((margin_used * leverage) / price, 6) if price > 0 else 0

            if volume <= 0:
                return jsonify({"status": "error", "message": "Invalid volume"}), 400

            if account["balance"] < margin_used:
                return jsonify({"status": "error", "message": "Insufficient balance"}), 400

            # Limit number of positions per symbol
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

        elif action == "sell":
            positions = account["positions"].get(symbol, [])
            if not positions:
                return jsonify({"status": "error", "message": "No positions to sell"}), 400

            # Calculate position metrics
            total_volume = sum(float(p["volume"]) for p in positions)
            total_margin = sum(
                float(p.get("margin_used", (float(p.get("entry_price", 0)) * float(p.get("volume", 0)) / float(p.get("leverage", 1)))))
                for p in positions
            )
            avg_entry = sum(float(p["entry_price"]) * float(p["volume"]) for p in positions) / total_volume if total_volume > 0 else 0
            profit = (price - avg_entry) * total_volume * leverage
            pl_pct = ((price - avg_entry) / avg_entry * leverage * 100) if avg_entry > 0 else 0

            # Update account
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
                "avg_entry": round(avg_entry, 2),
            })

        save_account(bot_id, account)
        return jsonify({
            "status": "success",
            "action": action,
            "symbol": symbol,
            "price": price,
            "balance": account["balance"]
        }), 200

    except Exception as e:
        logger.error(f"Webhook processing failed: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500

# --- Background stop loss checker thread ---
def stop_loss_background_worker(interval=60):  # 60 seconds (1 minute)
    while True:
        try:
            logger.info("Background stop loss checker running...")
            for bot_id in BOTS:
                account = load_account(bot_id)
                check_and_trigger_stop_losses(bot_id, account)
        except Exception as e:
            logger.error(f"Error in stop loss background worker: {e}", exc_info=True)
        time.sleep(interval)

if __name__ == '__main__':
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
        logger.info(f"Created data directory: {DATA_DIR}")
    
    # Start background stop loss checker (every 60 seconds)
    sl_thread = threading.Thread(target=stop_loss_background_worker, args=(60,), daemon=True)
    sl_thread.start()

    logger.info("Starting Flask server on port 5000")
    app.run(host='0.0.0.0', port=5000, threaded=True)





