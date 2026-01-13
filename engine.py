import asyncio
import time
import aiohttp
from statistics import mean
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

# ================== CONFIG ==================
BINANCE_URL = "https://api.binance.com/api/v3/klines"
TIMEFRAME = "5m"

ALL_PAIRS = [
    "SOLUSDT", "BNBUSDT",
    "DOGEUSDT", "TRXUSDT",
    "ADAUSDT", "XRPUSDT",
    "TONUSDT", "ARBUSDT",
    "OPUSDT", "PEPEUSDT",
    "BONKUSDT", "FLOKIUSDT",
    "1000SATSUSDT", "WIFUSDT"
]

AUTO_MODE = True
MAX_AUTO_PAIRS = 6

DEPOSIT = 100.0
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
    "deposit": DEPOSIT,
    "total_pnl": 0.0,
    "deals": 0,
    "active_pairs": ["SOLUSDT", "BNBUSDT"],
    "auto_pairs": [],
    "active_grids": {},   # pair -> grid
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
            d = await r.json()
            return d if isinstance(d, list) else []

def calc_pnl(entry, exit, qty):
    gross = (exit - entry) * qty
    fees = (entry * qty * MAKER_FEE) + (exit * qty * TAKER_FEE)
    return gross - fees

# ================== AUTO SELECT ==================
async def auto_select_pairs():
    scored = []

    for pair in ALL_PAIRS:
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
        return price * 0.0012   # 0.12%
    elif atr_pct < 2.0:
        return price * 0.0020   # 0.20%
    else:
        return price * 0.0035   # 0.35%

def build_neutral_grid(price, atr_val):
    atr_pct = atr_val / price * 100
    step = adaptive_step(price, atr_pct)
    levels = 20

    margin = STATE["deposit"] * MAX_MARGIN_PER_GRID
    notional = margin * LEVERAGE
    qty = (notional / price) / levels

    long_orders = []
    short_orders = []

    for i in range(1, levels + 1):
        long_entry = price - step * i
        long_exit = long_entry + step

        short_entry = price + step * i
        short_exit = short_entry - step

        if long_entry * qty >= MIN_ORDER_NOTIONAL:
            long_orders.append({
                "side": "long",
                "entry": long_entry,
                "exit": long_exit,
                "qty": qty,
                "open": False
            })

        if short_entry * qty >= MIN_ORDER_NOTIONAL:
            short_orders.append({
                "side": "short",
                "entry": short_entry,
                "exit": short_exit,
                "qty": qty,
                "open": False
            })

    return {
        "longs": long_orders,
        "shorts": short_orders,
        "step": step,
        "atr_pct": atr_pct
    }

# ================== ENGINE ==================
async def engine_loop():
    while True:
        if AUTO_MODE:
            await auto_select_pairs()

        all_pairs = list(set(STATE["active_pairs"] + STATE["auto_pairs"]))

        # === UPDATE GRIDS ===
        for pair, g in list(STATE["active_grids"].items()):
            kl = await get_klines(pair, 2)
            if not kl:
                continue

            price = float(kl[-1][4])

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

        # === START NEW GRIDS ===
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

                a = atr(h, l, c)
                if not a:
                    continue

                STATE["active_grids"][pair] = build_neutral_grid(c[-1], a)
                if len(STATE["active_grids"]) >= MAX_GRIDS:
                    break

        await asyncio.sleep(SCAN_INTERVAL)

# ================== WEB ==================
app = FastAPI()

@app.on_event("startup")
async def startup():
    asyncio.create_task(engine_loop())

@app.get("/", response_class=HTMLResponse)
def dashboard():
    uptime = int((time.time() - STATE["start_ts"]) / 60)
    equity = STATE["deposit"] + STATE["total_pnl"]

    rows = ""
    for p, s in STATE["pair_stats"].items():
        avg = s["pnl"] / s["deals"]
        rows += f"<tr><td>{p}</td><td>{s['deals']}</td><td>{s['pnl']:.2f}$</td><td>{avg:.3f}$</td></tr>"

    return f"""
    <html>
    <head>
        <title>GRID BOT — NEUTRAL</title>
        <style>
            body {{ background:#0f1116; color:#eee; font-family:Arial }}
            table {{ border-collapse:collapse; width:100% }}
            td,th {{ border:1px solid #333; padding:6px }}
        </style>
    </head>
    <body>
        <h2>🔥 GRID BOT — LONG + SHORT</h2>
        <p>Uptime: {uptime} min</p>
        <p>Equity: {equity:.2f}$ | PnL: {STATE["total_pnl"]:.2f}$</p>
        <p>Deals: {STATE["deals"]}</p>

        <h3>Активные пары</h3>
        <p>Ручные: {", ".join(STATE["active_pairs"])}</p>
        <p>Авто: {", ".join(STATE["auto_pairs"])}</p>

        <h3>Статистика по парам</h3>
        <table>
            <tr><th>Pair</th><th>Deals</th><th>PnL</th><th>Avg</th></tr>
            {rows or "<tr><td colspan=4>нет данных</td></tr>"}
        </table>
    </body>
    </html>
    """

# ================== RUN ==================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)