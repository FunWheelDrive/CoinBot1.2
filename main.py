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
        logging.FileHandler("bot_trading.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-12345")

# --- Configuration ---
SETTINGS_PASSWORD = "bot"  # Change for production
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

# --- Utility Functions ---
def pretty_now():
    return datetime.now(ZoneInfo("America/Edmonton")).strftime('%Y-%m-%d %H:%M:%S %Z')

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

# --- Kraken API Integration ---
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
        logger.info(f"Updated prices for: {', '.join(prices.keys())}")
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
    except Exception as e:
        logger.error(f"Error saving account {bot_id}: {str(e)}")
        raise

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

# --- Routes ---
@app.route('/')
def dashboard():
    active_bot = request.args.get("active", "1.0")
    if active_bot not in BOTS:
        active_bot = "1.0"
    
    all_symbols = set()
    for bot_id in BOTS:
        account = load_account(bot_id)
        all_symbols.update(account["positions"].keys())
    all_symbols.add("BTCUSDT")
    prices = fetch_latest_prices(list(all_symbols))

    dashboards = {}
    for bot_id, bot_cfg in BOTS.items():
        account = load_account(bot_id)
        position_stats = calculate_position_stats(account["positions"], prices)
        total_margin = sum(pos['margin_used'] for pos in position_stats)
        total_pl = sum(pos['pnl'] for pos in position_stats)
        available_cash = float(account["balance"])
        equity = available_cash + total_margin + total_pl
        
        # Build positions HTML
        positions_html = ""
        for pos in position_stats:
            positions_html += f"""
            <tr>
                <td>{pos['symbol']}</td>
                <td>{pos['volume']:.6f}</td>
                <td>{format_price(pos['entry_price'])}</td>
                <td>{format_price(pos['current_price'])}</td>
                <td>{pos['leverage']}x</td>
                <td>${pos['margin_used']:.2f}</td>
                <td>${pos['position_size']:.2f}</td>
                <td class='{pos['pl_class']}'>{pos['pnl']:+.2f}</td>
            </tr>
            """
        if not positions_html:
            positions_html = "<tr><td colspan='8'>No open positions</td></tr>"
        
        # Build trade log HTML
        trade_log_html = ""
        for log in reversed(account["trade_log"][-50:]):  # Show last 50 trades
            profit = log.get('profit')
            pl_class = "profit" if profit and profit > 0 else "loss" if profit and profit < 0 else ""
            trade_log_html += f"""
            <tr>
                <td>{log.get('timestamp', '')}</td>
                <td>{log.get('action', '')}</td>
                <td>{log.get('symbol', '')}</td>
                <td>{log.get('reason', '')}</td>
                <td>${float(log.get('price', 0)):.2f}</td>
                <td>{float(log.get('amount', 0)):.6f}</td>
                <td class='{pl_class}'>{f'{float(profit):+.2f}' if profit is not None else ''}</td>
                <td>${float(log.get('balance', 0)):.2f}</td>
            </tr>
            """
        if not trade_log_html:
            trade_log_html = "<tr><td colspan='8'>No trades yet</td></tr>"
        
        dashboards[bot_id] = {
            'bot': bot_cfg,
            'account': account,
            'equity': equity,
            'available_cash': available_cash,
            'total_pl': total_pl,
            'positions_html': positions_html,
            'trade_log_html': trade_log_html
        }

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>CoinBot Dashboard</title>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
        <style>
            body { background: #1a1d28; color: #e2e2e2; }
            .bot-panel { background: rgba(27,29,39,0.93); border-radius: 8px; padding: 20px; margin-top: 20px; }
            .profit { color: #18e198; }
            .loss { color: #fd4561; }
            table { background: rgba(19,21,32,0.92); }
        </style>
    </head>
    <body>
    <div class="container mt-4">
        <h1>CoinBot Dashboard</h1>
        <ul class="nav nav-tabs">
            {% for bot_id, bot_data in dashboards.items() %}
            <li class="nav-item">
                <a class="nav-link {% if active == bot_id %}active{% endif %}" 
                   href="/?active={{ bot_id }}"
                   style="color: {{ bot_data['bot']['color'] }};">
                   {{ bot_data["bot"]["name"] }}
                </a>
            </li>
            {% endfor %}
        </ul>
        
        {% for bot_id, bot_data in dashboards.items() %}
        <div class="tab-pane {% if active == bot_id %}active{% endif %}" id="bot{{ bot_id }}">
            <div class="bot-panel">
                <h3 style="color: {{ bot_data['bot']['color'] }};">{{ bot_data['bot']["name"] }}</h3>
                <p>Balance: ${{ bot_data['available_cash']|round(2) }} | Equity: ${{ bot_data['equity']|round(2) }}</p>
                
                <h4>Open Positions</h4>
                <table class="table table-sm">
                    <thead>
                        <tr>
                            <th>Symbol</th><th>Volume</th><th>Entry</th><th>Current</th>
                            <th>Leverage</th><th>Margin</th><th>Size</th><th>P/L</th>
                        </tr>
                    </thead>
                    <tbody>
                        {{ bot_data['positions_html']|safe }}
                    </tbody>
                </table>
                
                <h4>Recent Trades</h4>
                <table class="table table-sm">
                    <thead>
                        <tr>
                            <th>Time</th><th>Action</th><th>Symbol</th><th>Reason</th>
                            <th>Price</th><th>Amount</th><th>Profit</th><th>Balance</th>
                        </tr>
                    </thead>
                    <tbody>
                        {{ bot_data['trade_log_html']|safe }}
                    </tbody>
                </table>
            </div>
        </div>
        {% endfor %}
    </div>
    </body>
    </html>
    """, dashboards=dashboards, active=active_bot, now=pretty_now(), btc_price=get_bitcoin_price())

@app.route('/settings_login', methods=['GET', 'POST'])
def settings_login():
    if request.method == 'POST':
        if request.form.get('password') == SETTINGS_PASSWORD:
            session['settings_auth'] = True
            return redirect(url_for('settings', bot=request.args.get('bot', '1.0')))
        return "Invalid password", 401
    return '''
    <form method="post">
        <input type="password" name="password" placeholder="Password">
        <button type="submit">Login</button>
    </form>
    '''

@app.route('/settings_logout')
def settings_logout():
    session.pop('settings_auth', None)
    return redirect(url_for('dashboard'))

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if not session.get('settings_auth'):
        return redirect(url_for('settings_login', bot=request.args.get('bot', '1.0')))
    
    bot_id = request.args.get('bot', '1.0')
    if bot_id not in BOTS:
        return "Invalid bot", 400
    
    settings = load_bot_settings(bot_id)
    
    if request.method == 'POST':
        settings['leverage'] = int(request.form.get('leverage', 5))
        settings['stop_loss_pct'] = float(request.form.get('stop_loss_pct', 2.5))
        save_bot_settings(bot_id, settings)
        return redirect(url_for('settings', bot=bot_id))
    
    return f'''
    <h2>Settings for {BOTS[bot_id]["name"]}</h2>
    <form method="post">
        Leverage: <input type="number" name="leverage" value="{settings['leverage']}" min="1" max="20"><br>
        Stop Loss %: <input type="number" step="0.1" name="stop_loss_pct" value="{settings['stop_loss_pct']}"><br>
        <button type="submit">Save</button>
    </form>
    <a href="/">Back to Dashboard</a>
    '''

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        logger.info(f"Webhook received: {data}")

        # Validate bot
        bot_id = str(data.get('bot', '1.0'))
        if bot_id not in BOTS:
            return jsonify({"status": "error", "message": "Invalid bot"}), 400

        # Validate action
        action = str(data.get('action', '')).lower()
        if action not in ['buy', 'sell']:
            return jsonify({"status": "error", "message": "Invalid action"}), 400

        # Validate symbol
        symbol = str(data.get('symbol', '')).upper()
        if not symbol:
            return jsonify({"status": "error", "message": "Missing symbol"}), 400

        # Load account and settings
        account = load_account(bot_id)
        settings = load_bot_settings(bot_id)
        leverage = settings.get('leverage', 5)
        stop_loss_pct = settings.get('stop_loss_pct', 2.5)
        margin_pct = 0.05  # 5% of balance per trade

        # Get current price
        price = get_kraken_price(symbol)
        if price <= 0:
            fetch_latest_prices([symbol])
            price = get_kraken_price(symbol)
            if price <= 0:
                return jsonify({"status": "error", "message": "Could not get price"}), 400

        timestamp = pretty_now()
        reason = data.get('reason', 'TradingView signal')

        if action == 'buy':
            margin_used = account['balance'] * margin_pct
            if margin_used <= 0:
                return jsonify({"status": "error", "message": "Insufficient balance"}), 400

            volume = round((margin_used * leverage) / price, 6)
            
            new_position = {
                "volume": volume,
                "entry_price": price,
                "timestamp": timestamp,
                "margin_used": margin_used,
                "leverage": leverage,
                "stop_loss_pct": stop_loss_pct,
                "stop_loss_price": price * (1 - stop_loss_pct/100)
            }
            
            account['positions'].setdefault(symbol, []).append(new_position)
            account['balance'] -= margin_used
            
            account['trade_log'].append({
                "timestamp": timestamp,
                "action": "buy",
                "symbol": symbol,
                "reason": reason,
                "price": price,
                "amount": volume,
                "balance": round(account['balance'], 2),
                "leverage": leverage,
                "stop_loss_pct": stop_loss_pct
            })
            
            save_account(bot_id, account)
            return jsonify({
                "status": "success",
                "action": "buy",
                "symbol": symbol,
                "price": price,
                "volume": volume,
                "stop_loss_price": new_position["stop_loss_price"]
            })

        elif action == 'sell':
            positions = account['positions'].get(symbol, [])
            if not positions:
                return jsonify({"status": "error", "message": "No positions to sell"}), 400

            total_volume = sum(p['volume'] for p in positions)
            total_margin = sum(p['margin_used'] for p in positions)
            avg_entry = sum(p['entry_price'] * p['volume'] for p in positions) / total_volume
            
            profit = (price - avg_entry) * total_volume
            account['balance'] += total_margin + profit
            account['positions'][symbol] = []
            
            account['trade_log'].append({
                "timestamp": timestamp,
                "action": "sell",
                "symbol": symbol,
                "reason": reason,
                "price": price,
                "amount": total_volume,
                "profit": round(profit, 2),
                "balance": round(account['balance'], 2)
            })
            
            save_account(bot_id, account)
            return jsonify({
                "status": "success",
                "action": "sell",
                "symbol": symbol,
                "price": price,
                "volume": total_volume,
                "profit": round(profit, 2)
            })

    except Exception as e:
        logger.error(f"Webhook error: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal error"}), 500

# --- Stop Loss Monitor ---
def check_and_trigger_stop_losses():
    while True:
        try:
            for bot_id in BOTS:
                account = load_account(bot_id)
                modified = False
                
                for symbol, positions in account['positions'].items():
                    current_price = get_kraken_price(symbol)
                    if current_price <= 0:
                        continue
                        
                    new_positions = []
                    for position in positions:
                        stop_price = position.get('stop_loss_price', 
                                              position['entry_price'] * (1 - position.get('stop_loss_pct', 2.5)/100))
                        
                        if current_price <= stop_price:
                            # Trigger stop loss
                            volume = position['volume']
                            margin = position['margin_used']
                            profit = (current_price - position['entry_price']) * volume
                            
                            account['balance'] += margin + profit
                            account['trade_log'].append({
                                "timestamp": pretty_now(),
                                "action": "sell",
                                "symbol": symbol,
                                "reason": f"Stop Loss ({position.get('stop_loss_pct', 2.5)}%)",
                                "price": current_price,
                                "amount": volume,
                                "profit": round(profit, 2),
                                "balance": round(account['balance'], 2)
                            })
                            modified = True
                            logger.info(f"Stop loss triggered for {symbol} at {current_price}")
                        else:
                            new_positions.append(position)
                    
                    account['positions'][symbol] = new_positions
                
                if modified:
                    save_account(bot_id, account)
                    
        except Exception as e:
            logger.error(f"Stop loss monitor error: {str(e)}")
        
        time.sleep(2)

# --- Start Background Thread ---
if __name__ == '__main__':
    stop_loss_thread = threading.Thread(target=check_and_trigger_stop_losses, daemon=True)
    stop_loss_thread.start()
    
    logger.info("Starting CoinBot Trading System")
    app.run(host='0.0.0.0', port=5000, threaded=True, use_reloader=False)
