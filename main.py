from flask import Flask, request, render_template_string
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import os

app = Flask(__name__)
DATA_FILE = "account.json"
STARTING_BALANCE = 1000.00  # Your starting cash for P/L display

def save_account():
    with open(DATA_FILE, "w") as f:
        json.dump(account, f, indent=2, default=str)

def load_account():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            loaded = json.load(f)
            loaded["balance"] = float(loaded.get("balance", 1000))
            loaded["positions"] = loaded.get("positions", {})
            loaded["trade_log"] = loaded.get("trade_log", [])
            return loaded
    return {
        "balance": STARTING_BALANCE,
        "positions": {},  # {symbol: [{volume, entry_price, timestamp, usd_spent, leverage}]}
        "trade_log": []
    }

account = load_account()

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
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_ids}&vs_currencies=usd"
    try:
        r = requests.get(url, timeout=6)
        data = r.json()
        price_map = {}
        for sym, cg_id in symbol_map.items():
            if cg_id in data:
                price_map[sym] = float(data[cg_id]["usd"])
        for sym in symbols:
            if sym in price_map:
                prices[sym] = price_map[sym]
        print("Latest CoinGecko prices:", prices)
    except Exception as e:
        print("CoinGecko price fetch failed:", e)
    return prices

@app.route('/', methods=['GET'])
def dashboard():
    symbols = [
        symbol for symbol, poslist in account["positions"].items() if poslist
    ]
    latest_prices = fetch_latest_prices(symbols)
    open_positions = []

    for symbol, poslist in account["positions"].items():
        if poslist:
            total_volume = sum(p["volume"] for p in poslist)
            avg_entry = sum(p["entry_price"] * p["volume"]
                            for p in poslist) / total_volume if total_volume else 0
            last_price = latest_prices.get(symbol, avg_entry)
            leverage = poslist[0].get("leverage", 5)
            position_value = last_price * total_volume
            unrealized = (last_price - avg_entry) * total_volume
            pl_pct = ((last_price - avg_entry) / avg_entry * leverage * 100) if avg_entry > 0 else 0
            open_positions.append({
                "symbol": symbol,
                "num_positions": len(poslist),
                "total_volume": total_volume,
                "avg_entry": avg_entry,
                "last_price": last_price,
                "unrealized": unrealized,
                "position_value": position_value,
                "leverage": leverage,
                "pl_pct": pl_pct  # always defined
            })

    total_cash = account["balance"]
    total_margin = sum(
        sum(p["usd_spent"] for p in poslist)
        for poslist in account["positions"].values() if poslist)
    total_unrealized = sum(pos["unrealized"] for pos in open_positions)
    account_equity = total_cash + total_margin + total_unrealized
    total_pl = account_equity - STARTING_BALANCE

    # Defensive: Ensure every trade_log entry has 'pl_pct'
    trade_log = []
    for entry in account["trade_log"]:
        if 'pl_pct' not in entry or entry['pl_pct'] is None:
            if entry.get('action') == 'sell':
                try:
                    entry['pl_pct'] = round(
                        ((entry['price'] - entry.get('avg_entry', entry['price'])) /
                         (entry.get('avg_entry', entry['price'])) * entry.get('leverage', 5) * 100)
                        if entry.get('avg_entry', entry['price']) > 0 else 0, 2)
                except Exception:
                    entry['pl_pct'] = 0
            else:
                entry['pl_pct'] = None
        trade_log.append(entry)

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
  <title>CoinBot1.1 – Dashboard (CoinGecko)</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://fonts.googleapis.com/css?family=Inter:400,700&display=swap" rel="stylesheet">
  <style>
    :root {
      --crypto-gold: #ffd86e;
      --crypto-green: #54e6b1;
      --crypto-dark: #22272f;
      --crypto-darker: #1a1f27;
      --accent: #f7ca59;
      --table-header: #2a2e3a;
      --card-bg: #2e3443;
      --pill-bg: #222a3a;
      --pill-border: #6272f7;
      --shadow: 0 5px 42px rgba(20,30,60,0.2);
      --card-shadow: 0 3px 22px rgba(40,50,75,0.3);
      color-scheme: dark;
    }
    html, body {
      margin: 0;
      background: var(--crypto-darker);
      min-height: 100vh;
      font-family: 'Inter', Arial, sans-serif;
      color: #d9e6f3;
      position: relative;
    }
    body:before {
      content: "";
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: radial-gradient(circle at 68% 28%, #323f75 0px, transparent 700px),
                  radial-gradient(circle at 25% 77%, #ffd86e33 0px, transparent 450px),
                  radial-gradient(circle at 85% 90%, #39ffae22 0px, transparent 370px);
      z-index: 0;
      pointer-events: none;
    }
    .container {
      max-width: 1100px;
      margin: 38px auto;
      padding: 44px 24px 34px 24px;
      background: var(--crypto-dark);
      border-radius: 28px;
      box-shadow: var(--shadow);
      position: relative;
      z-index: 1;
    }
    h2 {
      font-size: 2.32em;
      margin: 0 0 18px 0;
      font-weight: 700;
      letter-spacing: 1.4px;
      color: var(--crypto-gold);
      text-align: center;
      text-shadow: 0 3px 14px #232b1d66;
    }
    .meta {
      display: flex;
      gap: 22px;
      align-items: center;
      font-size: 1.17em;
      margin-bottom: 25px;
      flex-wrap: wrap;
      justify-content: center;
      color: #cbd6f4;
      user-select: none;
    }
    .meta span {
      background: #252b39;
      border-radius: 18px;
      padding: 12px 27px;
      color: var(--crypto-gold);
      margin-bottom: 4px;
      border: 1.6px solid var(--crypto-gold);
      font-weight: 700;
      box-shadow: 0 1px 7px #ffd86e26;
      font-size: 1.09em;
      letter-spacing: 0.3px;
      min-width: 200px;
      text-align: center;
    }
    .meta .pnl {
      font-weight: 700;
      padding-left: 6px;
      padding-right: 6px;
      border-radius: 10px;
    }
    .meta .pnl-pos {
      color: var(--crypto-green);
      background: #142921;
      border: 1.5px solid #34ffa2;
    }
    .meta .pnl-neg {
      color: #ff5757;
      background: #2c1717;
      border: 1.5px solid #f57373;
    }
    .card {
      background: var(--card-bg);
      border-radius: 22px;
      box-shadow: var(--card-shadow);
      padding: 32px 20px 18px 20px;
      margin-bottom: 36px;
      position: relative;
      overflow: hidden;
    }
    .positions-section {
      margin-bottom: 32px;
    }
    .positions-section h3, .log-section h3 {
      color: var(--crypto-gold);
      margin: 0 0 13px 0;
      font-size: 1.33em;
      font-weight: 700;
      letter-spacing: 0.10em;
      text-align: left;
    }
    .table-wrap {
      overflow-x: auto;
    }
    table {
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      margin-top: 10px;
      background: var(--card-bg);
      border-radius: 16px;
      box-shadow: var(--card-shadow);
    }
    th, td {
      padding: 15px 13px;
      border-bottom: 1.6px solid #23233b;
      text-align: center;
      font-size: 1.06em;
      user-select: text;
    }
    th {
      background: var(--table-header);
      font-weight: 700;
      color: var(--crypto-gold);
      font-size: 1.13em;
      border-top: 1.6px solid #ffe3c2;
      letter-spacing: 0.5px;
    }
    tr:last-child td { border-bottom: none; }
    tr.buy { background: #22352a; }
    tr.sell { background: #3a2424;}
    td .coin-pill {
      display: inline-block;
      padding: 4px 18px 4px 18px;
      background: var(--pill-bg);
      color: var(--crypto-gold);
      border-radius: 32px;
      font-size: 1.05em;
      font-weight: 700;
      border: 1.7px solid var(--crypto-gold);
      letter-spacing: 0.4px;
      margin: 0 0 0 0;
      user-select: text;
    }
    .pnl-pos {
      color: var(--crypto-green);
      background: #142921;
      border-radius: 13px;
      padding: 4px 13px;
      font-weight: 700;
      letter-spacing: 0.4px;
      display: inline-block;
      border: 1.5px solid #34ffa2;
      user-select: text;
    }
    .pnl-neg {
      color: #ff5757;
      background: #2c1717;
      border-radius: 13px;
      padding: 4px 13px;
      font-weight: 700;
      letter-spacing: 0.4px;
      display: inline-block;
      border: 1.5px solid #f57373;
      user-select: text;
    }
  </style>
</head>
<body>
  <div class="container">
    <h2>
      <span style="font-size:1.20em;vertical-align:-3px;">&#128176; </span>CoinBot1.1 Dashboard
    </h2>
    <div class="meta">
      <span><b>Account Balance:</b> ${{ '{:,.2f}'.format(balance) }}</span>
      <span><b>Available Cash:</b> ${{ '{:,.2f}'.format(cash) }}</span>
      <span><b>Total P/L:</b>
        <span class="pnl {% if total_pl >= 0 %}pnl-pos{% else %}pnl-neg{% endif %}">
          ${{ '{:,.2f}'.format(total_pl) }}
        </span>
      </span>
      <span><b>Last Update:</b> {{ now }}</span>
    </div>
    <div class="positions-section card">
      <h3>Open Positions (Live P&amp;L)</h3>
      {% if open_positions %}
      <div class="table-wrap">
      <table>
        <tr>
          <th>Coin</th>
          <th># Positions</th>
          <th>Total Volume</th>
          <th>Avg Entry</th>
          <th>Last Price <span style="font-weight:400;font-size:0.90em;color:#b4bcde;">(live)</span></th>
          <th>Unrealized P&amp;L</th>
          <th>P&amp;L % (5x)</th>
          <th>Position Value</th>
          <th>Leverage</th>
        </tr>
        {% for pos in open_positions %}
        <tr>
          <td><span class="coin-pill">{{ pos.symbol }}</span></td>
          <td>{{ pos.num_positions }}</td>
          <td>{{ '{:,.5f}'.format(pos.total_volume) }}</td>
          <td>${{ '{:,.5f}'.format(pos.avg_entry) }}</td>
          <td>${{ '{:,.5f}'.format(pos.last_price) }}</td>
          <td>
            <span class="{% if pos.unrealized >= 0 %}pnl-pos{% else %}pnl-neg{% endif %}">
              ${{ '{:,.2f}'.format(pos.unrealized) }}
            </span>
          </td>
          <td>
            <span class="{% if pos.pl_pct is not defined or pos.pl_pct >= 0 %}pnl-pos{% else %}pnl-neg{% endif %}">
              {{ '{:+.2f}%'.format(pos.pl_pct if pos.pl_pct is defined and pos.pl_pct is not none else 0) }}
            </span>
          </td>
          <td>${{ '{:,.2f}'.format(pos.position_value) }}</td>
          <td>{{ pos.leverage }}x</td>
        </tr>
        {% endfor %}
      </table>
      </div>
      {% else %}
      <div class="no-positions"><b>No open positions.</b></div>
      {% endif %}
    </div>
    <div class="log-section card">
      <h3>Trade Log</h3>
      <div class="table-wrap">
      <table>
        <tr>
          <th>Time</th>
          <th>Action</th>
          <th>Coin</th>
          <th>Reason</th>
          <th>Price</th>
          <th>Amount</th>
          <th>P&amp;L</th>
          <th>P&amp;L % (5x)</th>
          <th>Balance After</th>
        </tr>
        {% for trade in trade_log %}
        <tr class="{{ trade.action }}">
          <td>{{ trade.timestamp }}</td>
          <td style="font-weight:700;">{{ trade.action|upper }}</td>
          <td><span class="coin-pill">{{ trade.symbol or "-" }}</span></td>
          <td>{{ trade.reason or "-" }}</td>
          <td>${{ '{:,.5f}'.format(trade.price) }}</td>
          <td>{{ '{:,.5f}'.format(trade.amount) }}</td>
          <td>
            {% if trade.action == 'sell' %}
            <span class="{% if trade.profit >= 0 %}pnl-pos{% else %}pnl-neg{% endif %}">
              ${{ '{:,.2f}'.format(trade.profit) }}
            </span>
            {% else %}-{% endif %}
          </td>
          <td>
            {% if trade.action == 'sell' %}
              <span class="{% if trade.pl_pct is not defined or trade.pl_pct >= 0 %}pnl-pos{% else %}pnl-neg{% endif %}">
                {{ '{:+.2f}%'.format(trade.pl_pct if trade.pl_pct is defined and trade.pl_pct is not none else 0) }}
              </span>
            {% else %}-{% endif %}
          </td>
          <td>${{ '{:,.2f}'.format(trade.balance) }}</td>
        </tr>
        {% endfor %}
      </table>
      </div>
    </div>
  </div>
</body>
</html>
"""

    return render_template_string(html,
                                  balance=round(account_equity, 2),
                                  cash=round(total_cash, 2),
                                  total_pl=round(total_pl, 2),
                                  open_positions=open_positions,
                                  trade_log=reversed(trade_log),
                                  now=pretty_now())

@app.route('/webhook', methods=['POST'])
def webhook():
    data = json.loads(request.data)
    action = data.get("action", "none").lower()
    price = float(data.get("price", 0))
    symbol = data.get("symbol", "N/A")
    reason = "TradingView signal"
    timestamp = datetime.now(
        ZoneInfo("America/Edmonton")).strftime('%Y-%m-%d %H:%M:%S %Z')

    leverage = 5
    margin_pct = 0.05  # 5% margin used per trade

    if action == "buy":
        margin_cash = account["balance"] * margin_pct
        position_size = margin_cash * leverage
        volume = round(position_size / price, 6) if price > 0 else 0

        poslist = account["positions"].get(symbol, [])
        if len(poslist) < 5 and volume > 0:
            if account["balance"] >= margin_cash:
                poslist.append({
                    "volume": volume,
                    "entry_price": price,
                    "timestamp": timestamp,
                    "usd_spent": margin_cash,
                    "leverage": leverage,
                })
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

    elif action == "sell":
        poslist = account["positions"].get(symbol, [])
        if poslist:
            total_volume = sum(p["volume"] for p in poslist)
            total_margin = sum(p["usd_spent"] for p in poslist)
            avg_entry = sum(p["entry_price"] * p["volume"]
                            for p in poslist) / total_volume
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

    save_account()
    return {"status": "ok"}, 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)

