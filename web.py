# app.py
from flask import Flask, render_template
import requests

ENGINE_URL = "https://YOUR-ENGINE.onrender.com/state"

app = Flask(__name__)

@app.route("/")
def index():
    try:
        data = requests.get(ENGINE_URL, timeout=5).json()
    except Exception:
        data = {}

    return render_template("index.html", data=data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)