"""
HeatGuard Python Backend Server
================================
Serves the static frontend AND provides secure API proxy endpoints
so API keys never need to be exposed in the browser.

Requirements:
    pip install flask flask-cors requests

Run:
    python server.py

Then open:
    http://localhost:5500
"""

import os
import json
import time
import logging
import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# ── Configuration ────────────────────────────────────────────────────────────

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
PORT      = 5500
DEBUG     = True   # Set False in production

# API Keys — set via environment variables (preferred) or paste below
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")   # Google Gemini
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")   # OpenAI (optional)

# External service base URLs
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
NOMINATIM_URL  = "https://nominatim.openstreetmap.org"
OVERPASS_URL   = "https://overpass-api.de/api/interpreter"
OSRM_URL       = "https://router.project-osrm.org"
GEMINI_URL     = (
    "https://generativelanguage.googleapis.com/v1beta"
    "/models/gemini-1.5-flash:generateContent"
)

HEADERS = {
    "User-Agent": "HeatGuard/2.0 (heatwave-early-warning; contact@heatguard.app)"
}

# Simple in-memory cache
_cache: dict = {}
CACHE_TTL = 300  # 5 minutes

# ── App setup ─────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=BASE_DIR)
CORS(app)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("heatguard")


# ── Cache helpers ─────────────────────────────────────────────────────────────

def cache_get(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def cache_set(key: str, data):
    _cache[key] = {"ts": time.time(), "data": data}


# ── Static file serving ───────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the main HTML page."""
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    """Serve any static asset (CSS, JS, images)."""
    return send_from_directory(BASE_DIR, filename)


# ── API: Weather (Open-Meteo proxy) ──────────────────────────────────────────

@app.route("/api/weather")
def weather():
    """
    Proxy to Open-Meteo. Query params forwarded as-is.
    GET /api/weather?latitude=8.5&longitude=76.9&hourly=temperature_2m,...
    """
    params = dict(request.args)
    cache_key = "weather:" + json.dumps(params, sort_keys=True)

    cached = cache_get(cache_key)
    if cached:
        log.info("Weather cache hit")
        return jsonify(cached)

    try:
        resp = requests.get(OPEN_METEO_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        cache_set(cache_key, data)
        log.info("Weather fetched lat=%s lon=%s", params.get("latitude"), params.get("longitude"))
        return jsonify(data)
    except requests.RequestException as e:
        log.error("Weather API error: %s", e)
        return jsonify({"error": str(e)}), 502


# ── API: Geocoding (Nominatim proxy) ─────────────────────────────────────────

@app.route("/api/geocode")
def geocode():
    """
    Forward-geocode a location name to coordinates.
    GET /api/geocode?q=Thiruvananthapuram
    """
    params = {**dict(request.args), "format": "json"}
    cache_key = "geocode:" + json.dumps(params, sort_keys=True)

    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    try:
        resp = requests.get(
            f"{NOMINATIM_URL}/search",
            params=params,
            headers=HEADERS,
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        cache_set(cache_key, data)
        log.info("Geocoded '%s' -> %d results", params.get("q"), len(data))
        return jsonify(data)
    except requests.RequestException as e:
        log.error("Geocode error: %s", e)
        return jsonify({"error": str(e)}), 502


@app.route("/api/reverse-geocode")
def reverse_geocode():
    """
    Reverse-geocode coordinates to a place name.
    GET /api/reverse-geocode?lat=8.5&lon=76.9
    """
    params = {
        "lat": request.args.get("lat"),
        "lon": request.args.get("lon"),
        "format": "json",
    }
    cache_key = "rev:" + str(params)
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    try:
        resp = requests.get(
            f"{NOMINATIM_URL}/reverse",
            params=params,
            headers=HEADERS,
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        cache_set(cache_key, data)
        return jsonify(data)
    except requests.RequestException as e:
        log.error("Reverse geocode error: %s", e)
        return jsonify({"error": str(e)}), 502


# ── API: Facilities (Overpass proxy) ─────────────────────────────────────────

@app.route("/api/facilities", methods=["POST"])
def facilities():
    """
    Proxy to Overpass API for nearby facilities.
    Body: { "query": "<overpass QL query>" }
    """
    body = request.get_json(silent=True) or {}
    query = body.get("query", "")
    if not query:
        return jsonify({"error": "Missing 'query' field"}), 400

    cache_key = "facilities:" + query[:200]
    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    try:
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            headers=HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        cache_set(cache_key, data)
        log.info("Facilities: %d elements", len(data.get("elements", [])))
        return jsonify(data)
    except requests.RequestException as e:
        log.error("Facilities error: %s", e)
        return jsonify({"error": str(e)}), 502


# ── API: Routing (OSRM proxy) ─────────────────────────────────────────────────

@app.route("/api/route")
def route():
    """
    Proxy to OSRM for routing.
    GET /api/route?start_lat=8.5&start_lon=76.9&end_lat=9.0&end_lon=77.1
    """
    start_lat = request.args.get("start_lat")
    start_lon = request.args.get("start_lon")
    end_lat   = request.args.get("end_lat")
    end_lon   = request.args.get("end_lon")

    if not all([start_lat, start_lon, end_lat, end_lon]):
        return jsonify({"error": "Missing coordinate parameters"}), 400

    coords = f"{start_lon},{start_lat};{end_lon},{end_lat}"
    url    = f"{OSRM_URL}/route/v1/driving/{coords}"
    params = {"overview": "full", "geometries": "geojson", "alternatives": "true"}
    cache_key = f"route:{coords}"

    cached = cache_get(cache_key)
    if cached:
        return jsonify(cached)

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        cache_set(cache_key, data)
        log.info("Route: %s -> %s", (start_lat, start_lon), (end_lat, end_lon))
        return jsonify(data)
    except requests.RequestException as e:
        log.error("Route error: %s", e)
        return jsonify({"error": str(e)}), 502


# ── API: AI Chat (Gemini) ────────────────────────────────────────────────────

@app.route("/api/ai", methods=["POST"])
def ai_chat():
    """
    Secure Gemini 1.5 Flash chat proxy with HeatGuard context injection.
    Body:
    {
        "message": "What is the heat risk today?",
        "context": {
            "location": "Thiruvananthapuram",
            "temperature": 38,
            "humidity": 72,
            "riskLevel": "High"
        }
    }
    """
    if not GEMINI_API_KEY:
        return jsonify({
            "error": "AI not configured.",
            "setup": "Set the GEMINI_API_KEY environment variable and restart server.py"
        }), 503

    body         = request.get_json(silent=True) or {}
    user_message = body.get("message", "").strip()
    context      = body.get("context", {})

    if not user_message:
        return jsonify({"error": "Missing 'message' field"}), 400

    # Build context string
    ctx_lines = []
    if context.get("location"):
        ctx_lines.append(f"Location: {context['location']}")
    if context.get("temperature") is not None:
        ctx_lines.append(f"Temperature: {context['temperature']} C")
    if context.get("humidity") is not None:
        ctx_lines.append(f"Humidity: {context['humidity']}%")
    if context.get("riskLevel"):
        ctx_lines.append(f"Current heat risk: {context['riskLevel']}")

    system_prompt = (
        "You are HeatGuard AI, an expert in heatwave safety, thermal risk, "
        "and public health. Give concise, practical, evidence-based advice. "
        "Always prioritise safety and recommend professional help for medical emergencies."
    )
    if ctx_lines:
        system_prompt += "\n\nCurrent conditions:\n" + "\n".join(ctx_lines)

    payload = {
        "contents": [{"parts": [{"text": system_prompt + "\n\nUser: " + user_message}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 512},
    }

    try:
        resp = requests.post(
            GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=20,
        )
        resp.raise_for_status()
        data  = resp.json()
        reply = data["candidates"][0]["content"]["parts"][0]["text"]
        log.info("AI reply: %d chars", len(reply))
        return jsonify({"reply": reply})
    except (KeyError, IndexError):
        log.error("Unexpected Gemini response: %s", resp.text[:200])
        return jsonify({"error": "Unexpected response from AI"}), 502
    except requests.RequestException as e:
        log.error("AI error: %s", e)
        return jsonify({"error": str(e)}), 502


# ── API: Camera Vision Scan (Gemini Vision) ───────────────────────────────────

@app.route("/api/vision-scan", methods=["POST"])
def vision_scan():
    """
    Analyze a base64 JPEG for heat safety risks.
    Body: { "image_base64": "<base64>", "worker_type": "construction" }
    """
    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY not set"}), 503

    body        = request.get_json(silent=True) or {}
    image_b64   = body.get("image_base64", "")
    worker_type = body.get("worker_type", "outdoor worker")

    if not image_b64:
        return jsonify({"error": "Missing 'image_base64' field"}), 400

    prompt = (
        f"Analyze this image for heat safety risks for a {worker_type}. "
        "Check: 1) Clothing type, 2) Head protection, 3) Sun/shade exposure. "
        "Respond with exactly 3 short bullet points with immediate practical recommendations."
    )

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}}
            ]
        }]
    }

    try:
        resp = requests.post(
            GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=25,
        )
        resp.raise_for_status()
        data   = resp.json()
        result = data["candidates"][0]["content"]["parts"][0]["text"]
        log.info("Vision scan complete")
        return jsonify({"analysis": result})
    except (KeyError, IndexError):
        return jsonify({"error": "Unexpected response from vision API"}), 502
    except requests.RequestException as e:
        log.error("Vision error: %s", e)
        return jsonify({"error": str(e)}), 502


# ── API: Health check ─────────────────────────────────────────────────────────

@app.route("/api/status")
def status():
    """Returns server health and configuration status."""
    return jsonify({
        "status":       "running",
        "version":      "2.0.0",
        "ai_ready":     bool(GEMINI_API_KEY),
        "cache_size":   len(_cache),
        "endpoints": {
            "weather":      "GET  /api/weather?latitude=&longitude=",
            "geocode":      "GET  /api/geocode?q=",
            "reverse":      "GET  /api/reverse-geocode?lat=&lon=",
            "facilities":   "POST /api/facilities  {query}",
            "route":        "GET  /api/route?start_lat=&start_lon=&end_lat=&end_lon=",
            "ai_chat":      "POST /api/ai          {message, context}",
            "vision_scan":  "POST /api/vision-scan {image_base64, worker_type}",
        }
    })


# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def server_error(e):
    log.exception("Internal server error")
    return jsonify({"error": "Internal server error"}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  HeatGuard Backend Server")
    print("=" * 60)
    print(f"  Site    :  http://localhost:{PORT}")
    print(f"  Status  :  http://localhost:{PORT}/api/status")
    print(f"  AI Key  :  {'OK (set)' if GEMINI_API_KEY else 'NOT SET - set GEMINI_API_KEY env var'}")
    print("=" * 60)
    print("\n  To set your API key before running:")
    print("  Windows:  $env:GEMINI_API_KEY='YOUR_KEY_HERE'")
    print("  Linux:    export GEMINI_API_KEY='YOUR_KEY_HERE'")
    print("\n  Install dependencies:")
    print("  pip install flask flask-cors requests\n")
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
