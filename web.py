# ====== web.py ======

import json
import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from datetime import datetime

STATE_FILE = "state.json"

app = FastAPI(title="Grid Bot UI")

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE) as f:
        return json.load(f)

@app.get("/", response_class=HTMLResponse)
def index():
    s = load_state()

    active_grids = s.get("active_grids", {})
    pair_stats = s.get("pair_stats", {})
    manual = s.get("active_pairs_manual", [])
    auto = s.get("active_pairs_auto", [])

    html = f"""
    <html>
    <head>
        <title>GRID BOT</title>
        <style>
            body {{
                font-family: Arial;
                background: #0e1117;
                color: #e6edf3;
                padding: 20px;
            }}
            h1, h2 {{
                color: #58a6ff;
            }}
            .box {{
                background: #161b22;
                padding: 15px;
                margin-bottom: 20px;
                border-radius: 8px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
            }}
            th, td {{
                padding: 6px;
                border-bottom: 1px solid #30363d;
                text-align: left;
            }}
            .green {{ color: #3fb950; }}
            .red {{ color: #f85149; }}
            .muted {{ color: #8b949e; }}
        </style>
    </head>
    <body>

    <h1>🤖 GRID BOT — WEB UI</h1>

    <div class="box">
        <h2>📊 Общая статистика</h2>
        <b>Equity:</b> {s.get("equity", 0)} $<br>
        <b>Total PnL:</b> {s.get("total_pnl", 0)} $<br>
        <b>Deals:</b> {s.get("deals", 0)}<br>
        <b>Uptime:</b> {s.get("uptime_min", 0)} min
    </div>

    <div class="box">
        <h2>🎯 Активные пары</h2>
        <b>Ручные:</b> {", ".join(manual) if manual else "—"}<br>
        <b>Авто:</b> {", ".join(auto) if auto else "—"}
    </div>

    <div class="box">
        <h2>🧱 Активные сетки</h2>
        <table>
            <tr>
                <th>Пара</th>
                <th>ATR</th>
                <th>Диапазон</th>
                <th>Ордеров</th>
            </tr>
    """

    for p, g in active_grids.items():
        html += f"""
            <tr>
                <td>{p}</td>
                <td>{round(g.get("atr", 0), 6)}</td>
                <td>{round(g["low"], 6)} → {round(g["high"], 6)}</td>
                <td>{len(g.get("orders", []))}</td>
            </tr>
        """

    html += """
        </table>
    </div>

    <div class="box">
        <h2>📈 Статистика по парам</h2>
        <table>
            <tr>
                <th>Пара</th>
                <th>Deals</th>
                <th>PnL</th>
                <th>Avg</th>
            </tr>
    """

    for p, ps in pair_stats.items():
        deals = ps.get("deals", 0)
        pnl = ps.get("pnl", 0)
        avg = pnl / deals if deals else 0
        cls = "green" if pnl >= 0 else "red"

        html += f"""
            <tr>
                <td>{p}</td>
                <td>{deals}</td>
                <td class="{cls}">{round(pnl, 4)}</td>
                <td>{round(avg, 4)}</td>
            </tr>
        """

    html += """
        </table>
    </div>

    <div class="box muted">
        Last update: """ + datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC") + """
    </div>

    </body>
    </html>
    """

    return html
