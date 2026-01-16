import asyncio
import time
import hmac
import hashlib
import aiohttp
import socket
from statistics import mean
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
import uvicorn

# ================== BINANCE ==================
BINANCE_FAPI = "https://fapi.binance.com"
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"

API_KEY = "PASTE_API_KEY_HERE"
API_SECRET = "PASTE_API_SECRET_HERE"

# ================== CONFIG ==================
TIMEFRAME = "5m"

ALLOWED_PAIRS = ["PEPEUSDT"]   # ⛔ ОДНА ПАРА
LEVERAGE = 1                  # 🔥 НЕ БОЛЬШЕ 2
MAX_POSITION_USDT = 5.0        # 💵 LIVE на $5
SCAN_INTERVAL = 20

ATR_PERIOD = 14
MAKER_FEE = 0.0002
TAKER_FEE = 0.0004
MIN_NOTIONAL = 5.0

# ================== STATE ==================
STATE = {
    "start_ts": time.time(),
    "deposit": 5.0,
    "total_pnl": 0.0,
    "deals": 0,
    "running": False,
    "live": False,
    "pair_stats": {}
}

# ================== HELPERS ==================
def sign(params):
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hmac.new(
        API_SECRET.encode(),
        query.encode(),
        hashlib.sha256
    ).hexdigest()

def get_ip():
    try:
        return socket.gethostbyname(socket.gethostname())
    except:
        return "unknown"

def ema(data, p):
    k = 2 / (p + 1)
    e = sum(data[:p]) / p
    for x in data[p:]:
        e = x * k + e * (1 - k)
    return e

def atr(highs, lows, closes):
    tr = []
    for i in range(1, len(closes)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        ))
    return mean(tr[-ATR_PERIOD:]) if len(tr) >= ATR_PERIOD else None

async def get_klines(symbol, limit=120):
    async with aiohttp.ClientSession() as s:
        async with s.get(
            BINANCE_KLINES,
            params={"symbol": symbol, "interval": TIMEFRAME, "limit": limit}
        ) as r:
            d = await r.json()
            return d if isinstance(d, list) else []

async def place_order(symbol, side, qty):
    if not STATE["live"]:
        return

    ts = int(time.time() * 1000)
    params = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": qty,
        "timestamp": ts
    }
    params["signature"] = sign(params)

    headers = {"X-MBX-APIKEY": API_KEY}

    async with aiohttp.ClientSession() as s:
        async with s.post(
            BINANCE_FAPI + "/fapi/v1/order",
            params=params,
            headers=headers
        ) as r:
            return await r.json()

# ================== ENGINE ==================
async def engine_loop():
    while True:
        if not STATE["running"]:
            await asyncio.sleep(1)
            continue

        for pair in ALLOWED_PAIRS:
            kl = await get_klines(pair, 120)
            if len(kl) < 60:
                continue

            closes = [float(k[4]) for k in kl]
            highs = [float(k[2]) for k in kl]
            lows = [float(k[3]) for k in kl]

            price = closes[-1]
            a = atr(highs, lows, closes)
            if not a:
                continue

            ema21 = ema(closes, 21)
            ema50 = ema(closes, 50)

            # 📌 ПРОСТОЙ ФИЛЬТР
            if ema21 > ema50:
                side = "BUY"
            elif ema21 < ema50:
                side = "SELL"
            else:
                continue

            qty = round(MAX_POSITION_USDT / price, 3)
            if qty * price < MIN_NOTIONAL:
                continue

            await place_order(pair, side, qty)

            STATE["deals"] += 1
            ps = STATE["pair_stats"].setdefault(pair, {"deals": 0})
            ps["deals"] += 1

            await asyncio.sleep(SCAN_INTERVAL)

        await asyncio.sleep(1)

# ================== WEB ==================
app = FastAPI()

@app.on_event("startup")
async def startup():
    asyncio.create_task(engine_loop())

@app.post("/start")
def start_bot():
    STATE["running"] = True

@app.post("/stop")
def stop_bot():
    STATE["running"] = False

@app.post("/live")
def toggle_live():
    STATE["live"] = not STATE["live"]

@app.get("/", response_class=HTMLResponse)
def dashboard():
    uptime = int((time.time() - STATE["start_ts"]) / 60)
    ip = get_ip()

    rows = ""
    for p, s in STATE["pair_stats"].items():
        rows += f"<tr><td>{p}</td><td>{s['deals']}</td></tr>"

    return f"""
    <html>
    <head>
        <title>GRID BOT — LIVE $5</title>
        <style>
            body {{ background:#0f1116; color:#eee; font-family:Arial }}
            button {{ padding:10px; margin:5px }}
            table {{ width:100%; border-collapse:collapse }}
            td,th {{ border:1px solid #333; padding:6px }}
        </style>
    </head>
    <body>
        <h2>🔥 GRID BOT — LIVE SAFE MODE</h2>

        <p><b>IP (add to Binance whitelist):</b> {ip}</p>
        <p>Uptime: {uptime} min</p>
        <p>Deposit: ${STATE["deposit"]}</p>
        <p>Deals: {STATE["deals"]}</p>
        <p>RUNNING: {STATE["running"]}</p>
        <p>LIVE: {STATE["live"]}</p>

        <form method="post" action="/start">
            <button>START</button>
        </form>
        <form method="post" action="/stop">
            <button>STOP</button>
        </form>
        <form method="post" action="/live">
            <button>LIVE ON / OFF</button>
        </form>

        <h3>Stats</h3>
        <table>
            <tr><th>Pair</th><th>Deals</th></tr>
            {rows or "<tr><td colspan=2>нет данных</td></tr>"}
        </table>
    </body>
    </html>
    """

# ================== RUN ==================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)