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

# --- Formatting functions ---
def format_price(price):
    try:
        price = float(price)
        if price >= 1000:
            return f"${price:,.0f}"
        elif price >= 1:
            return f"${price:,.2f}"
        elif price >= 0.01:
            return f"${price:,.4f}"
        elif price > 0:
            return f"${price:,.8f}"
        else:
            return "$0.00"
    except Exception:
        return "--"

def format_volume(volume):
    try:
        volume = float(volume)
        if volume >= 1000:
            return f"{volume:,.0f}"
        elif volume >= 1:
            return f"{volume:,.2f}"
        elif volume >= 0.01:
            return f"{volume:,.4f}"
        elif volume > 0:
            return f"{volume:,.6f}"
        else:
            return "0.00"
    except Exception:
        return "--"

def format_profit(profit):
    if profit in [None, '', 'None', 'null', 'NaN']:
        return "0.00"
    try:
        profit_float = float(profit)
        if profit_float == 0:
            return "0.00"
        abs_profit = abs(profit_float)
        if abs_profit >= 1000:
            return f"{profit_float:+,.0f}"
        elif abs_profit >= 1:
            formatted = f"{profit_float:+,.2f}"
            return formatted.replace(".00", "")
        elif abs_profit >= 0.01:
            formatted = f"{profit_float:+,.4f}"
            return formatted.rstrip("0").rstrip(".")
        else:
            formatted = f"{profit_float:+,.6f}"
            return formatted.rstrip("0").rstrip(".")
    except Exception as e:
        logger.error(f"PROFIT FORMAT ERROR - Value: '{profit}' | Type: {type(profit)} | Error: {str(e)}")
        return "--"

app.jinja_env.filters['format_price'] = format_price
app.jinja_env.filters['format_volume'] = format_volume
app.jinja_env.filters['format_profit'] = format_profit

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
    try:
        return datetime.now(ZoneInfo("America/Edmonton")).strftime('%Y-%m-%d %H:%M:%S %Z')
    except:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

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

# --- Core Functions ---
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

        # Patch: set missing "type" to "long"
        for symbol in account["positions"]:
            for position in account["positions"][symbol]:
                if "type" not in position:
                    position["type"] = "long"

        # Now cast all fields to correct types
        for symbol in account["positions"]:
            for position in account["positions"][symbol]:
                position["volume"] = float(position.get("volume", 0))
                position["entry_price"] = float(position.get("entry_price", 0))
                position["leverage"] = int(position.get("leverage", 1))
                position["margin_used"] = float(position.get("margin_used", 0))
                position["stop_loss_pct"] = float(position.get("stop_loss_pct", 2.5))
                position["take_profit_pct"] = float(position.get("take_profit_pct", 3.0)) if "take_profit_pct" in position else 3.0
                position["stop_loss_price"] = float(position.get("stop_loss_price", 0)) if "stop_loss_price" in position else None
                position["take_profit_price"] = float(position.get("take_profit_price", 0)) if "take_profit_price" in position else None
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
    default_settings = {"leverage": 5, "stop_loss_pct": 2.5, "take_profit_pct": 3.0}
    if not os.path.exists(settings_file):
        return default_settings
    with open(settings_file, "r") as f:
        settings = json.load(f)
    # Ensure all keys exist
    for k, v in default_settings.items():
        if k not in settings:
            settings[k] = v
    return settings

def save_bot_settings(bot_id, settings):
    settings_file = os.path.join(DATA_DIR, f"settings_{bot_id}.json")
    with open(settings_file, "w") as f:
        json.dump(settings, f, indent=2)

# --- Calculation Functions ---
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
            take_profit_pct = float(position.get("take_profit_pct", 3.0))
            position_type = position.get("type", "long")

            if not margin_used and entry and volume and leverage:
                margin_used = (entry * volume) / leverage

            position_size = margin_used * leverage

            if position_type == "long":
                pnl = (current_price - entry) * volume
                stop_loss_price = entry * (1 - stop_loss_pct/100)
                take_profit_price = entry * (1 + take_profit_pct/100)
            else:  # short
                pnl = (entry - current_price) * volume
                stop_loss_price = entry * (1 + stop_loss_pct/100)
                take_profit_price = entry * (1 - take_profit_pct/100)

            pl_class = "profit" if pnl > 0 else "loss" if pnl < 0 else ""

            position_stats.append({
                'symbol': symbol,
                'type': position_type,
                'volume': volume,
                'entry_price': entry,
                'current_price': current_price,
                'leverage': leverage,
                'margin_used': margin_used,
                'position_size': position_size,
                'pnl': pnl,
                'pl_class': pl_class,
                'stop_loss_pct': stop_loss_pct,
                'stop_loss_price': stop_loss_price,
                'take_profit_pct': take_profit_pct,
                'take_profit_price': take_profit_price
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

def get_bitcoin_price():
    price = latest_prices.get("BTCUSDT")
    return format_price(price) if price else "--"

# --- Routes ---
@app.route('/')
def dashboard():
    active_bot = request.args.get("active", "1.0")
    if active_bot not in BOTS:
        active_bot = "1.0"
    dashboards = {}
    prev_update_time = last_price_update.get('prev_time', last_price_update['time'])
    all_symbols = set()
    for bot_id in BOTS:
        account = load_account(bot_id)
        all_symbols.update(account["positions"].keys())
    all_symbols.add("BTCUSDT")
    prices = fetch_latest_prices(list(all_symbols))

    for bot_id, bot_cfg in BOTS.items():
        account = load_account(bot_id)
        position_stats = calculate_position_stats(account["positions"], prices)
        total_margin = sum(pos['margin_used'] for pos in position_stats)
        total_pl = sum(pos['pnl'] for pos in position_stats)
        available_cash = float(account["balance"])
        equity = available_cash + total_margin + total_pl

        positions_html = ""
        for pos in position_stats:
            positions_html += (
                f"<tr><td>{pos['symbol']}</td>"
                f"<td>{format_volume(pos['volume'])}</td>"
                f"<td>{format_price(pos['entry_price'])}</td>"
                f"<td>{format_price(pos['current_price'])}</td>"
                f"<td>{pos['leverage']}x</td>"
                f"<td>{format_price(pos['margin_used'])}</td>"
                f"<td>{format_price(pos['position_size'])}</td>"
                f"<td class='{pos['pl_class']}'>{format_profit(pos['pnl'])}</td>"
                f"<td>{pos['stop_loss_pct']}%</td>"
                f"<td>{format_price(pos['stop_loss_price'])}</td>"
                f"<td>{pos['take_profit_pct']}%</td>"
                f"<td>{format_price(pos['take_profit_price'])}</td></tr>"
            )
        if not positions_html:
            positions_html = "<tr><td colspan='12'>No open positions</td></tr>"

        trade_log_html = ""
        for log in reversed(account["trade_log"]):
            profit = log.get('profit')
            pl_class = "profit" if profit and float(profit) > 0 else "loss" if profit and float(profit) < 0 else ""
            avg_entry_val = log.get('avg_entry')
            avg_entry_str = format_price(avg_entry_val) if avg_entry_val not in (None, '') else ''

            trade_log_html += (
                f"<tr><td>{log.get('timestamp', '')}</td>"
                f"<td>{log.get('action', '')}</td>"
                f"<td>{log.get('symbol', '')}</td>"
                f"<td>{log.get('reason', '')}</td>"
                f"<td>{format_price(log.get('price', 0))}</td>"
                f"<td>{format_volume(log.get('amount', 0))}</td>"
                f"<td class='{pl_class}'>{format_profit(profit) if profit is not None else ''}</td>"
                f"<td class='{pl_class}'>{log.get('pl_pct', '')}</td>"
                f"<td>{format_price(log.get('balance', 0))}</td>"
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
                f"<td class='{pl_class}'>{format_profit(pl)}</td></tr>"
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
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
        <style>
            body { background: linear-gradient(120deg,#1a1d28 0%, #131520 100%); font-family: 'Segoe UI', 'Roboto', 'Montserrat', Arial, sans-serif; color: #e2e2e2;}
            .header-logo-hover {
                transition: all 0.3s ease;
            }
            .header-logo-hover:hover {
                transform: scale(1.05);
                opacity: 0.9;
            }
            .btc-price { display: inline-flex; align-items: center; margin-left: 14px; vertical-align: middle;}
            .btc-logo { vertical-align: middle; margin-right: 4px; margin-top: -2px;}
            .nav-tabs .nav-link { font-size: 1.2em; font-weight: 600; background: #222431; border: none; color: #AAA; border-radius: 0; margin-right: 2px; transition: background 0.2s, color 0.2s;}
            .nav-tabs .nav-link.active, .nav-tabs .nav-link:hover { background: linear-gradient(90deg, #232f43 60%, #232d3a 100%); color: #ffe082 !important; border-bottom: 3px solid #ffe082;}
            .bot-panel { background: rgba(27,29,39,0.93); border-radius: 18px; padding: 24px 18px; margin-top: 28px; box-shadow: 0 6px 32px #0009, 0 1.5px 6px #0003; border: 1.5px solid #33395b88; position: relative;}
            .bot-panel h3 { font-weight: bold; font-size: 2em; letter-spacing: 1px;}
            .bot-panel h5, .bot-panel h6 { color: #ccc;}
            .profit { color: #18e198; font-weight: bold;}
            .loss { color: #fd4561; font-weight: bold;}
            table { background: rgba(19,21,32,0.92); border-radius: 13px; overflow: hidden; margin-bottom: 22px; box-shadow: 0 2px 16px #0003;}
            th, td { padding: 10px 7px; text-align: center; border-bottom: 1px solid #24273a;}
            th { background: #25273a; color: #ffe082; font-size: 1.04em;}
            tr:last-child td { border-bottom: none; }
            .table-sm th, .table-sm td { font-size: 0.98em; }
            .tab-content { margin-top: 0; }
            .footer { margin-top: 24px; font-size: 0.99em; color: #888; text-align: right;}
            @media (max-width: 1200px) { .container { max-width: 99vw;}}
        </style>
    </head>
    <body>
    <div class="container mt-4">
        <div class="mb-4 d-flex align-items-center">
            <img src="{{ url_for('static', filename='COINBO.png') }}" 
                 alt="COINBO Logo" 
                 style="height: 325px; margin-right: 500px;"
                 class="header-logo-hover">
            <span class="btc-price">
                <svg class="btc-logo" viewBox="0 0 30 30" width="26" height="26">
                  <circle cx="15" cy="15" r="14" fill="#F7931A"/>
                  <text x="8" y="23" font-size="20" font-family="Arial" font-weight="bold" fill="#fff">₿</text>
                </svg>
                <span style="color:#F7931A; font-weight:bold; font-size:1.32em; letter-spacing:1px;">{{ btc_price }}</span>
            </span>
            <span style="margin-left: 18px;">
                <a href="{{ url_for('settings', bot=active) }}" class="btn btn-sm btn-warning">Settings</a>
            </span>
            <span style="margin-left: 8px;">
                {% if session.get('settings_auth') %}
                    <a href="{{ url_for('settings_logout') }}" class="btn btn-sm btn-secondary">Logout</a>
                {% endif %}
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
                    <h3 style="color: {{ bot_data['bot']['color'] }};">{{ bot_data["bot"]["name"] }}</h3>
                    <h5>Balance: <span style="color:{{ bot_data['bot']['color'] }};">{{ format_price(bot_data['available_cash']) }}</span>
                        | Equity: <span style="color:{{ bot_data['bot']['color'] }};">{{ format_price(bot_data['equity']) }}</span>
                    </h5>
                    <h6>Total P/L: <span class="{% if bot_data['total_pl'] > 0 %}profit{% elif bot_data['total_pl'] < 0 %}loss{% endif %}">{{ format_price(bot_data['total_pl']) }}</span></h6>
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
                                <th>Stop Loss %</th>
                                <th>Stop Loss Price</th>
                                <th>Take Profit %</th>
                                <th>Take Profit Price</th>
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
        btc_price=get_bitcoin_price(),
        session=session,
        format_price=format_price,
        format_volume=format_volume,
        format_profit=format_profit
    )

@app.route('/settings_login', methods=['GET', 'POST'])
def settings_login():
    error = ""
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == SETTINGS_PASSWORD:
            session['settings_auth'] = True
            flash("Logged in successfully!", "success")
            return redirect(url_for('settings', bot=request.args.get('bot', '1.0')))
        else:
            error = "Incorrect password"
    return render_template_string("""
        <h2>Enter Settings Password</h2>
        <form method="POST">
            <input type="password" name="password" autofocus>
            <button type="submit">Login</button>
        </form>
        {% if error %}<div style="color:red">{{ error }}</div>{% endif %}
        <p><a href="{{ url_for('dashboard') }}">Back to dashboard</a></p>
    """, error=error)

@app.route('/settings_logout')
def settings_logout():
    session.pop('settings_auth', None)
    flash("Logged out.", "info")
    return redirect(url_for('dashboard'))

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if not session.get('settings_auth'):
        return redirect(url_for('settings_login', bot=request.args.get('bot', '1.0')))

    bot_id = request.args.get('bot', '1.0')
    if bot_id not in BOTS:
        return redirect(url_for('dashboard'))

    settings = load_bot_settings(bot_id)

    if request.method == 'POST':
        try:
            leverage = int(request.form.get('leverage', 5))
            stop_loss_pct = float(request.form.get('stop_loss_pct', 2.5))
            take_profit_pct = float(request.form.get('take_profit_pct', 3.0))

            if not (1 <= leverage <= 20):
                flash("Leverage must be between 1 and 20", "danger")
            elif not (0.1 <= stop_loss_pct <= 20):
                flash("Stop loss must be between 0.1% and 20%", "danger")
            elif not (0.1 <= take_profit_pct <= 50):
                flash("Take profit must be between 0.1% and 50%", "danger")
            else:
                save_bot_settings(bot_id, {
                    'leverage': leverage,
                    'stop_loss_pct': stop_loss_pct,
                    'take_profit_pct': take_profit_pct
                })
                flash("Settings saved successfully!", "success")
                return redirect(url_for('settings', bot=bot_id))
        except ValueError:
            flash("Invalid input values", "danger")

    return render_template_string('''
        <h2>Settings for {{ bot["name"] }}</h2>
        <form method="POST">
            <div class="mb-3">
                <label class="form-label">Leverage</label>
                <input type="number" name="leverage" value="{{ settings['leverage'] }}" min="1" max="20" class="form-control">
            </div>
            <div class="mb-3">
                <label class="form-label">Stop Loss (%)</label>
                <input type="number" name="stop_loss_pct" value="{{ settings['stop_loss_pct'] }}" step="0.1" min="0.1" max="20" class="form-control">
            </div>
            <div class="mb-3">
                <label class="form-label">Take Profit (%)</label>
                <input type="number" name="take_profit_pct" value="{{ settings['take_profit_pct'] }}" step="0.1" min="0.1" max="50" class="form-control">
            </div>
            <button type="submit" class="btn btn-primary">Save</button>
            <a href="{{ url_for('dashboard') }}" class="btn btn-secondary">Cancel</a>
        </form>
    ''', bot=BOTS[bot_id], settings=settings)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        logger.info(f"Webhook received: {data}")

        bot_raw = str(data.get("bot", "")).strip().lower()
        bot_id = bot_raw.replace("coinbot", "").replace(" ", "") if bot_raw.startswith("coinbot") else bot_raw
        if bot_id not in BOTS:
            return jsonify({"status": "error", "message": f"Unknown bot: {bot_id}"}), 400

        action = str(data.get("action", "")).lower()
        if action not in ["buy", "sell", "short", "cover"]:
            return jsonify({"status": "error", "message": f"Invalid action: {action}"}), 400

        symbol = str(data.get("symbol", "")).upper()
        if not symbol:
            return jsonify({"status": "error", "message": "Missing symbol"}), 400

        settings = load_bot_settings(bot_id)
        leverage = settings.get("leverage", 5)
        stop_loss_pct = settings.get("stop_loss_pct", 2.5)
        take_profit_pct = settings.get("take_profit_pct", 3.0)
        margin_pct = 0.05

        price = get_kraken_price(symbol)
        if not price or price <= 0:
            fetch_latest_prices([symbol])
            price = get_kraken_price(symbol)
            if not price or price <= 0:
                return jsonify({"status": "error", "message": f"No live Kraken price for {symbol}"}), 400

        account = load_account(bot_id)
        timestamp = pretty_now()
        reason = data.get("reason", "TradingView signal")

        if action in ["buy", "short"]:
            margin_used = account["balance"] * margin_pct

            if margin_used <= 0:
                return jsonify({"status": "error", "message": "Insufficient balance for allocation"}), 400

            volume = round((margin_used * leverage) / price, 6)

            if len(account["positions"].get(symbol, [])) >= 5:
                return jsonify({"status": "error", "message": "Position limit reached"}), 400

            # Set stop loss and take profit prices
            if action == "buy":
                stop_loss_price = price * (1 - stop_loss_pct/100)
                take_profit_price = price * (1 + take_profit_pct/100)
                position_type = "long"
            else:  # short
                stop_loss_price = price * (1 + stop_loss_pct/100)
                take_profit_price = price * (1 - take_profit_pct/100)
                position_type = "short"

            new_position = {
                "type": position_type,
                "volume": volume,
                "entry_price": price,
                "timestamp": timestamp,
                "margin_used": margin_used,
                "leverage": leverage,
                "stop_loss_pct": stop_loss_pct,
                "stop_loss_price": stop_loss_price,
                "take_profit_pct": take_profit_pct,
                "take_profit_price": take_profit_price
            }
            account["positions"].setdefault(symbol, []).append(new_position)
            account["balance"] -= margin_used

            account["trade_log"].append({
                "timestamp": timestamp,
                "action": action,
                "symbol": symbol,
                "reason": reason,
                "price": price,
                "amount": volume,
                "balance": round(account["balance"], 2),
                "leverage": leverage,
            })
            save_account(bot_id, account)
            logger.info(f"{action.upper()} executed for {symbol} at {price} with SL {stop_loss_pct}%, TP {take_profit_pct}% (bot {bot_id})")
            return jsonify({
                "status": "success",
                "action": action,
                "symbol": symbol,
                "price": price,
                "volume": volume,
                "stop_loss_price": new_position["stop_loss_price"],
                "take_profit_price": new_position["take_profit_price"]
            }), 200

        elif action in ["sell", "cover"]:
            positions = [p for p in account["positions"].get(symbol, [])
                        if (action == "sell" and p["type"] == "long") or
                           (action == "cover" and p["type"] == "short")]

            if not positions:
                return jsonify({"status": "error", "message": f"No {action} positions to close"}), 400

            total_volume = sum(float(p["volume"]) for p in positions)
            total_margin = sum(float(p["margin_used"]) for p in positions)
            avg_entry = sum(float(p["entry_price"]) * float(p["volume"]) for p in positions) / total_volume if total_volume > 0 else 0

            if action == "sell":
                profit = (price - avg_entry) * total_volume
            else:
                profit = (avg_entry - price) * total_volume

            pl_pct = ((price - avg_entry) / avg_entry * 100) if avg_entry > 0 else 0
            if action == "cover":
                pl_pct = -pl_pct

            account["balance"] += total_margin + profit

            account["positions"][symbol] = [p for p in account["positions"].get(symbol, [])
                                          if p not in positions]

            account["trade_log"].append({
                "timestamp": timestamp,
                "action": action,
                "symbol": symbol,
                "reason": reason,
                "price": price,
                "amount": total_volume,
                "profit": round(profit, 8),
                "pl_pct": round(pl_pct, 4),
                "balance": round(account["balance"], 8),
                "avg_entry": round(avg_entry, 8),
            })
            save_account(bot_id, account)
            logger.info(f"{action.upper()} executed for {symbol} at {price} (bot {bot_id})")
            return jsonify({"status": "success", "action": action, "symbol": symbol, "price": price}), 200

        return jsonify({"status": "error", "message": "Unhandled action"}), 400

    except Exception as e:
        logger.error(f"Webhook processing failed: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500

def check_and_trigger_stop_losses():
    while True:
        try:
            for bot_id in BOTS:
                account = load_account(bot_id)
                modified = False

                for symbol, positions in account["positions"].items():
                    current_price = get_kraken_price(symbol)
                    if current_price == 0:
                        continue
                    new_positions = []
                    for position in positions:
                        position_type = position.get("type", "long")
                        stop_loss_price = position.get("stop_loss_price",
                                                    position["entry_price"] * (1 - position.get("stop_loss_pct", 2.5)/100) if position_type == "long" else position["entry_price"] * (1 + position.get("stop_loss_pct", 2.5)/100))
                        take_profit_price = position.get("take_profit_price")
                        take_profit_pct = position.get("take_profit_pct", 3.0)
                        entry = float(position.get("entry_price", 0))
                        volume = float(position.get("volume", 0))
                        margin_used = float(position.get("margin_used", 0))
                        stop_loss_pct = float(position.get("stop_loss_pct", 2.5))
                        leverage = int(position.get("leverage", 1))

                        # Stop loss/take profit logic
                        if position_type == "long":
                            stop_loss_trigger = current_price <= stop_loss_price
                            take_profit_trigger = take_profit_price is not None and current_price >= take_profit_price
                        else:
                            stop_loss_trigger = current_price >= stop_loss_price
                            take_profit_trigger = take_profit_price is not None and current_price <= take_profit_price

                        if stop_loss_trigger or take_profit_trigger:
                            if position_type == "long":
                                profit = (current_price - entry) * volume
                                action = "sell"
                                reason = f"Stop Loss ({stop_loss_pct}%)" if stop_loss_trigger else f"Take Profit ({take_profit_pct}%)"
                            else:
                                profit = (entry - current_price) * volume
                                action = "cover"
                                reason = f"Stop Loss ({stop_loss_pct}%)" if stop_loss_trigger else f"Take Profit ({take_profit_pct}%)"

                            account["balance"] += margin_used + profit

                            account["trade_log"].append({
                                "timestamp": pretty_now(),
                                "action": action,
                                "symbol": symbol,
                                "reason": reason,
                                "price": current_price,
                                "amount": volume,
                                "profit": round(profit, 8),
                                "balance": round(account["balance"], 8),
                                "leverage": leverage,
                                "avg_entry": round(entry, 8),
                            })
                            modified = True
                            logger.info(f"{reason} triggered for {symbol} {position_type} at {current_price}")
                        else:
                            new_positions.append(position)

                    account["positions"][symbol] = new_positions

                if modified:
                    save_account(bot_id, account)

        except Exception as e:
            logger.error(f"Error in stop loss checker: {str(e)}", exc_info=True)

        time.sleep(2)

stop_loss_thread = threading.Thread(target=check_and_trigger_stop_losses, daemon=True)
stop_loss_thread.start()

if __name__ == '__main__':
    logger.info("Starting Flask server on port 5000")
    app.run(host='0.0.0.0', port=5000, threaded=True)
