import os
import json
import asyncio
import aiohttp
import time
from statistics import mean

# ================== STORAGE ==================
STATE_FILE = "/data/state.json"

# ================== SETTINGS ==================
ALL_PAIRS = [
    "SOLUSDT", "BNBUSDT", "DOGEUSDT",
    "TRXUSDT", "ADAUSDT", "XRPUSDT",
    "TONUSDT", "ARBUSDT", "OPUSDT"
]

TIMEFRAME = "15m"
BINANCE_URL = "https://api.binance.com/api/v3/klines"

DEPOSIT = 100.0              # 🔒 ОБЩЕЕ депо
LEVERAGE = 10
MAX_GRIDS = 2
MAX_MARGIN_PER_GRID = 0.10   # 10% депо на сетку

ATR_PERIOD = 14
SCAN_INTERVAL = 20

MIN_ORDER_NOTIONAL = 5.0

# ================== STATE ==================
START_TS = time.time()

ACTIVE_PAIRS = ["SOLUSDT", "BNBUSDT"]
ACTIVE_GRIDS = {}
PAIR_STATS = {}

TOTAL_PNL = 0.0
DEALS = 0

# ================== STATE IO ==================
def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump({
            "uptime_min": int((time.time() - START_TS) / 60),
            "active_pairs": ACTIVE_PAIRS,
            "active_grids": list(ACTIVE_GRIDS.keys()),
            "total_pnl": TOTAL_PNL,
            "deals": DEALS,
            "pair_stats": PAIR_STATS
        }, f, indent=2)

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
            data = await r.json()
            return data if isinstance(data, list) else []

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
        orders.append({
            "entry": entry,
            "exit": exit,
            "qty": qty,
            "open": False
        })

    return {"low": low, "high": high, "orders": orders}

def calc_pnl(entry, exit, qty):
    return (exit - entry) * qty

# ================== ENGINE ==================
async def grid_engine():
    global TOTAL_PNL, DEALS

    while True:
        for pair in ACTIVE_PAIRS:
            if pair not in ACTIVE_GRIDS:
                kl = await get_klines(pair)
                if len(kl) < 50:
                    continue

                c = [float(k[4]) for k in kl]
                h = [float(k[2]) for k in kl]
                l = [float(k[3]) for k in kl]

                a = atr(h, l, c)
                if not a:
                    continue

                ACTIVE_GRIDS[pair] = build_grid(c[-1], a)

            grid = ACTIVE_GRIDS[pair]
            kl = await get_klines(pair, 2)
            price = float(kl[-1][4])

            if not (grid["low"] <= price <= grid["high"]):
                del ACTIVE_GRIDS[pair]
                continue

            for o in grid["orders"]:
                if not o["open"] and price <= o["entry"]:
                    o["open"] = True
                elif o["open"] and price >= o["exit"]:
                    pnl = calc_pnl(o["entry"], o["exit"], o["qty"])
                    TOTAL_PNL += pnl
                    DEALS += 1

                    PAIR_STATS.setdefault(pair, {"pnl": 0.0, "deals": 0})
                    PAIR_STATS[pair]["pnl"] += pnl
                    PAIR_STATS[pair]["deals"] += 1

                    o["open"] = False

        save_state()
        await asyncio.sleep(SCAN_INTERVAL)
