import time
import hmac
import hashlib
import requests
import asyncio
import socket
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

# ================= CONFIG =================
SYMBOL = "PEPEUSDT"
BASE_URL = "https://fapi.binance.com"

ORDER_NOTIONAL = 5.0      # SAFE MODE
TP_PCT = 0.003            # 0.3%
SL_PCT = 0.003

# ================= API =================
import os
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

# ================= STATE =================
STATE = {
    "start_ts": time.time(),
    "running": False,
    "live": True,
    "position": None,
    "center_price": None,
    "last_price": None,
    "deals": [],
}

# ================= HELPERS =================
def get_ip():
    return socket.gethostbyname(socket.gethostname())

def sign(params: dict):
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    signature = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query + "&signature=" + signature

def headers():
    return {"X-MBX-APIKEY": API_KEY}

def get_price():
    r = requests.get(f"{BASE_URL}/fapi/v1/ticker/price", params={"symbol": SYMBOL})
    return float(r.json()["price"])

def place_order(side, qty):
    params = {
        "symbol": SYMBOL,
        "side": side,
        "type": "MARKET",
        "quantity": round(qty, 0),
        "timestamp": int(time.time() * 1000)
    }
    q = sign(params)
    r = requests.post(f"{BASE_URL}/fapi/v1/order?{q}", headers=headers())
    return r.json()

# ================= ENGINE =================
async def engine():
    while True:
        if not STATE["running"] or not STATE["live"]:
            await asyncio.sleep(2)
            continue

        price = get_price()
        STATE["last_price"] = price

        if STATE["center_price"] is None:
            STATE["center_price"] = price
            await asyncio.sleep(2)
            continue

        qty = ORDER_NOTIONAL / price

        # NO POSITION
        if STATE["position"] is None:
            if price < STATE["center_price"] * (1 - 0.001):
                r = place_order("BUY", qty)
                STATE["position"] = {
                    "side": "LONG",
                    "entry": price,
                    "tp": price * (1 + TP_PCT),
                    "sl": price * (1 - SL_PCT)
                }
                STATE["deals"].append(f"OPEN LONG @ {price:.8f}")

            elif price > STATE["center_price"] * (1 + 0.001):
                r = place_order("SELL", qty)
                STATE["position"] = {
                    "side": "SHORT",
                    "entry": price,
                    "tp": price * (1 - TP_PCT),
                    "sl": price * (1 + SL_PCT)
                }
                STATE["deals"].append(f"OPEN SHORT @ {price:.8f}")

        # MANAGE POSITION
        else:
            pos = STATE["position"]

            if pos["side"] == "LONG":
                if price >= pos["tp"] or price <= pos["sl"]:
                    place_order("SELL", qty)
                    STATE["deals"].append(f"CLOSE LONG @ {price:.8f}")
                    STATE["position"] = None
                    STATE["center_price"] = price

            if pos["side"] == "SHORT":
                if price <= pos["tp"] or price >= pos["sl"]:
                    place_order("BUY", qty)
                    STATE["deals"].append(f"CLOSE SHORT @ {price:.8f}")
                    STATE["position"] = None
                    STATE["center_price"] = price

        await asyncio.sleep(2)

# ================= WEB =================
app = FastAPI()

@app.on_event("startup")
async def start():
    asyncio.create_task(engine())

@app.post("/start")
def start_bot():
    STATE["running"] = True
    return {"status": "started"}

@app.post("/stop")
def stop_bot():
    STATE["running"] = False
    return {"status": "stopped"}

@app.post("/live")
def toggle_live():
    STATE["live"] = not STATE["live"]
    return {"live": STATE["live"]}

@app.get("/", response_class=HTMLResponse)
def dashboard():
    uptime = int((time.time() - STATE["start_ts"]) / 60)
    logs = "<br>".join(STATE["deals"][-20:])

    return f"""
    <html>
    <body style="background:#0f1116;color:#eee;font-family:Arial">
    <h2>🔥 GRID BOT — LIVE SAFE MODE</h2>

    <p><b>IP (add to Binance whitelist):</b> {get_ip()}</p>
    <p>Uptime: {uptime} min</p>
    <p>Deposit: {ORDER_NOTIONAL}$</p>
    <p>RUNNING: {STATE["running"]}</p>
    <p>LIVE: {STATE["live"]}</p>
    <p>Center price: {STATE["center_price"]}</p>
    <p>Last price: {STATE["last_price"]}</p>

    <form action="/start" method="post"><button>START</button></form>
    <form action="/stop" method="post"><button>STOP</button></form>
    <form action="/live" method="post"><button>LIVE ON / OFF</button></form>

    <h3>Deal log</h3>
    <div style="font-size:14px">{logs}</div>
    </body>
    </html>
    """

# ================= RUN =================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)