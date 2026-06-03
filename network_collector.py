"""
=============================================================
  network_collector.py
  Collects all WiFi/Network metrics and sends to FastAPI server
  Run: python network_collector.py
=============================================================
"""

import psutil
import socket
import subprocess
import time
import json
import re
import platform
import requests as http_requests
from datetime import datetime

SERVER_URL = "http://localhost:8000"


def get_device_info():
    return {
        "hostname":   socket.gethostname(),
        "platform":   platform.system(),
        "os_version": platform.version(),
        "local_ip":   socket.gethostbyname(socket.gethostname()),
        "timestamp":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def get_wifi_info():
    result = {}
    try:
        output = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True
        ).stdout
        for line in output.split("\n"):
            line = line.strip()
            if line.startswith("SSID") and "BSSID" not in line:
                result["ssid"]          = line.split(":", 1)[1].strip()
            elif line.startswith("BSSID"):
                result["bssid"]         = line.split(":", 1)[1].strip()
            elif line.startswith("Signal"):
                result["signal_%"]      = line.split(":", 1)[1].strip()
            elif line.startswith("Radio type"):
                result["wifi_standard"] = line.split(":", 1)[1].strip()
            elif line.startswith("Authentication"):
                result["security"]      = line.split(":", 1)[1].strip()
            elif line.startswith("Channel"):
                result["channel"]       = line.split(":", 1)[1].strip()
            elif line.startswith("Receive rate"):
                result["rx_rate_mbps"]  = line.split(":", 1)[1].strip()
            elif line.startswith("Transmit rate"):
                result["tx_rate_mbps"]  = line.split(":", 1)[1].strip()
            elif line.startswith("Band"):
                result["band"]          = line.split(":", 1)[1].strip()
            elif line.startswith("State"):
                result["state"]         = line.split(":", 1)[1].strip()
    except Exception as e:
        result["error"] = str(e)
    return result


def get_network_interfaces():
    info = {}
    stats = psutil.net_if_stats()
    for iface, addrs in psutil.net_if_addrs().items():
        iface_data = {
            "is_up":      stats[iface].isup if iface in stats else False,
            "speed_mbps": stats[iface].speed if iface in stats else 0,
            "mtu":        stats[iface].mtu if iface in stats else 0,
        }
        for addr in addrs:
            if addr.family == socket.AF_INET:
                iface_data["ipv4"]      = addr.address
                iface_data["netmask"]   = addr.netmask
                iface_data["broadcast"] = addr.broadcast
            elif addr.family == socket.AF_INET6:
                iface_data["ipv6"]      = addr.address
            elif addr.family == psutil.AF_LINK:
                iface_data["mac"]       = addr.address
        info[iface] = iface_data
    return info


def get_gateway_and_dns():
    result = {"gateway": None, "dns_servers": []}
    try:
        ipconfig = subprocess.run(
            ["ipconfig", "/all"], capture_output=True, text=True
        ).stdout
        for line in ipconfig.split("\n"):
            if "Default Gateway" in line:
                parts = line.split(":")
                if len(parts) > 1 and parts[1].strip():
                    result["gateway"] = parts[1].strip()
            if "DNS Servers" in line:
                parts = line.split(":")
                if len(parts) > 1 and parts[1].strip():
                    result["dns_servers"].append(parts[1].strip())
    except Exception as e:
        result["error"] = str(e)
    return result


def get_ping_stats(host="8.8.8.8", count=10):
    result = {"host": host}
    try:
        output = subprocess.run(
            ["ping", "-n", str(count), host],
            capture_output=True, text=True
        ).stdout
        loss = re.search(r"(\d+)% loss", output)
        avg  = re.search(r"Average = (\d+)ms", output)
        mini = re.search(r"Minimum = (\d+)ms", output)
        maxi = re.search(r"Maximum = (\d+)ms", output)
        result["packet_loss_%"] = int(loss.group(1)) if loss else 0
        result["avg_ms"]        = int(avg.group(1))  if avg  else 0
        result["min_ms"]        = int(mini.group(1)) if mini else 0
        result["max_ms"]        = int(maxi.group(1)) if maxi else 0
        if result["min_ms"] and result["max_ms"]:
            result["jitter_ms"] = result["max_ms"] - result["min_ms"]
        loss_val = result["packet_loss_%"] or 0
        avg_val  = result["avg_ms"] or 0
        if loss_val > 10 or avg_val > 500:
            result["status"] = "CRITICAL"
        elif loss_val > 5 or avg_val > 200:
            result["status"] = "WARNING"
        else:
            result["status"] = "OK"
    except Exception as e:
        result["error"] = str(e)
    return result


def get_bandwidth(interval=1):
    result = {}
    try:
        interface = "Wi-Fi"
        for name, stat in psutil.net_if_stats().items():
            if stat.isup and "wi-fi" in name.lower():
                interface = name
                break
        old = psutil.net_io_counters(pernic=True).get(interface)
        time.sleep(interval)
        new = psutil.net_io_counters(pernic=True).get(interface)
        if old and new:
            result = {
                "interface":        interface,
                "upload_mbps":      round((new.bytes_sent - old.bytes_sent) / interval / 1024 / 1024, 3),
                "download_mbps":    round((new.bytes_recv - old.bytes_recv) / interval / 1024 / 1024, 3),
                "total_bytes_sent": new.bytes_sent,
                "total_bytes_recv": new.bytes_recv,
                "errors_in":        new.errin,
                "errors_out":       new.errout,
                "dropped_in":       new.dropin,
                "dropped_out":      new.dropout,
            }
    except Exception as e:
        result["error"] = str(e)
    return result


def get_dns_resolution_time(domains=["google.com", "youtube.com", "github.com", "cloudflare.com"]):
    results = []
    for domain in domains:
        start = time.time()
        try:
            ip      = socket.gethostbyname(domain)
            elapsed = round((time.time() - start) * 1000, 2)
            results.append({"domain": domain, "ip": ip, "ms": elapsed,
                            "status": "SLOW" if elapsed > 200 else "OK"})
        except Exception as e:
            results.append({"domain": domain, "error": str(e), "status": "FAILED"})
    return results


def get_active_connections():
    connections = []
    try:
        for conn in psutil.net_connections(kind="inet"):
            try:
                process = psutil.Process(conn.pid).name() if conn.pid else "Unknown"
            except:
                process = "Unknown"
            connections.append({
                "pid":            conn.pid,
                "process":        process,
                "status":         conn.status,
                "protocol":       "TCP" if conn.type == socket.SOCK_STREAM else "UDP",
                "local_address":  f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else None,
                "remote_address": f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else None,
            })
    except Exception as e:
        return {"error": str(e)}
    established = [c for c in connections if c["status"] == "ESTABLISHED"]
    listening   = [c for c in connections if c["status"] == "LISTEN"]
    return {
        "total_established": len(established),
        "total_listening":   len(listening),
        "established":       established[:20],
    }


def get_arp_table():
    devices = []
    try:
        output = subprocess.run(["arp", "-a"], capture_output=True, text=True).stdout
        for line in output.split("\n"):
            parts = line.split()
            if len(parts) >= 2 and parts[0][0].isdigit():
                devices.append({"ip": parts[0], "mac": parts[1],
                                "type": parts[2] if len(parts) > 2 else "unknown"})
    except Exception as e:
        return [{"error": str(e)}]
    return devices


def detect_errors(report):
    alerts = []
    ping = report.get("ping_google", {})
    if ping.get("packet_loss_%", 0) > 5:
        alerts.append(f"⚠️  HIGH PACKET LOSS: {ping['packet_loss_%']}%")
    if ping.get("avg_ms", 0) > 200:
        alerts.append(f"⚠️  HIGH LATENCY: {ping['avg_ms']}ms")
    if ping.get("jitter_ms", 0) > 50:
        alerts.append(f"⚠️  HIGH JITTER: {ping['jitter_ms']}ms")
    bw = report.get("bandwidth", {})
    if bw.get("errors_in", 0) > 0 or bw.get("errors_out", 0) > 0:
        alerts.append("⚠️  PACKET ERRORS detected")
    if bw.get("dropped_in", 0) > 0 or bw.get("dropped_out", 0) > 0:
        alerts.append("⚠️  DROPPED PACKETS detected")
    for dns in report.get("dns_tests", []):
        if dns.get("status") == "FAILED":
            alerts.append(f"❌ DNS FAILED: {dns['domain']}")
    signal    = report.get("wifi_info", {}).get("signal_%", "100%")
    signal_val = int(str(signal).replace("%", "")) if signal else 100
    if signal_val < 30:
        alerts.append(f"❌ CRITICAL SIGNAL: {signal}")
    elif signal_val < 50:
        alerts.append(f"⚠️  WEAK SIGNAL: {signal}")
    if not alerts:
        alerts.append("✅ All systems normal")
    return alerts


def collect_all():
    print("\n🔍 Collecting network data...")
    report = {}
    report["device"]       = get_device_info()
    report["wifi_info"]    = get_wifi_info()
    report["interfaces"]   = get_network_interfaces()
    report["gateway_dns"]  = get_gateway_and_dns()
    report["ping_google"]  = get_ping_stats("8.8.8.8", count=10)
    gw = report["gateway_dns"].get("gateway", "8.8.8.8")
    report["ping_gateway"] = get_ping_stats(gw, count=5)
    report["bandwidth"]    = get_bandwidth(interval=1)
    report["dns_tests"]    = get_dns_resolution_time()
    report["connections"]  = get_active_connections()
    report["arp_devices"]  = get_arp_table()
    report["alerts"]       = detect_errors(report)
    return report


def send_to_server(report):
    try:
        response = http_requests.post(
            f"{SERVER_URL}/metrics", json=report, timeout=10
        )
        if response.status_code == 200:
            print("✅ Data sent to server")
        else:
            print(f"⚠️  Server error: {response.status_code}")
    except Exception as e:
        print(f"❌ Cannot reach server: {e}")


if __name__ == "__main__":
    print("🚀 Network Monitor Started — sending every 30 seconds")
    print("   Press Ctrl+C to stop\n")
    while True:
        try:
            report = collect_all()
            send_to_server(report)
            print("⏳ Next in 30s...\n")
            time.sleep(30)
        except KeyboardInterrupt:
            print("\n🛑 Stopped.")
            break
        except Exception as e:
            print(f"❌ Error: {e}")
            time.sleep(30)