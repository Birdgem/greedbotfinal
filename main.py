import asyncio
import time
import aiohttp
import socket
from statistics import mean
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn

# ================== CONFIG ==================
BINANCE_URL = "https://api.binance.com/api/v3/klines"
TIMEFRAME = "5m"

ALL_PAIRS = [
    "SOLUSDT", "BNBUSDT",
    "DOGEUSDT", "ADAUSDT", "XRPUSDT",
    "PEPEUSDT", "BONKUSDT", "FLOKIUSDT",
    "1000SATSUSDT", "WIFUSDT"
]

MAX_AUTO_PAIRS = 5
LEVERAGE = 10
MAX_GRIDS = 2
MAX_MARGIN_PER_GRID = 0.12

ATR_PERIOD = 14
SCAN_INTERVAL = 15

MAKER_FEE = 0.0002
TAKER_FEE = 0.0004
MIN_ORDER_NOTIONAL = 5.0

# ================== STATE ==================
STATE = {
    "start_ts": time.time(),
    "deposit": 100.0,
    "total_pnl": 0.0,
    "deals": 0,
    "running": False,
    "engine_task": None,
    "active_pairs": ["SOLUSDT", "BNBUSDT"],
    "auto_pairs": [],
    "active_grids": {},
    "pair_stats": {}
}

# ================== HELPERS ==================
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
            BINANCE_URL,
            params={"symbol": symbol, "interval": TIMEFRAME, "limit": limit}
        ) as r:
            return await r.json()

def calc_pnl(entry, exit, qty):
    gross = (exit - entry) * qty
    fees = (entry * qty * MAKER_FEE) + (exit * qty * TAKER_FEE)
    return gross - fees

async def get_public_ip():
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.ipify.org") as r:
                return await r.text()
    except:
        return "unknown"

# ================== GRID ==================
def adaptive_step(price, atr_pct):
    if atr_pct < 1.0:
        return price * 0.0012
    elif atr_pct < 2.0:
        return price * 0.002
    else:
        return price * 0.0035

def build_grid(price, atr_val):
    atr_pct = atr_val / price * 100
    step = adaptive_step(price, atr_pct)
    levels = 14

    margin = STATE["deposit"] * MAX_MARGIN_PER_GRID
    notional = margin * LEVERAGE
    qty = (notional / price) / levels

    orders = []
    for i in range(1, levels + 1):
        entry = price - step * i
        exit = entry + step
        if entry * qty >= MIN_ORDER_NOTIONAL:
            orders.append({
                "entry": entry,
                "exit": exit,
                "qty": qty,
                "open": False
            })

    return {"orders": orders, "step": step, "center": price}

# ================== ENGINE ==================
async def engine_loop():
    while STATE["running"]:
        for pair in STATE["active_pairs"]:
            kl = await get_klines(pair, 120)
            if len(kl) < 50:
                continue

            closes = [float(k[4]) for k in kl]
            highs = [float(k[2]) for k in kl]
            lows = [float(k[3]) for k in kl]

            price = closes[-1]
            a = atr(highs, lows, closes)
            if not a:
                continue

            if pair not in STATE["active_grids"]:
                STATE["active_grids"][pair] = build_grid(price, a)

            g = STATE["active_grids"][pair]

            # плавающий центр
            if abs(price - g["center"]) > g["step"] * 3:
                STATE["active_grids"][pair] = build_grid(price, a)
                continue

            for o in g["orders"]:
                if not o["open"] and price <= o["entry"]:
                    o["open"] = True
                elif o["open"] and price >= o["exit"]:
                    pnl = calc_pnl(o["entry"], o["exit"], o["qty"])
                    STATE["total_pnl"] += pnl
                    STATE["deals"] += 1

                    ps = STATE["pair_stats"].setdefault(pair, {"pnl": 0.0, "deals": 0})
                    ps["pnl"] += pnl
                    ps["deals"] += 1

                    o["open"] = False

        await asyncio.sleep(SCAN_INTERVAL)

# ================== WEB ==================
app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    uptime = int((time.time() - STATE["start_ts"]) / 60)
    equity = STATE["deposit"] + STATE["total_pnl"]
    ip = await get_public_ip()

    rows = ""
    for p, s in STATE["pair_stats"].items():
        avg = s["pnl"] / s["deals"]
        rows += f"<tr><td>{p}</td><td>{s['deals']}</td><td>{s['pnl']:.2f}$</td><td>{avg:.3f}$</td></tr>"

    status = "🟢 RUNNING" if STATE["running"] else "🔴 STOPPED"

    return f"""
    <html>
    <head>
        <title>GRID BOT — LIVE</title>
        <style>
            body {{ background:#0f1116; color:#eee; font-family:Arial }}
            button {{ padding:10px; margin:5px }}
            table {{ border-collapse:collapse; width:100% }}
            td,th {{ border:1px solid #333; padding:6px }}
        </style>
    </head>
    <body>
        <h2>🔥 GRID BOT — LIVE</h2>
        <p>Status: <b>{status}</b></p>
        <p>Uptime: {uptime} min</p>
        <p>Equity: {equity:.2f}$ | PnL: {STATE["total_pnl"]:.2f}$</p>
        <p>Deals: {STATE["deals"]}</p>
        <p><b>Server IP (for Binance whitelist):</b> {ip}</p>

        <h3>Control</h3>
        <form action="/start" method="post">
            <button type="submit">▶ START</button>
        </form>
        <form action="/stop" method="post">
            <button type="submit">⏹ STOP</button>
        </form>

        <h3>Deposit</h3>
        <form action="/deposit" method="post">
            <input name="amount" value="{STATE['deposit']}" />
            <button type="submit">Update</button>
        </form>

        <h3>Stats</h3>
        <table>
            <tr><th>Pair</th><th>Deals</th><th>PnL</th><th>Avg</th></tr>
            {rows or "<tr><td colspan=4>нет данных</td></tr>"}
        </table>
    </body>
    </html>
    """

@app.post("/start")
async def start():
    if not STATE["running"]:
        STATE["running"] = True
        STATE["engine_task"] = asyncio.create_task(engine_loop())
    return RedirectResponse("/", status_code=303)

@app.post("/stop")
async def stop():
    STATE["running"] = False
    return RedirectResponse("/", status_code=303)

@app.post("/deposit")
async def set_deposit(amount: float = Form(...)):
    STATE["deposit"] = float(amount)
    return RedirectResponse("/", status_code=303)

# ================== RUN ==================
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)