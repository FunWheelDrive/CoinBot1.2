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

def pretty_now():
    return datetime.now(ZoneInfo("America/Edmonton")).strftime('%Y-%m-%d %H:%M:%S %Z')

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

@app.route('/', methods=['GET'])
def dashboard():
    global account
    account = load_account()
    symbols = list(account.get("positions", {}).keys())
    prices = fetch_latest_prices(symbols)

    # Calculate Available Cash (not in open positions)
    available_cash = account.get("balance", 0)

    # Calculate Equity (Available Cash + open positions at current price, including leveraged P&L)
    equity = available_cash
    for symbol, positions in account.get("positions", {}).items():
        current_price = prices.get(symbol, 0)
        for p in positions:
            entry = p.get("entry_price", 0)
            volume = p.get("volume", 0)
            leverage = p.get("leverage", 1)
            # Robust margin_used detection
            margin_used = p.get("margin_used")
            if margin_used is None:
                margin_used = p.get("usd_spent")
            if margin_used is None or margin_used == 0:
                if entry and volume and leverage:
                    margin_used = (entry * volume) / leverage
                else:
                    margin_used = 0
            if current_price and entry:
                pnl = (current_price - entry) * volume * leverage
                equity += margin_used + pnl

    # --- Calculate Total P/L (with leverage) ---
    total_pl = 0
    for symbol, positions in account.get("positions", {}).items():
        current_price = prices.get(symbol, 0)
        for p in positions:
            entry = p.get("entry_price", 0)
            volume = p.get("volume", 0)
            leverage = p.get("leverage", 1)
            if current_price and entry:
                total_pl += (current_price - entry) * volume * leverage

    # --- Per-coin net P/L from trade log ---
    coin_stats = {}
    for log in account.get("trade_log", []):
        sym = log.get("symbol")
        profit = log.get("profit")
        if sym is not None and profit is not None:
            coin_stats.setdefault(sym, 0)
            coin_stats[sym] += profit

    coin_stats_html = ""
    for coin, pl in sorted(coin_stats.items()):
        pl_class = "profit" if pl > 0 else "loss" if pl < 0 else ""
        coin_stats_html += (
            f"<tr><td>{coin}</td>"
            f"<td class='{pl_class}'>{pl:+.2f}</td></tr>"
        )
    if not coin_stats_html:
        coin_stats_html = "<tr><td colspan='2'>No trades yet</td></tr>"

    # --- Build positions table rows ---
    positions_html = ""
    for symbol, positions in account.get("positions", {}).items():
        current_price = prices.get(symbol, 0)
        for p in positions:
            entry = p.get("entry_price", 0)
            volume = p.get("volume", 0)
            leverage = p.get("leverage", 1)
            # Robust margin_used detection (for old and new positions)
            margin_used = p.get("margin_used")
            if margin_used is None:
                margin_used = p.get("usd_spent")
            if margin_used is None or margin_used == 0:
                if entry and volume and leverage:
                    margin_used = (entry * volume) / leverage
                else:
                    margin_used = 0
            position_size = margin_used * leverage
            if entry == 0 or current_price == 0:
                continue  # skip positions with missing price
            pl = (current_price - entry) * volume * leverage
            pl_class = "profit" if pl > 0 else "loss" if pl < 0 else ""
            positions_html += (
                f"<tr><td>{symbol}</td>"
                f"<td>{volume:.6f}</td>"
                f"<td>${entry:.2f}</td>"
                f"<td>${current_price:.2f}</td>"
                f"<td>{leverage}x</td>"
                f"<td>${margin_used:.2f}</td>"
                f"<td>${position_size:.2f}</td>"
                f"<td class='{pl_class}'>{pl:+.2f}</td></tr>"
            )
    if not positions_html:
        positions_html = "<tr><td colspan='8'>No open positions</td></tr>"

    # --- Trade log (all trades, profit with leverage) ---
    trade_log_html = ""
    for log in reversed(account.get("trade_log", [])):
        profit = log.get("profit")
        pl_pct = log.get("pl_pct")
        profit_class = "profit" if profit is not None and profit > 0 else "loss" if profit is not None and profit < 0 else ""
        leverage = log.get("leverage", "")
        avg_entry = log.get("avg_entry", "")
        trade_log_html += (
            f"<tr><td>{log.get('timestamp')}</td>"
            f"<td>{log.get('action')}</td>"
            f"<td>{log.get('symbol')}</td>"
            f"<td>{log.get('reason')}</td>"
            f"<td>${log.get('price', 0):.2f}</td>"
            f"<td>{log.get('amount', 0):.6f}</td>"
            f"<td class='{profit_class}'>{(profit if profit is not None else '')}</td>"
            f"<td class='{profit_class}'>{(pl_pct if pl_pct is not None else '')}</td>"
            f"<td>${log.get('balance', 0):.2f}</td>"
            f"<td>{leverage}</td>"
            f"<td>{avg_entry:.2f}" if avg_entry not in ("", None) else "<td></td>"
            f"</tr>"
        )
    if not trade_log_html:
        trade_log_html = "<tr><td colspan='11'>No trades yet</td></tr>"

    # --- Dashboard HTML with Sidebar ---
    html = """
    <html>
    <head>
        <title>CoinBot Dashboard</title>
        <link href="https://fonts.googleapis.com/css?family=Montserrat:700,400&display=swap" rel="stylesheet">
        <style>
            body {
                font-family: 'Montserrat', Arial, sans-serif;
                background: linear-gradient(135deg, #23253a 0%, #1c1e2e 100%);
                color: #ffe082;
                margin: 0; padding: 0;
            }
            .container-flex {
                display: flex;
                justify-content: center;
                align-items: flex-start;
                gap: 36px;
                margin: 40px auto;
                max-width: 1500px;
                width: 100%;
            }
            .dashboard-card {
                background: #232733;
                border-radius: 18px;
                box-shadow: 0 8px 32px rgba(0,0,0,0.24);
                padding: 28px 18px 22px 18px;
                width: 950px;
                border: 2px solid #ffe08244;
            }
            .sidebar-card {
                background: #191a25;
                border-radius: 14px;
                padding: 22px 12px 18px 12px;
                min-width: 210px;
                border: 2px solid #ffe08233;
                margin-top: 0;
                height: fit-content;
            }
            h1 {
                color: #ffe082;
                font-size: 2.2em;
                margin-bottom: 10px;
                text-shadow: 1px 2px 10px #0008;
                text-align: center;
            }
            h2 {
                color: #ffe082;
                font-size: 1.2em;
                margin-top: 26px;
                margin-bottom: 10px;
                letter-spacing: 1px;
            }
            .total-pl {
                font-size: 1.6em;
                font-weight: 700;
                margin: 18px 0 16px 0;
                display: inline-block;
                border-radius: 16px;
                padding: 10px 28px;
                background: #15171f;
                border: 2px solid #ffe08260;
                box-shadow: 0 3px 12px #0003;
            }
            .profit { color: #2fdc8b; font-weight: bold;}
            .loss { color: #ff4b57; font-weight: bold;}
            table {
                border-collapse: separate;
                border-spacing: 0;
                width: 100%;
                margin-bottom: 28px;
                background: #232733;
                border-radius: 15px;
                overflow: hidden;
                box-shadow: 0 2px 10px #0003;
            }
            th, td {
                padding: 10px 7px;
                text-align: center;
                border-bottom: 1px solid #35395b;
            }
            th {
                background: #22253c;
                color: #ffe082;
                font-size: 1.04em;
                letter-spacing: 1px;
            }
            tr:nth-child(even) { background: #232733; }
            tr:nth-child(odd) { background: #282b40; }
            tr:last-child td { border-bottom: none; }
            .sidebar-card h3 {
                color: #ffe082;
                font-size: 1.08em;
                margin-bottom: 10px;
                text-align: center;
                font-weight: 700;
            }
            .sidebar-table {
                background: #181a2c;
                width: 100%;
                border-radius: 9px;
                box-shadow: 0 2px 7px #0002;
                margin-top: 8px;
            }
            .sidebar-table th, .sidebar-table td {
                padding: 7px 6px;
                font-size: 1em;
                border-bottom: 1px solid #2b2d46;
            }
            .sidebar-table th {
                background: #191a25;
                color: #ffe082;
            }
            .sidebar-table tr:last-child td { border-bottom: none; }
            .footer { text-align: right; color: #aaa; font-size: 0.95em; margin-top: 30px; }
            @media (max-width: 1200px) {
                .container-flex { flex-direction: column; align-items: stretch; }
                .dashboard-card { width: 100vw; min-width: 0; }
                .sidebar-card { min-width: 0; margin-top: 16px;}
            }
        </style>
    </head>
    <body>
        <div class="container-flex">
            <div class="dashboard-card">
                <h1>CoinBot Dashboard</h1>
                <h2>
                    Account Balance (Equity): <span>${{ equity|round(2) }}</span><br>
                    Available Cash: <span>${{ available_cash|round(2) }}</span>
                </h2>
                <div class="total-pl {{ 'profit' if total_pl > 0 else 'loss' if total_pl < 0 else '' }}">
                    Total P/L: ${{ '{0:.2f}'.format(total_pl) }}
                </div>
                <h2>Open Positions</h2>
                <table>
                    <tr>
                        <th>Symbol</th>
                        <th>Volume</th>
                        <th>Entry Price</th>
                        <th>Current Price</th>
                        <th>Leverage</th>
                        <th>Margin Used</th>
                        <th>Position Size</th>
                        <th>Unrealized P/L</th>
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
                        <th>Leverage</th>
                        <th>Avg Entry</th>
                    </tr>
                    {{ trade_log_html|safe }}
                </table>
                <div class="footer">
                    Updated: {{ now }}
                </div>
            </div>
            <div class="sidebar-card">
                <h3>Coin P/L Summary</h3>
                <table class="sidebar-table">
                    <tr><th>Coin</th><th>Total P/L</th></tr>
                    {{ coin_stats_html|safe }}
                </table>
            </div>
        </div>
    </body>
    </html>
    """

    return render_template_string(
        html,
        balance=account.get("balance", 0),
        equity=equity,
        available_cash=available_cash,
        positions_html=positions_html,
        trade_log_html=trade_log_html,
        now=pretty_now(),
        total_pl=total_pl,
        coin_stats_html=coin_stats_html,
    )

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok", "time": pretty_now()}), 200

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
            available_cash = account["balance"]
            margin_used = available_cash * margin_pct
            position_size = margin_used * leverage
            volume = round(position_size / price, 6) if price > 0 else 0

            poslist = account["positions"].get(symbol, [])
            if len(poslist) < 5 and volume > 0:
                if account["balance"] >= margin_used:
                    new_position = {
                        "volume": volume,
                        "entry_price": price,
                        "timestamp": timestamp,
                        "margin_used": margin_used,
                        "leverage": leverage,
                    }
                    poslist.append(new_position)
                    account["positions"][symbol] = poslist
                    account["balance"] -= margin_used
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
                    logger.info(f"BUY: {volume} {symbol} @ ${price} | Margin used: ${margin_used:.2f} | Position size: ${position_size:.2f}")
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
                # Robust margin_used sum for old and new positions
                total_margin = 0
                for p in poslist:
                    mu = p.get("margin_used")
                    if mu is None:
                        mu = p.get("usd_spent")
                    if mu is None or mu == 0:
                        entry = p.get("entry_price", 0)
                        volume = p.get("volume", 0)
                        leverage = p.get("leverage", 1)
                        if entry and volume and leverage:
                            mu = (entry * volume) / leverage
                        else:
                            mu = 0
                    total_margin += mu
                avg_entry = sum(p["entry_price"] * p["volume"] for p in poslist) / total_volume
                leverage = poslist[0].get("leverage", 5)
                profit = (price - avg_entry) * total_volume * leverage
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

if __name__ == '__main__':
    logger.info("Starting Flask server on port 5000")
    app.run(host='0.0.0.0', port=5000, threaded=True)
