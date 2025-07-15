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

# --- Jinja price format filter (also use as Python function below) ---
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

# --- Password for settings page ---
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

# ... (all other functions and routes unchanged) ...

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
        # --- Only change: build positions_html using format_price ---
        positions_html = ""
        for pos in position_stats:
            positions_html += (
                f"<tr><td>{pos['symbol']}</td>"
                f"<td>{pos['volume']:.6f}</td>"
                f"<td>{format_price(pos['entry_price'])}</td>"
                f"<td>{format_price(pos['current_price'])}</td>"
                f"<td>{pos['leverage']}x</td>"
                f"<td>${pos['margin_used']:.2f}</td>"
                f"<td>${pos['position_size']:.2f}</td>"
                f"<td class='{pos['pl_class']}'>{pos['pnl']:+.2f}</td></tr>"
            )
        if not positions_html:
            positions_html = "<tr><td colspan='8'>No open positions</td></tr>"
        # Rest is unchanged...
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
    <!-- ... The rest of your HTML, unchanged ... -->
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
    <!-- ... Rest of your HTML and route unchanged ... -->
    '''

    return render_template_string(
        html,
        dashboards=dashboards,
        active=active_bot,
        now=pretty_now(),
        coinbot_update_time=prev_update_time,
        btc_price=get_bitcoin_price(),
        session=session
    )

# --- Password protected settings page ---
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
    bot_id = request.args.get("bot", "1.0")
    if bot_id not in BOTS:
        bot_id = "1.0"
    settings = load_bot_settings(bot_id)
    message = ""
    if request.method == "POST":
        leverage = request.form.get("leverage", type=int, default=5)
        stop_loss_pct = request.form.get("stop_loss_pct", type=float, default=2.5)
        settings["leverage"] = leverage
        settings["stop_loss_pct"] = stop_loss_pct
        save_bot_settings(bot_id, settings)
        message = "Settings updated!"
    return render_template_string("""
        <h2>Settings for {{ bot_id }}</h2>
        <form method="POST">
            <label>Leverage: <input type="number" name="leverage" value="{{ settings['leverage'] }}" min="1" max="20"></label><br>
            <label>Stop Loss % (per position): <input type="number" step="0.01" name="stop_loss_pct" value="{{ settings['stop_loss_pct'] }}"></label><br>
            <button type="submit">Save</button>
        </form>
        <p style="color:green;">{{ message }}</p>
        <p><a href="{{ url_for('dashboard', active=bot_id) }}">Back to dashboard</a></p>
        <p><a href="{{ url_for('settings_logout') }}">Logout</a></p>
    """, bot_id=bot_id, settings=settings, message=message)

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

        # --- Fetch settings for this bot ---
        settings = load_bot_settings(bot_id)
        leverage = settings.get("leverage", 5)
        margin_pct = 0.05  # Still hard-coded. You could add this to settings if desired.

        # --- FIX: Fetch latest price on-demand if needed ---
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
                settings = load_bot_settings(bot_id)
                leverage = settings.get("leverage", 5)
                stop_loss_pct = settings.get("stop_loss_pct", 2.5)
                for symbol, positions in account["positions"].items():
                    kraken_price = get_kraken_price(symbol)
                    if kraken_price == 0:
                        logger.warning(f"No price for {symbol}, skipping stop loss check")
                        continue
                    new_positions = []
                    for position in positions:
                        entry = float(position["entry_price"])
                        lev = int(position.get("leverage", leverage))
                        stop_loss_price = entry * (1 - (stop_loss_pct / 100) / lev)  # Use current settings
                        if kraken_price > 0 and kraken_price <= stop_loss_price:
                            logger.info(f"Stop loss triggered for {symbol} at price {kraken_price:.6f} (entry: {entry:.6f}, stop: {stop_loss_price:.6f})")
                            reason = "Stop Loss"
                            timestamp = pretty_now()
                            volume = float(position["volume"])
                            margin_used = float(position["margin_used"])
                            profit = (kraken_price - entry) * volume * lev
                            pl_pct = ((kraken_price - entry) / entry * lev * 100) if entry > 0 else 0
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
                                "leverage": lev,
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
        needed_symbols.add("BTCUSDT")
        fetch_latest_prices(list(needed_symbols))
        time.sleep(2)

stop_loss_thread = threading.Thread(target=check_and_trigger_stop_losses, daemon=True)
stop_loss_thread.start()

if __name__ == '__main__':
    logger.info("Starting Flask server on port 5000")
    app.run(host='0.0.0.0', port=5000, threaded=True)
