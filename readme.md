To run make sure all below services are running including influxdb.
Make sure to make changes in token which would be different in your case.

cd "C:\Program Files\GrafanaLabs\grafana\bin"
& .\grafana-server.exe

cd D:\info
python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload

cd D:\info
python network_collector.py

---
