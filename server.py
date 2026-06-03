"""
=============================================================
  server.py
  FastAPI Server — receives metrics and writes to InfluxDB
  Run: python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
=============================================================
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
import json, os

app = FastAPI(title="Network Health Monitor API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ⚠️ PASTE YOUR TOKEN HERE
INFLUX_URL    = "http://localhost:8086"
INFLUX_TOKEN  = "JaeskNhmVOBLfD-zLciqxF2fSxrl4FhycfDTm58ydCZtENV-_j1ufCYp9WmPhYj84dz5FfAyKStFxfLtxr-C7g=="
INFLUX_ORG    = "myorg"
INFLUX_BUCKET = "network_metrics"

influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
write_api     = influx_client.write_api(write_options=SYNCHRONOUS)

REPORTS_DIR = "reports"
os.makedirs(REPORTS_DIR, exist_ok=True)


def write_to_influx(data: dict):
    hostname = data.get("device", {}).get("hostname", "unknown")
    points   = []

    wifi       = data.get("wifi_info", {})
    signal_raw = wifi.get("signal_%", "0%")
    signal_val = int(str(signal_raw).replace("%", "").strip()) if signal_raw else 0
    points.append(Point("wifi_info").tag("host", hostname).tag("ssid", wifi.get("ssid", "unknown")).tag("band", wifi.get("band", "unknown"))
        .field("signal_percent", float(signal_val))
        .field("rx_rate_mbps", float(str(wifi.get("rx_rate_mbps", "0")).split()[0]) if wifi.get("rx_rate_mbps") else 0.0)
        .field("tx_rate_mbps", float(str(wifi.get("tx_rate_mbps", "0")).split()[0]) if wifi.get("tx_rate_mbps") else 0.0))

    ping = data.get("ping_google", {})
    points.append(Point("ping_google").tag("host", hostname)
        .field("avg_ms", float(ping.get("avg_ms") or 0))
        .field("min_ms", float(ping.get("min_ms") or 0))
        .field("max_ms", float(ping.get("max_ms") or 0))
        .field("jitter_ms", float(ping.get("jitter_ms") or 0))
        .field("packet_loss_pct", float(ping.get("packet_loss_%") or 0)))

    ping_gw = data.get("ping_gateway", {})
    points.append(Point("ping_gateway").tag("host", hostname).tag("gateway", ping_gw.get("host", "unknown"))
        .field("avg_ms", float(ping_gw.get("avg_ms") or 0))
        .field("packet_loss_pct", float(ping_gw.get("packet_loss_%") or 0)))

    bw = data.get("bandwidth", {})
    points.append(Point("bandwidth").tag("host", hostname).tag("interface", bw.get("interface", "unknown"))
        .field("upload_mbps", float(bw.get("upload_mbps") or 0))
        .field("download_mbps", float(bw.get("download_mbps") or 0))
        .field("errors_in", float(bw.get("errors_in") or 0))
        .field("errors_out", float(bw.get("errors_out") or 0))
        .field("dropped_in", float(bw.get("dropped_in") or 0))
        .field("dropped_out", float(bw.get("dropped_out") or 0)))

    for dns in data.get("dns_tests", []):
        points.append(Point("dns_resolution").tag("host", hostname).tag("domain", dns.get("domain", "unknown"))
            .field("resolution_ms", float(dns.get("ms") or 0))
            .field("failed", 1.0 if dns.get("status") == "FAILED" else 0.0))

    conn = data.get("connections", {})
    points.append(Point("connections").tag("host", hostname)
        .field("established", float(conn.get("total_established") or 0))
        .field("listening", float(conn.get("total_listening") or 0)))

    alerts    = data.get("alerts", [])
    has_error = any("⚠️" in a or "❌" in a for a in alerts)
    points.append(Point("alerts").tag("host", hostname)
        .field("total_alerts", float(len(alerts)))
        .field("has_error", 1.0 if has_error else 0.0))

    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=points)
    print(f"   ✅ {len(points)} metrics written to InfluxDB")


@app.get("/")
def root():
    return {"status": "running", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


@app.post("/metrics")
async def receive_metrics(data: dict):
    try:
        hostname  = data.get("device", {}).get("hostname", "unknown")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(f"{REPORTS_DIR}/{hostname}_{timestamp}.json", "w") as f:
            json.dump(data, f, indent=2)
        write_to_influx(data)
        alerts    = data.get("alerts", [])
        has_error = any("⚠️" in a or "❌" in a for a in alerts)
        print(f"\n📥 {hostname} | Signal: {data.get('wifi_info',{}).get('signal_%')} | Ping: {data.get('ping_google',{}).get('avg_ms')}ms | {'⚠️ ISSUES' if has_error else '✅ OK'}")
        return {"status": "received", "influxdb": "written", "hostname": hostname}
    except Exception as e:
        print(f"❌ Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/status")
def get_status():
    files = sorted(os.listdir(REPORTS_DIR), reverse=True)
    if not files:
        return {"message": "No reports yet"}
    with open(f"{REPORTS_DIR}/{files[0]}") as f:
        latest = json.load(f)
    return {
        "hostname":      latest.get("device", {}).get("hostname"),
        "last_seen":     latest.get("device", {}).get("timestamp"),
        "signal":        latest.get("wifi_info", {}).get("signal_%"),
        "ssid":          latest.get("wifi_info", {}).get("ssid"),
        "ping_ms":       latest.get("ping_google", {}).get("avg_ms"),
        "packet_loss":   latest.get("ping_google", {}).get("packet_loss_%"),
        "upload_mbps":   latest.get("bandwidth", {}).get("upload_mbps"),
        "download_mbps": latest.get("bandwidth", {}).get("download_mbps"),
        "alerts":        latest.get("alerts", []),
        "total_reports": len(files),
    }


@app.get("/latest")
def get_latest():
    files = sorted(os.listdir(REPORTS_DIR), reverse=True)
    if not files:
        raise HTTPException(status_code=404, detail="No reports yet")
    with open(f"{REPORTS_DIR}/{files[0]}") as f:
        return json.load(f)