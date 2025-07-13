from flask import Flask, request, render_template_string, jsonify
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import threading
import logging

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
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key")
app.config['TEMPLATES_AUTO_RELOAD'] = True

# --- Bot Config ---
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

file_lock = threading.Lock()

def pretty_now():
    return datetime.now(ZoneInfo("America/Edmonton")).strftime('%Y-%m-%d %H:%M:%S %Z')

def create_new_account():
    return {
        "balance": STARTING_BALANCE,
        "positions": {},
        "trade_log": []
    }

def load_account(bot_id):
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

        account["balance"] = float(account.get("balance", STARTING_BALANCE))
        account["positions"] = account.get("positions", {})
        account["trade_log"] = account.get("trade_log", [])

        logger.info(f"Successfully loaded account for bot {bot_id}")
        return account
    except (json.JSONDecodeError, IOError, ValueError) as e:
        logger.error(f"Error loading account {bot_id}: {str(e)}")
        return create_new_account()

def save_account(bot_id, account):
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

def calculate_position_stats(positions, prices):
    positions_html = []
    for symbol, position_list in positions.items():
        current_price = prices.get(symbol, 0)
        for position in position_list:
            entry = position.get("entry_price", 0)
            volume = position.get("volume", 0)
            leverage = position.get("leverage", 1)

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

def calculate_coin_stats(trade_log):
    coin_stats = {}
    for log in trade_log:
        if 'symbol' in log and 'profit' in log and log['profit'] is not None:
            coin_stats[log['symbol']] = coin_stats.get(log['symbol'], 0) + log['profit']
    return coin_stats

@app.route('/')
def dashboard():
    active_bot = request.args.get("active", "1.0")
    if active_bot not in BOTS:
        active_bot = "1.0"

    dashboards = {}
    for bot_id, bot_cfg in BOTS.items():
        account = load_account(bot_id)

        symbols = list(account["positions"].keys())
        prices = fetch_latest_prices(symbols)

        equity = account["balance"]
        total_pl = 0

        positions_html = calculate_position_stats(account["positions"], prices)

        for pos in positions_html:
            equity += pos['margin_used'] + pos['pnl']
            total_pl += pos['pnl']

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

    html = '''
<!DOCTYPE html>
<html>
<head>
    <title>CoinBot Dashboard</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <style>
        body {
            background: linear-gradient(120deg,#1a1d28 0%, #131520 100%);
            font-family: 'Segoe UI', 'Roboto', 'Montserrat', Arial, sans-serif;
            color: #e2e2e2;
        }
        .header-logo {
            height: 44px;
            margin-right: 18px;
            vertical-align: middle;
        }
        .header-title {
            font-size: 2.4em;
            font-weight: bold;
            letter-spacing: 2px;
            color: #FFF;
            display: inline-block;
            vertical-align: middle;
            text-shadow: 0 3px 15px #000A, 0 1px 0 #e5b500a0;
        }
        .nav-tabs .nav-link {
            font-size: 1.2em;
            font-weight: 600;
            background: #222431;
            border: none;
            color: #AAA;
            border-radius: 0;
            margin-right: 2px;
            transition: background 0.2s, color 0.2s;
        }
        .nav-tabs .nav-link.active, .nav-tabs .nav-link:hover {
            background: linear-gradient(90deg, #232f43 60%, #232d3a 100%);
            color: #ffe082 !important;
            border-bottom: 3px solid #ffe082;
        }
        .bot-panel {
            background: rgba(27,29,39,0.93);
            border-radius: 18px;
            padding: 24px 18px;
            margin-top: 28px;
            box-shadow: 0 6px 32px #0009, 0 1.5px 6px #0003;
            border: 1.5px solid #33395b88;
            position: relative;
        }
        .bot-panel h3 {
            font-weight: bold;
            font-size: 2em;
            letter-spacing: 1px;
        }
        .bot-panel h5, .bot-panel h6 {
            color: #ccc;
        }
        .profit { color: #18e198; font-weight: bold;}
        .loss { color: #fd4561; font-weight: bold;}
        table {
            background: rgba(19,21,32,0.92);
            border-radius: 13px;
            overflow: hidden;
            margin-bottom: 22px;
            box-shadow: 0 2px 16px #0003;
        }
        th, td {
            padding: 10px 7px;
            text-align: center;
            border-bottom: 1px solid #24273a;
        }
        th {
            background: #25273a;
            color: #ffe082;
            font-size: 1.04em;
        }
        tr:last-child td { border-bottom: none; }
        .table-sm th, .table-sm td { font-size: 0.98em; }
        .tab-content { margin-top: 0; }
        .footer {
            margin-top: 24px; font-size: 0.99em; color: #888;
            text-align: right;
        }
        @media (max-width: 1200px) {
            .container { max-width: 99vw;}
        }
    </style>
</head>
<body>
<div class="container mt-4">
    <div class="mb-4 d-flex align-items-center">
        <svg class="header-logo" viewBox="0 0 50 50" fill="none" xmlns="http://www.w3.org/2000/svg">
            <circle cx="25" cy="25" r="25" fill="#23252e"/>
            <g>
              <circle cx="25" cy="25" r="19.5" fill="#F7931A"/>
              <text x="15" y="33" font-size="22" font-family="Arial" font-weight="bold" fill="#fff">₿</text>
            </g>
        </svg>
        <span class="header-title">CoinBot Dashboard</span>
    </div>
    <ul class="nav nav-tabs" id="botTabs" role="tablist">
        {% for bot_id, bot_data in dashboards.items() %}
        <li class="nav-item" role="presentation">
            <a class="nav-link {% if active == bot_id %}active{% endif %} bot-tab"
               href="{{ url_for('dashboard', active=bot_id) }}"
               style="color: {{ bot_data['bot']['color'] }};"
               >{{ bot_data["bot"]["name"] }}</a>
        </li>
        {% endfor %}
    </ul>
    <div class="tab-content">
        {% for bot_id, bot_data in dashboards.items() %}
        <div class="tab-pane fade {% if active == bot_id %}show active{% endif %}" id="bot{{ bot_id }}">
            <div class="bot-panel" style="box-shadow: 0 2px 12px {{ bot_data['bot']['color'] }}33;">
                <h3 style="color: {{ bot_data['bot']['color'] }};">{{ bot_data['bot']["name"] }}</h3>
                <h5>Balance: <span style="color:{{ bot_data['bot']['color'] }};">${{ bot_data['account']['balance']|round(2) }}</span>
                    | Equity: <span style="color:{{ bot_data['bot']['color'] }};">${{ bot_data['equity']|round(2) }}</span>
                </h5>
                <h6>Total P/L: <span class="{% if bot_data['total_pl'] > 0 %}profit{% elif bot_data['total_pl'] < 0 %}loss{% endif %}">${{ '{0:.2f}'.format(bot_data['total_pl']) }}</span></h6>
                <h5 class="mt-4 mb-2">Open Positions</h5>
                <table class="table table-sm table-striped">
                    <thead>
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
                    </thead>
                    <tbody>
                    {% for pos in bot_data['positions_html'] %}
                    <tr>
                        <td>{{ pos.symbol }}</td>
                        <td>{{ "%.6f"|format(pos.volume) }}</td>
                        <td>${{ "%.2f"|format(pos.entry_price) }}</td>
                        <td>${{ "%.2f"|format(pos.current_price) }}</td>
                        <td>{{ pos.leverage }}x</td>
                        <td>${{ "%.2f"|format(pos.margin_used) }}</td>
                        <td>${{ "%.2f"|format(pos.position_size) }}</td>
                        <td class="{{ pos.pl_class }}">{{ "%.2f"|format(pos.pnl) }}</td>
                    </tr>
                    {% endfor %}
                    </tbody>
                </table>
                <h5 class="mt-4 mb-2">Trade Log</h5>
                <table class="table table-sm table-striped">
                    <thead>
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
                    </thead>
                    <tbody>
                    {% for log in bot_data['trade_log_html'] %}
                    <tr>
                        <td>{{ log.timestamp }}</td>
                        <td>{{ log.action }}</td>
                        <td>{{ log.symbol }}</td>
                        <td>{{ log.reason }}</td>
                        <td>${{ "%.2f"|format(log.price) }}</td>
                        <td>{{ "%.6f"|format(log.amount) }}</td>
                        <td class="{{ log.pl_class }}">{{ log.profit if log.profit is not none else "" }}</td>
                        <td class="{{ log.pl_class }}">{{ log.pl_pct if log.pl_pct is not none else "" }}</td>
                        <td>${{ "%.2f"|format(log.balance) }}</td>
                        <td>{{ log.leverage }}</td>
                        <td>{{ "%.2f"|format(log.avg_entry) if log.avg_entry != "" else "" }}</td>
                    </tr>
                    {% endfor %}
                    </tbody>
                </table>
                <h5 class="mt-4 mb-2">Coin P/L Summary</h5>
                <table class="table table-sm">
                    <thead><tr><th>Coin</th><th>Total P/L</th></tr></thead>
                    <tbody>
                    {% for coin in bot_data['coin_stats_html'] %}
                    <tr>
                        <td>{{ coin.coin }}</td>
                        <td class="{{ coin.pl_class }}">{{ "%.2f"|format(coin.pl) }}</td>
                    </tr>
                    {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
        {% endfor %}
    </div>
    <div class="footer">
        Updated: {{ now }}
    </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
'''

    return render_template_string(html, dashboards=dashboards, active=active_bot, now=pretty_now())


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

        bot_raw = str(data.get("bot", "")).strip().lower()
        if bot_raw.startswith("coinbot"):
            bot_id = bot_raw.replace("coinbot", "").replace(" ", "")
        else:
            bot_id = bot_raw

        if bot_id not in BOTS:
            return jsonify({"status": "error", "message": "Unknown bot"}), 400

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

        account = load_account(bot_id)
        reason = data.get("reason", "TradingView signal")
        timestamp = pretty_now()
        leverage = 5
        margin_pct = 0.05

        if action == "buy":
            margin_used = account["balance"] * margin_pct
            volume = round((margin_used * leverage) / price, 6) if price > 0 else 0

            if volume <= 0:
                return jsonify({"status": "error", "message": "Invalid volume"}), 400

            if account["balance"] < margin_used:
                return jsonify({"status": "error", "message": "Insufficient balance"}), 400

            if len(account["positions"].get(symbol, [])) >= 5:
                return jsonify({"status": "error", "message": "Position limit reached"}), 400

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
                "profit": None,
                "pl_pct": None,
                "avg_entry": None,
            })

        elif action == "sell":
            positions = account["positions"].get(symbol, [])
            if not positions:
                return jsonify({"status": "error", "message": "No positions to sell"}), 400

            total_volume = sum(p["volume"] for p in positions)
            total_margin = sum(
                p.get("margin_used", (p.get("entry_price", 0) * p.get("volume", 0) / p.get("leverage", 1)))
                for p in positions
            )
            avg_entry = sum(p["entry_price"] * p["volume"] for p in positions) / total_volume
            profit = (price - avg_entry) * total_volume * leverage
            pl_pct = ((price - avg_entry) / avg_entry * leverage * 100) if avg_entry > 0 else 0

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

