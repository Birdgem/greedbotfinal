# ====== engine.py ======

import os
import json
import asyncio
import aiohttp
import time
from statistics import mean
from datetime import datetime

# ================== FILES ==================
STATE_FILE = "state.json"

# ================== SETTINGS ==================
AUTO_MODE = True
MAX_AUTO_PAIRS = 4

ALL_PAIRS = [
    "SOLUSDT", "BNBUSDT",
    "DOGEUSDT", "TRXUSDT",
    "ADAUSDT", "XRPUSDT",
    "TONUSDT", "ARBUSDT",
    "OPUSDT"
]

# ручные пары — приоритет
ACTIVE_PAIRS = ["DOGEUSDT", "TONUSDT"]

TIMEFRAME = "15m"
BINANCE_URL = "https://api.binance.com/api/v3/klines"

# ---- DRY RUN ----
DEPOSIT = 100.0          # ВСЁ депо на бота
LEVERAGE = 10
MAX_GRIDS = 2
MAX_MARGIN_PER_GRID = 0.10   # 10% депо на одну сетку

MAKER_FEE = 0.0002
TAKER_FEE = 0.0004

ATR_PERIOD = 14
SCAN_INTERVAL = 20

MIN_ORDER_NOTIONAL = 5.0

# ================== STATE ==================
START_TS = time.time()

AUTO_SELECTED_PAIRS = []
ACTIVE_GRIDS = {}
LAST_REJECT_REASON = {}

TOTAL_PNL = 0.0
DEALS = 0

PAIR_STATS = {}

# ================== STATE IO ==================
def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump({
            "uptime_min": int((time.time() - START_TS) / 60),
            "deposit": DEPOSIT,
            "equity": round(DEPOSIT + TOTAL_PNL, 4),

            "active_pairs_manual": ACTIVE_PAIRS,
            "active_pairs_auto": AUTO_SELECTED_PAIRS,

            "active_grids": ACTIVE_GRIDS,
            "pair_stats": PAIR_STATS,

            "total_pnl": round(TOTAL_PNL, 4),
            "deals": DEALS,
            "last_reject_reason": LAST_REJECT_REASON
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
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
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

# ================== AUTO PAIR SELECTION ==================
async def auto_select_pairs():
    global AUTO_SELECTED_PAIRS
    scored = []

    for pair in ALL_PAIRS:
        kl = await get_klines(pair)
        if len(kl) < 50:
            LAST_REJECT_REASON[pair] = "not enough candles"
            continue

        c = [float(k[4]) for k in kl]
        h = [float(k[2]) for k in kl]
        l = [float(k[3]) for k in kl]

        price = c[-1]
        a = atr(h, l, c)

        if not a:
            LAST_REJECT_REASON[pair] = "ATR unavailable"
            continue

        atr_pct = a / price * 100

        # фильтры под грид
        if price > 20:
            LAST_REJECT_REASON[pair] = "price too high"
            continue

        if atr_pct < 0.4 or atr_pct > 3.0:
            LAST_REJECT_REASON[pair] = f"ATR {atr_pct:.2f}% bad"
            continue

        scored.append((pair, atr_pct))

    scored.sort(key=lambda x: abs(x[1] - 1.2))
    AUTO_SELECTED_PAIRS = [p for p, _ in scored[:MAX_AUTO_PAIRS]]

# ================== ANALYSIS ==================
async def analyze_pair(pair):
    kl = await get_klines(pair)
    if len(kl) < 50:
        return None

    c = [float(k[4]) for k in kl]
    h = [float(k[2]) for k in kl]
    l = [float(k[3]) for k in kl]

    price = c[-1]
    e7 = ema(c, 7)
    e25 = ema(c, 25)
    a = atr(h, l, c)

    if price > e7 > e25:
        side = "LONG"
    elif price < e7 < e25:
        side = "SHORT"
    else:
        side = "FLAT"

    return {"price": price, "side": side, "atr": a}

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
async def grid_engine():
    global TOTAL_PNL, DEALS

    while True:
        if AUTO_MODE:
            await auto_select_pairs()

        all_pairs = list(set(ACTIVE_PAIRS + AUTO_SELECTED_PAIRS))

        # ---- UPDATE GRIDS ----
        for pair, g in list(ACTIVE_GRIDS.items()):
            kl = await get_klines(pair, limit=2)
            if not kl:
                continue

            price = float(kl[-1][4])

            if pair not in all_pairs or not (g["low"] <= price <= g["high"]):
                del ACTIVE_GRIDS[pair]
                continue

            for o in g["orders"]:
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

        # ---- START NEW GRIDS ----
        if len(ACTIVE_GRIDS) < MAX_GRIDS:
            for pair in all_pairs:
                if pair in ACTIVE_GRIDS:
                    continue

                res = await analyze_pair(pair)
                if not res:
                    continue

                ACTIVE_GRIDS[pair] = build_grid(res["price"], res["atr"])
                save_state()

                if len(ACTIVE_GRIDS) >= MAX_GRIDS:
                    break

        await asyncio.sleep(SCAN_INTERVAL)

# ================== MAIN ==================
if __name__ == "__main__":
    print("GRID ENGINE STARTED")
    asyncio.run(grid_engine())
