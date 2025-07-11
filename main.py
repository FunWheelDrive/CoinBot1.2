from flask import Flask, request, render_template_string, jsonify
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import threading
import logging

# Configure logging
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

# Create a lock for thread-safe file operations
file_lock = threading.Lock()

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

@app.route('/', methods=['GET'])
def dashboard():
    # [Keep your existing dashboard code unchanged]
    # ... (no changes needed to your HTML/dashboard rendering) ...

@app.route('/health', methods=['GET'])
def health_check():
    """Simple health check endpoint"""
    return jsonify({"status": "ok", "time": pretty_now()}), 200

@app.route('/webhook', methods=['POST'])
def webhook():
    global account
    
    # Log incoming request
    client_ip = request.remote_addr
    logger.info(f"Webhook received from {client_ip}")
    
    try:
        # Validate request
        if not request.data:
            logger.warning("Empty webhook payload")
            return jsonify({"status": "error", "message": "Empty payload"}), 400
            
        data = request.get_json()
        if not data:
            logger.warning("Invalid JSON payload")
            return jsonify({"status": "error", "message": "Invalid JSON"}), 400
            
        # Extract and validate required fields
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

        # Process buy action
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
                    
                    # Add new position
                    poslist.append(new_position)
                    account["positions"][symbol] = poslist
                    account["balance"] -= margin_cash
                    
                    # Add to trade log
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

        # Process sell action
        elif action == "sell":
            poslist = account["positions"].get(symbol, [])
            if poslist:
                total_volume = sum(p["volume"] for p in poslist)
                total_margin = sum(p["usd_spent"] for p in poslist)
                avg_entry = sum(p["entry_price"] * p["volume"] for p in poslist) / total_volume
                profit = (price - avg_entry) * total_volume
                pl_pct = ((price - avg_entry) / avg_entry * leverage * 100) if avg_entry > 0 else 0
                
                # Update account
                account["balance"] += total_margin + profit
                
                # Add to trade log
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
                
                # Clear positions
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

