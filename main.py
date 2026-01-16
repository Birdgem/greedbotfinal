import asyncio
import time
import hmac
import hashlib
import os
import aiohttp
from urllib.parse import urlencode

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

# ================== CONFIG ==================
SYMBOL = "1000PEPEUSDT"
BINANCE_BASE = "https://fapi.binance.com"

API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")

DEPOSIT_USDT = 5.0
LEVERAGE = 10
STEP_PCT = 0.002     # 0.2%
SCAN_INTERVAL = 5

# ================== STATE ==================
STATE = {
    "start_ts": time.time(),
    "running": True,
    "live": False,
    "center_price": None,
    "last_price": None,
    "position": None,   # "LONG" / "SHORT" / None
    "deal_log": []
}

# ================== BINANCE HELPERS ==================
def sign(params: dict) -> str:
    query = urlencode(params)
    signature = hmac.new(
        API_SECRET.encode(),
        query.encode(),
        hashlib.sha256
    ).hexdigest()
    return query + "&signature=" + signature

async def binance_request(method, path, params=None, signed=False):
    if params is None:
        params = {}

    headers = {"X-MBX-APIKEY": API_KEY} if signed else {}

    if signed:
        params["timestamp"] = int(time.time() * 1000)
        query = sign(params)
    else:
        query = urlencode(params)

    url = f"{BINANCE_BASE}{path}?{query}"

    async with aiohttp.ClientSession() as s:
        async with s.request(method, url, headers=headers) as r:
            return await r.json()

# ================== MARKET ==================
async def get_price():
    data = await binance_request(
        "GET", "/fapi/v1/ticker/price", {"symbol": SYMBOL}
    )
    return float(data["price"])

async def open_market(side: str, qty: float):
    if not STATE["live"]:
        STATE["deal_log"].append(f"[SIM] {side} {qty}")
        return

    res = await binance_request(
        "POST",
        "/fapi/v1/order",
        {
            "symbol": SYMBOL,
            "side": side,
            "type": "MARKET",
            "quantity": qty
        },
        signed=True
    )
    STATE["deal_log"].append(str(res))

# ================== ENGINE ==================
async def engine():
    while True:
        if not STATE["running"]:
            await asyncio.sleep(1)
            continue

        try:
            price = await get_price()
            STATE["last_price"] = price

            if STATE["center_price"] is None:
                STATE["center_price"] = price
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            step = STATE["center_price"] * STEP_PCT
            qty = round((DEPOSIT_USDT * LEVERAGE) / price, 0)

            # SHORT
            if STATE["position"] is None and price >= STATE["center_price"] + step:
                STATE["position"] = "SHORT"
                await open_market("SELL", qty)
                STATE["deal_log"].append(f"OPEN SHORT @ {price}")

            # LONG
            elif STATE["position"] is None and price <= STATE["center_price"] - step:
                STATE["position"] = "LONG"
                await open_market("BUY", qty)
                STATE["deal_log"].append(f"OPEN LONG @ {price}")

            # RESET GRID
            if abs(price - STATE["center_price"]) > step * 3:
                STATE["center_price"] = price
                STATE["position"] = None

        except Exception as e:
            STATE["deal_log"].append(f"ERR: {e}")

        await asyncio.sleep(SCAN_INTERVAL)

# ================== WEB ==================
app = FastAPI()

@app.on_event("startup")
async def startup():
    asyncio.create_task(engine())

@app.post("/start")
def start():
    STATE["running"] = True

@app.post("/stop")
def stop():
    STATE["running"] = False

@app.post("/live")
def toggle_live():
    STATE["live"] = not STATE["live"]

@app.get("/", response_class=HTMLResponse)
def dashboard():
    uptime = int((time.time() - STATE["start_ts"]) / 60)

    return f"""
    <html>
    <head>
        <title>GRID BOT — LIVE SAFE MODE</title>
        <style>
            body {{ background:#0f1116; color:#eee; font-family:Arial }}
            button {{ margin:5px; padding:6px }}
        </style>
    </head>
    <body>
        <h2>🔥 GRID BOT — LIVE SAFE MODE</h2>

        <p><b>IP (add to Binance whitelist):</b> {{request.client.host}}</p>
        <p>Uptime: {uptime} min</p>
        <p>Deposit: {DEPOSIT_USDT}$</p>
        <p>RUNNING: {STATE["running"]}</p>
        <p>LIVE: {STATE["live"]}</p>
        <p>Center price: {STATE["center_price"]}</p>
        <p>Last price: {STATE["last_price"]}</p>

        <form method="post" action="/start"><button>START</button></form>
        <form method="post" action="/stop"><button>STOP</button></form>
        <form method="post" action="/live"><button>LIVE ON / OFF</button></form>

        <h3>Deal log</h3>
        <pre>{"\\n".join(STATE["deal_log"][-20:])}</pre>
    </body>
    </html>
    """

# ================== RUN ==================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)