






from flask import Flask, request, render_template_string, jsonify
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
        account["balance"] = float(account.get("balance", STARTING_BALANCE))
        account["positions"] = account.get("positions", {})
        account["trade_log"] = account.get("trade_log", [])
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
    "PEPEUSDT": "PEPEUSDT",
}

latest_prices = {}
last_price_update = {'time': pretty_now(), 'prev_time': pretty_now()}

def fetch_latest_prices(symbols):
    # Always add BTCUSDT so its price is always fetched
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

def get_bitcoin_price():
    price = latest_prices.get("BTCUSDT")
    return f"${price:,.2f}" if price else "--"

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
    prev_update_time = last_price_update.get('prev_time', last_price_update['time'])

    # Always include BTCUSDT in price fetching
    all_symbols = set()
    for bot_id in BOTS:
        account = load_account(bot_id)
        all_symbols.update(account["positions"].keys())
    all_symbols.add("BTCUSDT")
    prices = fetch_latest_prices(list(all_symbols))

    for bot_id, bot_cfg in BOTS.items():
        account = load_account(bot_id)
        symbols = list(account["positions"].keys())
        # Use the prices already fetched above
        position_stats = calculate_position_stats(account["positions"], prices)
        total_margin = sum(pos['margin_used'] for pos in position_stats)
        total_pl = sum(pos['pnl'] for pos in position_stats)
        available_cash = float(account["balance"])
        equity = available_cash + total_margin + total_pl

        positions_html = ""
        for pos in position_stats:
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
            'available_cash': available_cash,
            'total_pl': total_pl,
            'coin_stats_html': coin_stats_html,
            'positions_html': positions_html,
            'trade_log_html': trade_log_html
        }

    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>CoinBot Dashboard</title>
        <!-- REMOVED: <meta http-equiv="refresh" content="10"> -->
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
                margin-right: 22px;
            }
            .btc-price {
                display: inline-flex;
                align-items: center;
                margin-left: 14px;
                vertical-align: middle;
            }
            .btc-logo {
                vertical-align: middle;
                margin-right: 4px;
                margin-top: -2px;
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
            <span class="btc-price">
                <svg class="btc-logo" viewBox="0 0 30 30" width="26" height="26">
                  <circle cx="15" cy="15" r="14" fill="#F7931A"/>
                  <text x="8" y="23" font-size="20" font-family="Arial" font-weight="bold" fill="#fff">₿</text>
                </svg>
                <span style="color:#F7931A; font-weight:bold; font-size:1.32em; letter-spacing:1px;">{{ btc_price }}</span>
            </span>
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
                    <h5>Balance: <span style="color:{{ bot_data['bot']['color'] }};">${{ bot_data['available_cash']|round(2) }}</span>
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
            Updated: {{now}}<br>
            CoinBotAutoUpdate: {{ coinbot_update_time }}
        </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    '''
    return render_template_string(
        html,
        dashboards=dashboards,
        active=active_bot,
        now=pretty_now(),
        coinbot_update_time=prev_update_time,
        btc_price=get_bitcoin_price()
    )

# --- WEBHOOK AND STOP LOSS LOGIC ---
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

# Get live price from Kraken
price = get_kraken_price(symbol)
if not price or price <= 0:
    # Try to fetch latest price for this symbol right now
    fetch_latest_prices([symbol])
    price = get_kraken_price(symbol)
    if not price or price <= 0:
        return jsonify({"status": "error", "message": f"No live Kraken price for {symbol}"}), 400


        account = load_account(bot_id)
        timestamp = pretty_now()
        leverage = 5  # Or set as desired
        margin_pct = 0.05  # 5% per trade
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
            profit = (price - avg_entry) * total_volume * leverage
            pl_pct = ((price - avg_entry) / avg_entry * leverage * 100) if avg_entry > 0 else 0

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
                for symbol, positions in account["positions"].items():
                    kraken_price = get_kraken_price(symbol)
                    if kraken_price == 0:
                        logger.warning(f"No price for {symbol}, skipping stop loss check")
                        continue
                    new_positions = []
                    for position in positions:
                        entry = float(position["entry_price"])
                        leverage = int(position["leverage"])
                        stop_loss_price = entry * (1 - (0.025 / leverage))  # 2.5% of margin used
                        if kraken_price > 0 and kraken_price <= stop_loss_price:
                            logger.info(f"Stop loss triggered for {symbol} at price {kraken_price:.6f} (entry: {entry:.6f}, stop: {stop_loss_price:.6f})")
                            reason = "Stop Loss"
                            timestamp = pretty_now()
                            volume = float(position["volume"])
                            margin_used = float(position["margin_used"])
                            profit = (kraken_price - entry) * volume * leverage
                            pl_pct = ((kraken_price - entry) / entry * leverage * 100) if entry > 0 else 0
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
                                "leverage": leverage,
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
        needed_symbols.add("BTCUSDT")  # Always keep BTC up-to-date for the dashboard
        fetch_latest_prices(list(needed_symbols))
        time.sleep(10)  # Check every 10 seconds

stop_loss_thread = threading.Thread(target=check_and_trigger_stop_losses, daemon=True)
stop_loss_thread.start()

if __name__ == '__main__':
    logger.info("Starting Flask server on port 5000")
    app.run(host='0.0.0.0', port=5000, threaded=True)
