import asyncio
import time
import aiohttp
from statistics import mean
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn

# ================== CONFIG ==================
BINANCE_URL = "https://api.binance.com/api/v3/klines"
TIMEFRAME = "5m"

ALL_PAIRS = [
    "SOLUSDT", "BNBUSDT", "DOGEUSDT", "TRXUSDT",
    "ADAUSDT", "XRPUSDT", "TONUSDT", "ARBUSDT",
    "OPUSDT", "PEPEUSDT", "BONKUSDT", "FLOKIUSDT",
    "1000SATSUSDT", "WIFUSDT"
]

AUTO_MODE = True
MAX_AUTO_PAIRS = 6

LEVERAGE = 10
MAX_GRIDS = 3
MAX_MARGIN_PER_GRID = 0.12

ATR_PERIOD = 14
SCAN_INTERVAL = 15

MAKER_FEE = 0.0002
TAKER_FEE = 0.0004
MIN_ORDER_NOTIONAL = 5.0

# ================== STATE ==================
STATE = {
    "start_ts": time.time(),
    "engine_enabled": False,      # 🔘 START / STOP
    "deposit": 100.0,
    "total_pnl": 0.0,
    "deals": 0,
    "active_pairs": ["SOLUSDT", "BNBUSDT"],
    "auto_pairs": [],
    "active_grids": {},
    "pair_stats": {},
    "public_ip": "detecting..."
}

# ================== HELPERS ==================
def ema(data, period):
    k = 2 / (period + 1)
    e = sum(data[:period]) / period
    for x in data[period:]:
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
            BINANCE_URL,
            params={"symbol": symbol, "interval": TIMEFRAME, "limit": limit}
        ) as r:
            d = await r.json()
            return d if isinstance(d, list) else []

def calc_pnl(entry, exit, qty):
    gross = (exit - entry) * qty
    fees = (entry * qty * MAKER_FEE) + (exit * qty * TAKER_FEE)
    return gross - fees

# ================== PUBLIC IP ==================
async def fetch_public_ip():
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.ipify.org?format=json") as r:
                data = await r.json()
                STATE["public_ip"] = data.get("ip", "unknown")
    except:
        STATE["public_ip"] = "unavailable"

# ================== AUTO SELECT ==================
async def auto_select_pairs():
    scored = []

    for pair in ALL_PAIRS:
        kl = await get_klines(pair)
        if len(kl) < 60:
            continue

        closes = [float(k[4]) for k in kl]
        highs = [float(k[2]) for k in kl]
        lows = [float(k[3]) for k in kl]

        price = closes[-1]
        a = atr(highs, lows, closes)
        if not a:
            continue

        atr_pct = a / price * 100

        if price > 20:
            continue
        if not (0.5 <= atr_pct <= 5.0):
            continue

        scored.append((pair, abs(atr_pct - 1.5)))

    scored.sort(key=lambda x: x[1])
    STATE["auto_pairs"] = [p for p, _ in scored[:MAX_AUTO_PAIRS]]

# ================== GRID ==================
def adaptive_step(price, atr_pct):
    if atr_pct < 1.0:
        return price * 0.0012
    elif atr_pct < 2.0:
        return price * 0.0020
    else:
        return price * 0.0035

def build_grid(price, center, atr_val, trend):
    atr_pct = atr_val / price * 100
    step = adaptive_step(price, atr_pct)
    levels = 20

    margin = STATE["deposit"] * MAX_MARGIN_PER_GRID
    notional = margin * LEVERAGE
    qty = (notional / price) / levels

    longs, shorts = [], []

    for i in range(1, levels + 1):
        le = center - step * i
        lx = le + step

        se = center + step * i
        sx = se - step

        if trend != "down" and le * qty >= MIN_ORDER_NOTIONAL:
            longs.append({"side": "long", "entry": le, "exit": lx, "qty": qty, "open": False})

        if trend != "up" and se * qty >= MIN_ORDER_NOTIONAL:
            shorts.append({"side": "short", "entry": se, "exit": sx, "qty": qty, "open": False})

    return {"center": center, "step": step, "longs": longs, "shorts": shorts}

# ================== ENGINE ==================
async def engine_loop():
    while True:
        if not STATE["engine_enabled"]:
            await asyncio.sleep(1)
            continue

        if AUTO_MODE:
            await auto_select_pairs()

        all_pairs = list(set(STATE["active_pairs"] + STATE["auto_pairs"]))

        for pair, g in list(STATE["active_grids"].items()):
            kl = await get_klines(pair, 60)
            if not kl:
                continue

            closes = [float(k[4]) for k in kl]
            price = closes[-1]

            for o in g["longs"] + g["shorts"]:
                if not o["open"]:
                    if o["side"] == "long" and price <= o["entry"]:
                        o["open"] = True
                    elif o["side"] == "short" and price >= o["entry"]:
                        o["open"] = True
                else:
                    if o["side"] == "long" and price >= o["exit"]:
                        pnl = calc_pnl(o["entry"], o["exit"], o["qty"])
                    elif o["side"] == "short" and price <= o["exit"]:
                        pnl = calc_pnl(o["exit"], o["entry"], o["qty"])
                    else:
                        continue

                    STATE["total_pnl"] += pnl
                    STATE["deals"] += 1
                    ps = STATE["pair_stats"].setdefault(pair, {"pnl": 0.0, "deals": 0})
                    ps["pnl"] += pnl
                    ps["deals"] += 1
                    o["open"] = False

        if len(STATE["active_grids"]) < MAX_GRIDS:
            for pair in all_pairs:
                if pair in STATE["active_grids"]:
                    continue

                kl = await get_klines(pair)
                if len(kl) < 60:
                    continue

                closes = [float(k[4]) for k in kl]
                highs = [float(k[2]) for k in kl]
                lows = [float(k[3]) for k in kl]

                a = atr(highs, lows, closes)
                if not a:
                    continue

                ema21 = ema(closes, 21)
                ema50 = ema(closes, 50)
                ema200 = ema(closes, 200)

                trend = "flat"
                if ema50 > ema200:
                    trend = "up"
                elif ema50 < ema200:
                    trend = "down"

                STATE["active_grids"][pair] = build_grid(closes[-1], ema21, a, trend)
                if len(STATE["active_grids"]) >= MAX_GRIDS:
                    break

        await asyncio.sleep(SCAN_INTERVAL)

# ================== WEB ==================
app = FastAPI()

@app.on_event("startup")
async def startup():
    asyncio.create_task(fetch_public_ip())
    asyncio.create_task(engine_loop())

@app.post("/start")
def start_bot():
    STATE["engine_enabled"] = True
    return RedirectResponse("/", status_code=303)

@app.post("/stop")
def stop_bot():
    STATE["engine_enabled"] = False
    STATE["active_grids"].clear()
    return RedirectResponse("/", status_code=303)

@app.post("/deposit")
def update_deposit(amount: float = Form(...)):
    STATE["deposit"] = max(1.0, amount)
    STATE["active_grids"].clear()
    return RedirectResponse("/", status_code=303)

@app.get("/", response_class=HTMLResponse)
def dashboard():
    uptime = int((time.time() - STATE["start_ts"]) / 60)
    equity = STATE["deposit"] + STATE["total_pnl"]

    rows = ""
    for p, s in STATE["pair_stats"].items():
        avg = s["pnl"] / s["deals"]
        rows += f"<tr><td>{p}</td><td>{s['deals']}</td><td>{s['pnl']:.2f}$</td><td>{avg:.3f}$</td></tr>"

    status = "🟢 RUNNING" if STATE["engine_enabled"] else "🔴 STOPPED"

    return f"""
    <html>
    <head>
        <title>GRID BOT — CONTROL</title>
        <style>
            body {{ background:#0f1116; color:#eee; font-family:Arial }}
            button {{ padding:8px 14px; margin:4px }}
            input {{ padding:6px }}
            table {{ border-collapse:collapse; width:100% }}
            td,th {{ border:1px solid #333; padding:6px }}
        </style>
    </head>
    <body>
        <h2>🤖 GRID BOT</h2>
        <p><b>Status:</b> {status}</p>
        <p><b>Server IP:</b> {STATE["public_ip"]}</p>

        <form method="post" action="/start"><button>▶ START</button></form>
        <form method="post" action="/stop"><button>⏹ STOP</button></form>

        <form method="post" action="/deposit">
            <input name="amount" type="number" step="1" value="{STATE["deposit"]}">
            <button>💰 Update Deposit</button>
        </form>

        <p>Uptime: {uptime} min</p>
        <p>Equity: {equity:.2f}$ | PnL: {STATE["total_pnl"]:.2f}$</p>
        <p>Deals: {STATE["deals"]}</p>

        <h3>Pairs</h3>
        <p>Manual: {", ".join(STATE["active_pairs"])}</p>
        <p>Auto: {", ".join(STATE["auto_pairs"])}</p>

        <h3>Stats</h3>
        <table>
            <tr><th>Pair</th><th>Deals</th><th>PnL</th><th>Avg</th></tr>
            {rows or "<tr><td colspan=4>no data</td></tr>"}
        </table>
    </body>
    </html>
    """

# ================== RUN ==================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)