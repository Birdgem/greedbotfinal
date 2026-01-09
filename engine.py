# engine.py
import asyncio
import time
import aiohttp
from statistics import mean
from fastapi import FastAPI
import uvicorn

# ================== SETTINGS ==================
ALL_PAIRS = [
    "SOLUSDT", "BNBUSDT",
    "DOGEUSDT", "TRXUSDT",
    "ADAUSDT", "XRPUSDT",
    "TONUSDT", "ARBUSDT",
    "OPUSDT"
]

TIMEFRAME = "15m"
BINANCE_URL = "https://api.binance.com/api/v3/klines"

DEPOSIT = 100.0
LEVERAGE = 10
MAX_GRIDS = 2
MAX_MARGIN_PER_GRID = 0.10

MAKER_FEE = 0.0002
TAKER_FEE = 0.0004

ATR_PERIOD = 14
SCAN_INTERVAL = 20
MIN_ORDER_NOTIONAL = 5.0

AUTO_MODE = True
MAX_AUTO_PAIRS = 4

# ================== STATE ==================
START_TS = time.time()

STATE = {
    "equity": DEPOSIT,
    "total_pnl": 0.0,
    "deals": 0,
    "active_pairs": ["SOLUSDT", "BNBUSDT"],
    "auto_pairs": [],
    "active_grids": {},
    "pair_stats": {},
    "last_reject": {}
}

# ================== FASTAPI ==================
app = FastAPI(title="Grid Bot Engine")

@app.get("/state")
def get_state():
    uptime = int((time.time() - START_TS) / 60)
    return {
        **STATE,
        "uptime_min": uptime,
        "timestamp": int(time.time())
    }

# ================== INDICATORS ==================
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

# ================== BINANCE ==================
async def get_klines(symbol, limit=120):
    async with aiohttp.ClientSession() as s:
        async with s.get(
            BINANCE_URL,
            params={"symbol": symbol, "interval": TIMEFRAME, "limit": limit}
        ) as r:
            d = await r.json()
            return d if isinstance(d, list) else []

# ================== AUTO PAIRS ==================
async def auto_select_pairs():
    scored = []

    for pair in ALL_PAIRS:
        kl = await get_klines(pair)
        if len(kl) < 50:
            STATE["last_reject"][pair] = "not enough candles"
            continue

        c = [float(k[4]) for k in kl]
        h = [float(k[2]) for k in kl]
        l = [float(k[3]) for k in kl]

        price = c[-1]
        a = atr(h, l, c)
        if not a:
            continue

        atr_pct = a / price * 100
        if price > 15 or atr_pct < 0.4 or atr_pct > 3.0:
            continue

        scored.append((pair, abs(atr_pct - 1.2)))

    scored.sort(key=lambda x: x[1])
    STATE["auto_pairs"] = [p for p, _ in scored[:MAX_AUTO_PAIRS]]

# ================== GRID ==================
def build_grid(price, atr_val):
    rng = atr_val * 2.5
    levels = 8

    low = price - rng
    high = price + rng
    step = (high - low) / levels

    margin = DEPOSIT * MAX_MARGIN_PER_GRID
    notional = margin * LEVERAGE
    qty = (notional / price) / levels

    orders = []
    for i in range(levels):
        entry = low + step * i
        exit = entry + step
        if entry * qty < MIN_ORDER_NOTIONAL:
            continue
        orders.append({"entry": entry, "exit": exit, "qty": qty, "open": False})

    return {
        "low": low,
        "high": high,
        "orders": orders,
        "atr": atr_val
    }

def calc_pnl(entry, exit, qty):
    gross = (exit - entry) * qty
    fees = (entry * qty * MAKER_FEE) + (exit * qty * TAKER_FEE)
    return gross - fees

# ================== ENGINE ==================
async def grid_loop():
    while True:
        if AUTO_MODE:
            await auto_select_pairs()

        all_pairs = list(set(STATE["active_pairs"] + STATE["auto_pairs"]))

        # --- update grids ---
        for pair, g in list(STATE["active_grids"].items()):
            kl = await get_klines(pair, 2)
            if not kl:
                continue

            price = float(kl[-1][4])

            if pair not in all_pairs or not (g["low"] <= price <= g["high"]):
                del STATE["active_grids"][pair]
                continue

            for o in g["orders"]:
                if not o["open"] and price <= o["entry"]:
                    o["open"] = True
                elif o["open"] and price >= o["exit"]:
                    pnl = calc_pnl(o["entry"], o["exit"], o["qty"])
                    STATE["total_pnl"] += pnl
                    STATE["equity"] = DEPOSIT + STATE["total_pnl"]
                    STATE["deals"] += 1

                    ps = STATE["pair_stats"].setdefault(pair, {"deals": 0, "pnl": 0.0})
                    ps["deals"] += 1
                    ps["pnl"] += pnl

                    o["open"] = False

        # --- start new grids ---
        if len(STATE["active_grids"]) < MAX_GRIDS:
            for pair in all_pairs:
                if pair in STATE["active_grids"]:
                    continue

                kl = await get_klines(pair)
                if len(kl) < 50:
                    continue

                c = [float(k[4]) for k in kl]
                h = [float(k[2]) for k in kl]
                l = [float(k[3]) for k in kl]

                price = c[-1]
                a = atr(h, l, c)
                if not a:
                    continue

                STATE["active_grids"][pair] = build_grid(price, a)

                if len(STATE["active_grids"]) >= MAX_GRIDS:
                    break

        await asyncio.sleep(SCAN_INTERVAL)

# ================== STARTUP ==================
@app.on_event("startup")
async def startup():
    asyncio.create_task(grid_loop())

# ================== ENTRY ==================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)