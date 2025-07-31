import os
import json
import threading
import logging
from datetime import datetime
import ccxt
from zoneinfo import ZoneInfo
import requests

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("live_trading.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
DATA_DIR = "data"
LIVE_TRADING_STATE_FILE = os.path.join(DATA_DIR, "live_trading.json")
COINEX_API_KEY = os.environ.get("COINEX_API_KEY", "")
COINEX_API_SECRET = os.environ.get("COINEX_API_SECRET", "")
LIVE_TRADING_ENABLED = False  # Controlled by main.py
live_trading_lock = threading.Lock()

# CoinEx market pairs (aligned with main.py)
coinex_pairs = {
    "BTCUSDT": "BTC/USDT",
    "ETHUSDT": "ETH/USDT",
    # Add other pairs as needed
}

# Initialize CoinEx exchange
exchange = ccxt.coinex({
    'apiKey': COINEX_API_KEY,
    'secret': COINEX_API_SECRET,
    'enableRateLimit': True
})

# Global state
live_trading_state = {
    "enabled": False,
    "balance": 0.0,
    "positions": {},
    "trade_log": [],
    "selected_bots": [],
    "position_size_pct": 1.0,
    "live_kill_switch": {
        "active": False,
        "starting_balance": None,
        "starting_balance_date": None,
        "breach_start": None,
        "kill_switch_pct": 5.0
    }
}

def pretty_now():
    try:
        return datetime.now(ZoneInfo("America/Edmonton")).strftime('%Y-%m-%d %H:%M:%S %Z')
    except:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def load_live_trading_state():
    """Load live trading state from file."""
    global live_trading_state
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(LIVE_TRADING_STATE_FILE):
        return live_trading_state
    try:
        with live_trading_lock:
            with open(LIVE_TRADING_STATE_FILE, "r") as f:
                state = json.load(f)
        # Ensure all required keys exist
        for key, default in live_trading_state.items():
            if key not in state:
                state[key] = default
        # Validate positions
        for symbol in state.get("positions", {}):
            for pos in state["positions"][symbol]:
                pos["volume"] = float(pos.get("volume", 0))
                pos["entry_price"] = float(pos.get("entry_price", 0))
                pos["leverage"] = int(pos.get("leverage", 1))
                pos["unrealized_pnl"] = float(pos.get("unrealized_pnl", 0))
                pos["stop_loss_price"] = float(pos.get("stop_loss_price", 0)) if pos.get("stop_loss_price") else None
                pos["take_profit_price"] = float(pos.get("take_profit_price", 0)) if pos.get("take_profit_price") else None
        live_trading_state.update(state)
        return state
    except Exception as e:
        logger.error(f"Error loading live trading state: {str(e)}")
        return live_trading_state

def save_live_trading_state(state):
    """Save live trading state to file."""
    try:
        with live_trading_lock:
            with open(LIVE_TRADING_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2, default=str)
        logger.info("Live trading state saved")
    except Exception as e:
        logger.error(f"Error saving live trading state: {str(e)}")
        raise

def verify_password(password):
    """Verify live trading password (placeholder for secure implementation)."""
    LIVE_TRADING_PASSWORD = os.environ.get("LIVE_TRADING_PASSWORD", "secure_live_password")
    return password == LIVE_TRADING_PASSWORD

def get_account_balance():
    """Fetch account balance from CoinEx."""
    try:
        balance = exchange.fetch_balance()
        total_balance = float(balance['total'].get('USDT', 0.0))
        return {"status": "success", "total_balance": total_balance}
    except Exception as e:
        logger.error(f"Error fetching CoinEx balance: {str(e)}")
        return {"status": "error", "message": str(e), "total_balance": live_trading_state.get("balance", 0.0)}

def place_market_order(symbol, action, volume):
    """Place a market order on CoinEx."""
    try:
        pair = coinex_pairs.get(symbol)
        if not pair:
            return {"status": "error", "message": f"Invalid symbol: {symbol}"}
        
        side = "buy" if action in ["buy", "cover"] else "sell"
        order = exchange.create_market_order(pair, side, volume)
        price = float(order['price']) if order.get('price') else get_coinex_price(symbol)
        order_id = order.get('id')
        
        return {
            "status": "success",
            "order_id": order_id,
            "price": price,
            "volume": volume
        }
    except Exception as e:
        logger.error(f"Error placing market order for {symbol}: {str(e)}")
        return {"status": "error", "message": str(e)}

def place_stop_loss_order(symbol, volume, stop_price, side):
    """Place a stop-loss order on CoinEx."""
    try:
        pair = coinex_pairs.get(symbol)
        if not pair:
            return {"status": "error", "message": f"Invalid symbol: {symbol}"}
        
        order = exchange.create_order(
            symbol=pair,
            type='stop',
            side=side,
            amount=volume,
            params={'stopPrice': stop_price}
        )
        return {
            "status": "success",
            "order_id": order.get('id'),
            "stop_price": stop_price
        }
    except Exception as e:
        logger.error(f"Error placing stop-loss order for {symbol}: {str(e)}")
        return {"status": "error", "message": str(e)}

def place_take_profit_order(symbol, volume, take_profit_price, side):
    """Place a take-profit order on CoinEx."""
    try:
        pair = coinex_pairs.get(symbol)
        if not pair:
            return {"status": "error", "message": f"Invalid symbol: {symbol}"}
        
        order = exchange.create_order(
            symbol=pair,
            type='limit',
            side=side,
            amount=volume,
            price=take_profit_price
        )
        return {
            "status": "success",
            "order_id": order.get('id'),
            "take_profit_price": take_profit_price
        }
    except Exception as e:
        logger.error(f"Error placing take-profit order for {symbol}: {str(e)}")
        return {"status": "error", "message": str(e)}

def cancel_order(symbol, order_id):
    """Cancel an order on CoinEx."""
    try:
        pair = coinex_pairs.get(symbol)
        if not pair:
            return {"status": "error", "message": f"Invalid symbol: {symbol}"}
        
        exchange.cancel_order(order_id, pair)
        return {"status": "success", "message": f"Order {order_id} canceled"}
    except Exception as e:
        logger.error(f"Error canceling order {order_id} for {symbol}: {str(e)}")
        return {"status": "error", "message": str(e)}

def get_coinex_price(symbol):
    """Fetch current price from CoinEx API."""
    try:
        pair = coinex_pairs.get(symbol)
        if not pair:
            logger.warning(f"No CoinEx pair for {symbol}")
            return 0
        ticker = exchange.fetch_ticker(pair)
        return float(ticker['last'])
    except Exception as e:
        logger.error(f"Error fetching price for {symbol}: {str(e)}")
        return 0

def handle_webhook(data, bot_settings, get_price_func):
    """Handle webhook for live trading."""
    global LIVE_TRADING_ENABLED
    if not LIVE_TRADING_ENABLED:
        return jsonify({"status": "error", "message": "Live trading is disabled"}), 400

    try:
        bot_id = str(data.get("bot", "")).strip().lower().replace("coinbot", "").replace(" ", "")
        if bot_id not in live_trading_state.get("selected_bots", []):
            return jsonify({"status": "error", "message": f"Bot {bot_id} not selected for live trading"}), 400

        with live_trading_lock:
            if live_trading_state.get("live_kill_switch", {}).get("active", False):
                return jsonify({"status": "error", "message": "Live trading halted due to kill switch activation"}), 400

        action = str(data.get("action", "")).lower()
        if action not in ["buy", "sell", "short", "cover"]:
            return jsonify({"status": "error", "message": f"Invalid action: {action}"}), 400

        symbol = str(data.get("symbol", "")).upper()
        if symbol not in coinex_pairs:
            return jsonify({"status": "error", "message": f"Invalid symbol: {symbol}"}), 400

        settings = bot_settings.get(bot_id, {
            "leverage": 5,
            "stop_loss_pct": 2.5,
            "take_profit_pct": 3.0
        })
        leverage = settings.get("leverage", 5)
        stop_loss_pct = settings.get("stop_loss_pct", 2.5)
        take_profit_pct = settings.get("take_profit_pct", 3.0)
        position_size_pct = live_trading_state.get("position_size_pct", 1.0)

        price = get_price_func(symbol)
        if not price or price <= 0:
            return jsonify({"status": "error", "message": f"No live price for {symbol}"}), 400

        state = load_live_trading_state()
        timestamp = pretty_now()

        if action in ["buy", "short"]:
            balance = get_account_balance().get("total_balance", state.get("balance", 0.0))
            if balance <= 0:
                return jsonify({"status": "error", "message": "Insufficient balance"}), 400

            margin_used = balance * (position_size_pct / 100)
            volume = round((margin_used * leverage) / price, 6)

            if len(state["positions"].get(symbol, [])) >= 5:
                return jsonify({"status": "error", "message": "Position limit reached"}), 400

            position_type = "long" if action == "buy" else "short"
            stop_loss_price = price * (1 - stop_loss_pct/100) if action == "buy" else price * (1 + stop_loss_pct/100)
            take_profit_price = price * (1 + take_profit_pct/100) if action == "buy" else price * (1 - take_profit_pct/100)
            stop_side = "sell" if action == "buy" else "buy"
            tp_side = "sell" if action == "buy" else "buy"

            # Place market order
            order_result = place_market_order(symbol, action, volume)
            if order_result["status"] != "success":
                return jsonify(order_result), 400

            order_price = order_result["price"]
            order_id = order_result["order_id"]

            # Place stop-loss order
            sl_result = place_stop_loss_order(symbol, volume, stop_loss_price, stop_side)
            sl_order_id = sl_result.get("order_id") if sl_result["status"] == "success" else None

            # Place take-profit order
            tp_result = place_take_profit_order(symbol, volume, take_profit_price, tp_side)
            tp_order_id = tp_result.get("order_id") if tp_result["status"] == "success" else None

            new_position = {
                "type": position_type,
                "volume": volume,
                "entry_price": order_price,
                "leverage": leverage,
                "unrealized_pnl": 0.0,
                "stop_loss_price": stop_loss_price,
                "stop_loss_order_id": sl_order_id,
                "take_profit_price": take_profit_price,
                "take_profit_order_id": tp_order_id,
                "timestamp": timestamp
            }
            state["positions"].setdefault(symbol, []).append(new_position)
            state["trade_log"].append({
                "timestamp": timestamp,
                "action": action,
                "symbol": symbol,
                "reason": data.get("reason", "TradingView signal"),
                "price": order_price,
                "amount": volume,
                "leverage": leverage
            })
            state["balance"] = get_account_balance().get("total_balance", state.get("balance", 0.0))
            save_live_trading_state(state)
            live_trading_state.update(state)

            logger.info(f"Live {action.upper()} executed for {symbol} at {order_price} with SL {stop_loss_price}, TP {take_profit_price}")
            return jsonify({
                "status": "success",
                "action": action,
                "symbol": symbol,
                "price": order_price,
                "volume": volume,
                "stop_loss_price": stop_loss_price,
                "take_profit_price": take_profit_price,
                "order_id": order_id
            }), 200

        elif action in ["sell", "cover"]:
            positions = [p for p in state["positions"].get(symbol, [])
                        if (action == "sell" and p["type"] == "long") or
                           (action == "cover" and p["type"] == "short")]
            if not positions:
                return jsonify({"status": "error", "message": f"No {action} positions to close for {symbol}"}), 400

            total_volume = sum(float(p["volume"]) for p in positions)
            total_profit = 0
            new_positions = [p for p in state["positions"].get(symbol, []) if p not in positions]

            for position in positions:
                entry_price = float(position.get("entry_price", 0))
                volume = float(position.get("volume", 0))
                leverage = int(position.get("leverage", 1))
                sl_order_id = position.get("stop_loss_order_id")
                tp_order_id = position.get("take_profit_order_id")

                # Cancel stop-loss and take-profit orders
                if sl_order_id:
                    cancel_order(symbol, sl_order_id)
                if tp_order_id:
                    cancel_order(symbol, tp_order_id)

                # Place market order to close position
                order_result = place_market_order(symbol, action, volume)
                if order_result["status"] != "success":
                    return jsonify(order_result), 400

                close_price = order_result["price"]
                profit = (close_price - entry_price) * volume if action == "sell" else (entry_price - close_price) * volume
                total_profit += profit

                state["trade_log"].append({
                    "timestamp": timestamp,
                    "action": action,
                    "symbol": symbol,
                    "reason": data.get("reason", "TradingView signal"),
                    "price": close_price,
                    "amount": volume,
                    "profit": round(profit, 8),
                    "leverage": leverage,
                    "avg_entry": round(entry_price, 8)
                })

            state["positions"][symbol] = new_positions
            if not new_positions:
                del state["positions"][symbol]
            state["balance"] = get_account_balance().get("total_balance", state.get("balance", 0.0))
            save_live_trading_state(state)
            live_trading_state.update(state)

            logger.info(f"Live {action.upper()} executed for {symbol} at {close_price}, closed {total_volume} units")
            return jsonify({
                "status": "success",
                "action": action,
                "symbol": symbol,
                "price": close_price,
                "volume": total_volume,
                "profit": round(total_profit, 8)
            }), 200

    except Exception as e:
        logger.error(f"Live trading webhook error: {str(e)}")
        return jsonify({"status": "error", "message": f"Webhook processing failed: {str(e)}"}), 500