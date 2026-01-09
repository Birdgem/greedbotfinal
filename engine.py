import time
import asyncio
import aiohttp
from statistics import mean
from fastapi import FastAPI
import uvicorn

app = FastAPI()

# ================== CONFIG ==================
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

ATR_PERIOD = 14
SCAN_INTERVAL = 20

# ================== STATE ==================
START_TS = time.time()

STATE = {
    "equity": DEPOSIT,
    "total_pnl": 0.0,
    "deals": 0,
    "active_pairs": ["SOLUSDT", "TONUSDT"],
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
            d = await r.json()
            return d if isinstance(d, list) else []

# ================== ENGINE LOOP ==================
async def engine_loop():
    while True:
        for pair in STATE["active_pairs"]:
            kl = await get_klines(pair)
            if len(kl) < 50:
                continue

            c = [float(k[4]) for k in kl]
            h = [float(k[2]) for k in kl]
            l = [float(k[3]) for k in kl]

            a = atr(h, l, c)
            if not a:
                continue

            # эмуляция сделки
            pnl = a * 0.001
            STATE["total_pnl"] += pnl
            STATE["equity"] = DEPOSIT + STATE["total_pnl"]
            STATE["deals"] += 1

            ps = STATE["pair_stats"].setdefault(pair, {
                "pnl": 0.0,
                "deals": 0
            })

            ps["pnl"] += pnl
            ps["deals"] += 1

        await asyncio.sleep(SCAN_INTERVAL)

# ================== API ==================
@app.get("/state")
def get_state():
    return {
        "equity": STATE.get("equity", 0),
        "total_pnl": STATE.get("total_pnl", 0),
        "deals": STATE.get("deals", 0),
        "active_pairs": STATE.get("active_pairs", []),
        "auto_pairs": STATE.get("auto_pairs", []),
        "active_grids": STATE.get("active_grids", {}),
        "pair_stats": STATE.get("pair_stats", {}),
        "uptime_min": int((time.time() - START_TS) / 60),
        "timestamp": int(time.time())
    }

# ================== START ==================
if __name__ == "__main__":
    asyncio.get_event_loop().create_task(engine_loop())
    uvicorn.run(app, host="0.0.0.0", port=8001)