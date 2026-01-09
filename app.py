from flask import Flask, render_template
import requests

ENGINE_URL = "http://localhost:8001"

app = Flask(__name__)

@app.route("/")
def index():
    try:
        r = requests.get(ENGINE_URL + "/state", timeout=3)
        data = r.json()
    except Exception:
        data = {}

    data.setdefault("pair_stats", {})
    data.setdefault("active_grids", {})
    data.setdefault("active_pairs", [])
    data.setdefault("auto_pairs", [])

    return render_template("index.html", data=data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)