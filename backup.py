from flask import Flask, request, render_template, jsonify
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import threading
import logging
from typing import Dict, List, Any

# --- Constants ---
STARTING_BALANCE = 1000.00
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

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
app.config['TEMPLATES_AUTO_RELOAD'] = True

# --- Configuration ---
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

# --- Thread Safety ---
file_lock = threading.Lock()

# --- Helper Functions ---
def pretty_now() -> str:
    return datetime.now(ZoneInfo("America/Edmonton")).strftime('%Y-%m-%d %H:%M:%S %Z')

def create_new_account() -> Dict[str, Any]:
    return {
        "balance": STARTING_BALANCE,
        "positions": {},
        "trade_log": []
    }

def load_account(bot_id: str) -> Dict[str, Any]:
    """Safely load account data with proper error handling"""
    if bot_id not in BOTS:
        raise ValueError(f"Invalid bot_id: {bot_id}")

    data_file = BOTS[bot_id]["data_file"]
    
    if not os.path.exists(data_file):
        logger.info(f"Creating new account for bot {bot_id}")
        return create_new_account()

    try:
        with file_lock:
            with open(data_file, 'r') as f:
                account = json.load(f)
                
        # Ensure data structure integrity
        account["balance"] = float(account.get("balance", STARTING_BALANCE))
        account["positions"] = account.get("positions", {})
        account["trade_log"] = account.get("trade_log", [])
        
        logger.info(f"Successfully loaded account for bot {bot_id}")
        return account
        
    except (json.JSONDecodeError, IOError, ValueError) as e:
        logger.error(f"Error loading account {bot_id}: {str(e)}")
        return create_new_account()

def save_account(bot_id: str, account: Dict[str, Any]) -> None:
    """Safely save account data with proper error handling"""
    if bot_id not in BOTS:
        raise ValueError(f"Invalid bot_id: {bot_id}")

    try:
        with file_lock:
            with open(BOTS[bot_id]["data_file"], 'w') as f:
                json.dump(account, f, indent=2, default=str)
        logger.info(f"Successfully saved account for bot {bot_id}")
    except IOError as e:
        logger.error(f"Error saving account {bot_id}: {str(e)}")
        raise

def fetch_latest_prices(symbols: List[str]) -> Dict[str, float]:
    """Fetch current prices from CoinGecko API"""
    symbol_map = {
        'BTCUSDT': 'bitcoin',
        'ETHUSDT': 'ethereum',
        'SOLUSDT': 'solana',
        'DOGEUSDT': 'dogecoin',
        'AVAXUSDT': 'avalanche-2',
        'MATICUSDT': 'matic-network',
        'ADAUSDT': 'cardano',
        'LTCUSDT': 'litecoin',
        'DOTUSDT': 'polkadot',
        'PEPEUSDT': 'pepe',
    }
    
    cg_ids = [symbol_map[sym] for sym in symbols if sym in symbol_map]
    if not cg_ids:
        return {}

    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(cg_ids)}&vs_currencies=usd"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        prices = {}
        data = response.json()
        for sym, cg_id in symbol_map.items():
            if cg_id in data and "usd" in data[cg_id]:
                prices[sym] = float(data[cg_id]["usd"])
        
        logger.info(f"Fetched prices for {len(prices)} symbols")
        return prices
        
    except Exception as e:
        logger.error(f"Price fetch failed: {str(e)}")
        return {}

def calculate_position_stats(positions: Dict[str, List[Dict]], prices: Dict[str, float]) -> Dict[str, Any]:
    """Calculate position statistics and HTML"""
    positions_html = []
    for symbol, position_list in positions.items():
        current_price = prices.get(symbol, 0)
        for position in position_list:
            entry = position.get("entry_price", 0)
            volume = position.get("volume", 0)
            leverage = position.get("leverage", 1)
            
            # Calculate margin used if not present
            margin_used = position.get("margin_used")
            if margin_used is None:
                margin_used = (entry * volume) / leverage if entry and volume and leverage else 0
            
            if entry and current_price:
                pnl = (current_price - entry) * volume * leverage
                pl_class = "profit" if pnl > 0 else "loss" if pnl < 0 else ""
                
                positions_html.append({
                    'symbol': symbol,
                    'volume': volume,
                    'entry_price': entry,
                    'current_price': current_price,
                    'leverage': leverage,
                    'margin_used': margin_used,
                    'position_size': margin_used * leverage,
                    'pnl': pnl,
                    'pl_class': pl_class
                })

    return positions_html

def calculate_coin_stats(trade_log: List[Dict]) -> Dict[str, float]:
    """Calculate profit/loss by coin"""
    coin_stats = {}
    for log in trade_log:
        if 'symbol' in log and 'profit' in log and log['profit'] is not None:
            coin_stats[log['symbol']] = coin_stats.get(log['symbol'], 0) + log['profit']
    return coin_stats

# --- Routes ---
@app.route('/')
def dashboard():
    active_bot = request.args.get("active", "1.0")
    if active_bot not in BOTS:
        active_bot = "1.0"

    dashboards = {}
    for bot_id, bot_cfg in BOTS.items():
        account = load_account(bot_id)
        
        # Get symbols from positions
        symbols = list(account["positions"].keys())
        prices = fetch_latest_prices(symbols)
        
        # Calculate equity and P/L
        equity = account["balance"]
        total_pl = 0
        
        # Process positions
        positions_html = calculate_position_stats(account["positions"], prices)
        
        # Calculate equity including positions
        for pos in positions_html:
            equity += pos['margin_used'] + pos['pnl']
            total_pl += pos['pnl']
        
        # Process trade log
        trade_log_html = []
        for log in reversed(account["trade_log"]):
            profit = log.get('profit')
            pl_class = "profit" if profit and profit > 0 else "loss" if profit and profit < 0 else ""
            
            trade_log_html.append({
                'timestamp': log.get('timestamp'),
                'action': log.get('action'),
                'symbol': log.get('symbol'),
                'reason': log.get('reason'),
                'price': log.get('price', 0),
                'amount': log.get('amount', 0),
                'profit': profit,
                'pl_pct': log.get('pl_pct'),
                'balance': log.get('balance', 0),
                'leverage': log.get('leverage', ''),
                'avg_entry': log.get('avg_entry', ''),
                'pl_class': pl_class
            })
        
        # Calculate coin stats
        coin_stats = calculate_coin_stats(account["trade_log"])
        coin_stats_html = [
            {'coin': coin, 'pl': pl, 'pl_class': "profit" if pl > 0 else "loss" if pl < 0 else ""}
            for coin, pl in sorted(coin_stats.items())
        ]
        
        dashboards[bot_id] = {
            'bot': bot_cfg,
            'account': account,
            'equity': equity,
            'available_cash': account["balance"],
            'total_pl': total_pl,
            'positions_html': positions_html,
            'trade_log_html': trade_log_html,
            'coin_stats_html': coin_stats_html,
            'prices': prices
        }

    return render_template('dashboard.html',
                         dashboards=dashboards,
                         active=active_bot,
                         now=pretty_now())

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
            return jsonify({"status": "error", "message": "Unknown bot"}), 400

        # Validate request
        action = data.get("action", "").lower()
        if action not in ["buy", "sell"]:
            return jsonify({"status": "error", "message": "Invalid action"}), 400

        symbol = data.get("symbol", "").upper()
        if not symbol:
            return jsonify({"status": "error", "message": "Missing symbol"}), 400

        try:
            price = float(data.get("price", 0))
            if price <= 0:
                raise ValueError("Invalid price")
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "Invalid price"}), 400

        # Process trade
        account = load_account(bot_id)
        reason = data.get("reason", "TradingView signal")
        timestamp = pretty_now()
        leverage = 5  # Could be configurable per bot

        if action == "buy":
            margin_pct = 0.05  # 5% of balance per trade
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
            total_volume = sum(p["volume"] for p in positions)
            total_margin = sum(
                p.get("margin_used", (p.get("entry_price", 0) * p.get("volume", 0) / p.get("leverage", 1))
                for p in positions
            )
            avg_entry = sum(p["entry_price"] * p["volume"] for p in positions) / total_volume
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

if __name__ == '__main__':
    logger.info("Starting Flask server on port 5000")
    app.run(host='0.0.0.0', port=5000, threaded=True)
