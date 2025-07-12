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
app.secret_key = "supersecretkey"

STARTING_BALANCE = 1000.00
file_lock = threading.Lock()

BOTS = {
    "1.0": {
        "name": "Coinbot 1.0",
        "color": "#06D1BF",
        "data_file": "account_1.json"
    },
    "2.0": {
        "name": "Coinbot 2.0",
        "color": "#FACB39",
        "data_file": "account_2.json"
    },
    "3.0": {
        "name": "Coinbot 3.0",
        "color": "#FF4B57",
        "data_file": "account_3.json"
    }
}

def save_account(bot_id, account):
    data_file = BOTS[bot_id]["data_file"]
    with file_lock:
        with open(data_file, "w") as f:
            json.dump(account, f, indent=2, default=str)
    logger.info(f"Account data saved for bot {bot_id}")

def load_account(bot_id):
    data_file = BOTS[bot_id]["data_file"]
    if os.path.exists(data_file):
        with file_lock:
            with open(data_file, "r") as f:
                loaded = json.load(f)
                loaded["balance"] = float(loaded.get("balance", STARTING_BALANCE))
                loaded["positions"] = loaded.get("positions", {})
                loaded["trade_log"] = loaded.get("trade_log", [])
                logger.info(f"Account data loaded from file for bot {bot_id}")
                return loaded
    logger.info(f"No account file found for bot {bot_id}, creating new account")
    return {
        "balance": STARTING_BALANCE,
        "positions": {},
        "trade_log": []
    }

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

@app.route('/')
def dashboard():
    active = request.args.get("active", "1.0")
    if active not in BOTS:
        active = "1.0"

    dashboards = {}
    for bot_id, bot_cfg in BOTS.items():
        account = load_account(bot_id)
        symbols = list(account.get("positions", {}).keys())
        prices = fetch_latest_prices(symbols)
        available_cash = account.get("balance", 0)
        equity = available_cash
        for symbol, positions in account.get("positions", {}).items():
            current_price = prices.get(symbol, 0)
            for p in positions:
                entry = p.get("entry_price", 0)
                volume = p.get("volume", 0)
                leverage = p.get("leverage", 1)
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

        total_pl = 0
        for symbol, positions in account.get("positions", {}).items():
            current_price = prices.get(symbol, 0)
            for p in positions:
                entry = p.get("entry_price", 0)
                volume = p.get("volume", 0)
                leverage = p.get("leverage", 1)
                if current_price and entry:
                    total_pl += (current_price - entry) * volume * leverage

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

        positions_html = ""
        for symbol, positions in account.get("positions", {}).items():
            current_price = prices.get(symbol, 0)
            for p in positions:
                entry = p.get("entry_price", 0)
                volume = p.get("volume", 0)
                leverage = p.get("leverage", 1)
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
                    continue
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

        dashboards[bot_id] = dict(
            bot=bot_cfg,
            account=account,
            equity=equity,
            available_cash=available_cash,
            total_pl=total_pl,
            coin_stats_html=coin_stats_html,
            positions_html=positions_html,
            trade_log_html=trade_log_html
        )

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
            <svg class="header-logo" viewBox="0 0 50 50" fill="none">
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
                        {{ bot_data['positions_html']|safe }}
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
                        {{ bot_data['trade_log_html']|safe }}
                        </tbody>
                    </table>
                    <h5 class="mt-4 mb-2">Coin P/L Summary</h5>
                    <table class="table table-sm">
                        <tr><th>Coin</th><th>Total P/L</th></tr>
                        {{ bot_data['coin_stats_html']|safe }}
                    </table>
                </div>
            </div>
            {% endfor %}
        </div>
        <div class="footer">
            Updated: {{now}}
        </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    '''
    return render_template_string(html, dashboards=dashboards, active=active, now=pretty_now())

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    bot_raw = str(data.get("bot", "")).strip().lower()
    if bot_raw.startswith("coinbot"):
        bot_id = bot_raw.replace("coinbot", "").replace(" ", "")
    else:
        bot_id = bot_raw
    if bot_id not in BOTS:
        logger.warning(f"Unknown bot_id in webhook: {bot_id}")
        return jsonify({"status": "error", "message": "Unknown bot"}), 400

    account = load_account(bot_id)
    try:
        action = data.get("action", "").lower()
        symbol = data.get("symbol", "").upper()
        if not action or action not in ["buy", "sell"]:
            return jsonify({"status": "error", "message": "Invalid action"}), 400
        if not symbol:
            return jsonify({"status": "error", "message": "Missing symbol"}), 400
        try:
            price = float(data.get("price", 0))
            if price <= 0:
                raise ValueError("Invalid price")
        except:
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
                    save_account(bot_id, account)
                    return jsonify({
                        "status": "success",
                        "action": "buy",
                        "symbol": symbol,
                        "price": price,
                        "volume": volume
                    }), 200
                else:
                    return jsonify({
                        "status": "error",
                        "message": "Insufficient balance"
                    }), 400
            else:
                return jsonify({
                    "status": "error",
                    "message": "Position limit reached or invalid volume"
                }), 400

        elif action == "sell":
            poslist = account["positions"].get(symbol, [])
            if poslist:
                total_volume = sum(p["volume"] for p in poslist)
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
                save_account(bot_id, account)
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
