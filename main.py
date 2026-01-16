import asyncio
import time
import aiohttp
import hmac
import hashlib
import urllib.parse
import os
import socket
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

# ================= CONFIG =================
BINANCE_URL = "https://fapi.binance.com"
PAIR = "PEPEUSDT"

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

DEPOSIT = 5.0
LEVERAGE = 5
STEP_PCT = 0.002        # 0.2%
SCAN_INTERVAL = 5

MIN_NOTIONAL = 5.0

# ================= STATE =================
STATE = {
    "start_ts": time.time(),
    "running": False,
    "live": False,
    "deals": 0,
    "center": None,
    "last_price": None,
}

# ================= HELPERS =================
def server_ip():
    try:
        return socket.gethostbyname(socket.gethostname())
    except:
        return "unknown"

def sign(params):
    query = urllib.parse.urlencode(params)
    return hmac.new(
        API_SECRET.encode(),
        query.encode(),
        hashlib.sha256
    ).hexdigest()

async def get_price():
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f"{BINANCE_URL}/fapi/v1/ticker/price",
            params={"symbol": PAIR}
        ) as r:
            data = await r.json()
            return float(data["price"])

async def place_order(side, qty):
    if not STATE["live"]:
        return

    ts = int(time.time() * 1000)
    params = {
        "symbol": PAIR,
        "side": side,
        "type": "MARKET",
        "quantity": round(qty, 3),
        "timestamp": ts
    }
    params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": API_KEY}

    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"{BINANCE_URL}/fapi/v1/order",
            params=params,
            headers=headers
        ) as r:
            await r.json()

# ================= ENGINE =================
async def grid_loop():
    while True:
        if not STATE["running"]:
            await asyncio.sleep(1)
            continue

        price = await get_price()
        STATE["last_price"] = price

        if STATE["center"] is None:
            STATE["center"] = price
            await asyncio.sleep(SCAN_INTERVAL)
            continue

        step = STATE["center"] * STEP_PCT
        notional = DEPOSIT * LEVERAGE
        qty = notional / price

        if qty * price < MIN_NOTIONAL:
            await asyncio.sleep(SCAN_INTERVAL)
            continue

        # BUY below center
        if price <= STATE["center"] - step:
            await place_order("BUY", qty)
            STATE["center"] = price
            STATE["deals"] += 1

        # SELL above center
        elif price >= STATE["center"] + step:
            await place_order("SELL", qty)
            STATE["center"] = price
            STATE["deals"] += 1

        await asyncio.sleep(SCAN_INTERVAL)

# ================= WEB =================
app = FastAPI()

@app.on_event("startup")
async def startup():
    asyncio.create_task(grid_loop())

@app.post("/start")
def start():
    STATE["running"] = True
    return {"running": True}

@app.post("/stop")
def stop():
    STATE["running"] = False
    return {"running": False}

@app.post("/live")
def toggle_live():
    STATE["live"] = not STATE["live"]
    return {"live": STATE["live"]}

@app.get("/", response_class=HTMLResponse)
def dashboard():
    uptime = int((time.time() - STATE["start_ts"]) / 60)
    return f"""
    <html>
    <head>
        <title>GRID BOT — LIVE SAFE MODE</title>
        <style>
            body {{ background:#0f1116; color:#eee; font-family:Arial }}
            button {{ padding:10px; margin:5px; font-size:16px }}
        </style>
    </head>
    <body>
        <h2>🔥 GRID BOT — LIVE SAFE MODE</h2>
        <p><b>IP (add to Binance whitelist):</b> {server_ip()}</p>
        <p>Uptime: {uptime} min</p>
        <p>Deposit: ${DEPOSIT}</p>
        <p>Deals: {STATE["deals"]}</p>
        <p>RUNNING: {STATE["running"]}</p>
        <p>LIVE: {STATE["live"]}</p>
        <p>Center price: {STATE["center"]}</p>
        <p>Last price: {STATE["last_price"]}</p>

        <form action="/start" method="post"><button>START</button></form>
        <form action="/stop" method="post"><button>STOP</button></form>
        <form action="/live" method="post"><button>LIVE ON / OFF</button></form>
    </body>
    </html>
    """

# ================= RUN =================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)