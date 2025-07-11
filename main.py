from flask import Flask, request, render_template_string, jsonify
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import threading
import logging

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
DATA_FILE = "account.json"
STARTING_BALANCE = 1000.00
file_lock = threading.Lock()

# --- Account Data Management ---
def save_account():
    with file_lock:
        with open(DATA_FILE, "w") as f:
            json.dump(account, f, indent=2, default=str)
    logger.info("Account data saved")

def load_account():
    if os.path.exists(DATA_FILE):
        with file_lock:
            with open(DATA_FILE, "r") as f:
                loaded = json.load(f)
                loaded["balance"] = float(loaded.get("balance", STARTING_BALANCE))
                loaded["positions"] = loaded.get("positions", {})
                loaded["trade_log"] = loaded.get("trade_log", [])
                logger.info("Account data loaded from file")
                return loaded
    logger.info("No account file found, creating new account")
    return {
        "balance": STARTING_BALANCE,
        "positions": {},
        "trade_log": []
    }

account = load_account()

# --- Utility Functions ---
def pretty_now():
    return datetime.now(
        ZoneInfo("America/Edmonton")).strftime('%Y-%m-%d %H:%M:%S %Z')

def fetch_latest_prices(symbols):
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
    cg_ids = ','.join(symbol_map[sym] for sym in symbols if sym in symbol_map)
    prices = {}
    if not cg_ids:
        return prices
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_ids}&vs_currencies=usd"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        for sym, cg_id in symbol_map.items():
            if cg_id in data and "usd" in data[cg_id]:
                prices[sym] = float(data[cg_id]["usd"])
        logger.info(f"Fetched prices for {len(prices)} symbols")
    except Exception as e:
        logger.error(f"Price fetch failed: {str(e)}")
    return prices

# --- Dashboard Webpage ---
@app.route('/', methods=['GET'])
def dashboard():
    symbols = list(account.get("positions", {}).keys())
    prices = fetch_latest_prices(symbols)
    html = """
    <html>
    <head>
        <title>Trading Bot Dashboard</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 30px; background: #f7f7fa; }
            h1 { color: #2c3e50; }
            table { border-collapse: collapse; width: 100%; margin-bottom: 30px; }
            th, td { border: 1px solid #ddd; padding: 8px; }
            th { background: #222; color: #fff; }
            tr:nth-child(even){background-color: #f2f2f2;}
            .profit { color: green; }
            .loss { color: red; }
        </style>
    </head>
    <body>
        <h1>Trading Bot Dashboard</h1>
        <h2>Account Balance: <span>${{ balance|round(2) }}</span></h2>
        <h2>Open Positions</h2>
        <table>
            <tr>
                <th>Symbol</th>
                <th>Volume</th>
                <th>Entry Price</th>
                <th>Current Price</th>
                <th>Leverage</th>
                <th>USD Spent</th>
                <th>P/L</th>
            </tr>
            {{ positions_html|safe }}
        </table>
        <h2>Trade Log</h2>
        <table>
            <tr>
                <th>Time</th>
                <th>Action</th>
                <th>Symbol</th>
                <th>Reason</th>
                <th>Price</th>
                <th>Amount</th>
                <th>Profit</th>
                <th>P/L %</th>
                <th>Balance</th>
            </tr>
            {{ trade_log_html|safe }}
        </table>
        <p style="font-size:0.8em; color:#888;">Updated: {{ now }}</p>
    </body>
    </html>
    """

    # Build positions table rows
    positions_html = ""
    for symbol, positions in account.get("positions", {}).items():
        for p in positions:
            current_price = prices.get(symbol, 0)
            entry = p.get("entry_price", 0)
            volume = p.get("volume", 0)
            usd_spent = p.get("usd_spent", 0)
            leverage = p.get("leverage", 1)
            pl = (current_price - entry) * volume if current_price and entry else 0
            pl_class = "profit" if pl > 0 else "loss" if pl < 0 else ""
            positions_html += f"<tr><td>{symbol}</td><td>{volume:.6f}</td><td>${entry:.2f}</td><td>${current_price:.2f}</td><td>{leverage}x</td><td>${usd_spent:.2f}</td><td class='{pl_class}'>{pl:+.2f}</td></tr>"

    # Build trade log table rows (show last 25)
    trade_log_html = ""
    for log in reversed(account.get("trade_log", [])[-25:]):
        profit = log.get("profit")
        pl_pct = log.get("pl_pct")
        profit_class = "profit" if profit is not None and profit > 0 else "loss" if profit is not None and profit < 0 else ""
        trade_log_html += f"<tr><td>{log.get('timestamp')}</td><td>{log.get('action')}</td><td>{log.get('symbol')}</td><td>{log.get('reason')}</td><td>${log.get('price', 0):.2f}</td><td>{log.get('amount', 0):.6f}</td><td class='{profit_class}'>{(profit if profit is not None else '')}</td><td class='{profit_class}'>{(pl_pct if pl_pct is not None else '')}</td><td>${log.get('balance', 0):.2f}</td></tr>"

    return render_template_string(
        html,
        balance=account.get("balance", 0),
        positions_html=positions_html or "<tr><td colspan='7'>No open positions</td></tr>",
        trade_log_html=trade_log_html or "<tr><td colspan='9'>No trades yet</td></tr>",
        now=pretty_now()
    )

# --- Health Check Endpoint ---
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "time": pretty_now()}), 200

# --- Webhook Endpoint for TradingView ---
@app.route('/webhook', methods=['POST'])
def webhook():
    global account

    client_ip = request.remote_addr
    logger.info(f"Webhook received from {client_ip}")

    try:
        if not request.data:
            logger.warning("Empty webhook payload")
            return jsonify({"status": "error", "message": "Empty payload"}), 400

        data = request.get_json()
        if not data:
            logger.warning("Invalid JSON payload")
            return jsonify({"status": "error", "message": "Invalid JSON"}), 400

        action = data.get("action", "").lower()
        symbol = data.get("symbol", "").upper()

        if not action or action not in ["buy", "sell"]:
            logger.warning(f"Invalid action: {action}")
            return jsonify({"status": "error", "message": "Invalid action"}), 400

        if not symbol:
            logger.warning("Missing symbol")
            return jsonify({"status": "error", "message": "Missing symbol"}), 400

        try:
            price = float(data.get("price", 0))
            if price <= 0:
                raise ValueError("Invalid price")
        except:
            logger.warning(f"Invalid price: {data.get('price')}")
            return jsonify({"status": "error", "message": "Invalid price"}), 400

        reason = data.get("reason", "TradingView signal")
        timestamp = pretty_now()
        leverage = 5
        margin_pct = 0.05

        if action == "buy":
            margin_cash = account["balance"] * margin_pct
            position_size = margin_cash * leverage
            volume = round(position_size / price, 6) if price > 0 else 0

            poslist = account["positions"].get(symbol, [])
            if len(poslist) < 5 and volume > 0:
                if account["balance"] >= margin_cash:
                    new_position = {
                        "volume": volume,
                        "entry_price": price,
                        "timestamp": timestamp,
                        "usd_spent": margin_cash,
                        "leverage": leverage,
                    }
                    poslist.append(new_position)
                    account["positions"][symbol] = poslist
                    account["balance"] -= margin_cash
                    account["trade_log"].append({
                        "timestamp": timestamp,
                        "action": "buy",
                        "symbol": symbol,
                        "reason": reason,
                        "price": price,
                        "amount": volume,
                        "profit": None,
                        "pl_pct": None,
                        "balance": round(account["balance"], 2),
                        "leverage": leverage,
                    })
                    save_account()
                    logger.info(f"BUY: {volume} {symbol} @ ${price}")
                    return jsonify({
                        "status": "success",
                        "action": "buy",
                        "symbol": symbol,
                        "price": price,
                        "volume": volume
                    }), 200
                else:
                    logger.warning(f"Insufficient balance for {symbol} buy")
                    return jsonify({
                        "status": "error",
                        "message": "Insufficient balance"
                    }), 400
            else:
                logger.warning(f"Position limit reached for {symbol} or invalid volume")
                return jsonify({
                    "status": "error",
                    "message": "Position limit reached or invalid volume"
                }), 400

        elif action == "sell":
            poslist = account["positions"].get(symbol, [])
            if poslist:
                total_volume = sum(p["volume"] for p in poslist)
                total_margin = sum(p["usd_spent"] for p in poslist)
                avg_entry = sum(p["entry_price"] * p["volume"] for p in poslist) / total_volume
                profit = (price - avg_entry) * total_volume
                pl_pct = ((price - avg_entry) / avg_entry * leverage * 100) if avg_entry > 0 else 0

                account["balance"] += total_margin + profit
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
                    "avg_entry": avg_entry,
                })
                account["positions"][symbol] = []
                save_account()
                logger.info(f"SELL: {total_volume} {symbol} @ ${price} | Profit: ${profit:.2f}")
                return jsonify({
                    "status": "success",
                    "action": "sell",
                    "symbol": symbol,
                    "price": price,
                    "volume": total_volume,
                    "profit": profit,
                    "return_pct": pl_pct
                }), 200
            else:
                logger.warning(f"No positions to sell for {symbol}")
                return jsonify({
                    "status": "error",
                    "message": "No positions to sell"
                }), 400

    except Exception as e:
        logger.error(f"Webhook processing failed: {str(e)}")
        return jsonify({
            "status": "error",
            "message": "Internal server error"
        }), 500

# --- Run the Server ---
if __name__ == '__main__':
    logger.info("Starting Flask server on port 5000")
    app.run(host='0.0.0.0', port=5000, threaded=True)
