import random
import requests
import time

# Control switch: set True to trigger threshold alerts (CPU > 85%, RAM > 90%)
TRIGGER_ALERT = True

API_URL = "http://127.0.0.1:5000/api/push"
# Use fixed server_id 3 (auto-created by app.py if missing)
TARGET_SERVER_ID = 3


def generate_simulated_metrics(trigger_alert):
    """Generate simulated CPU, RAM, and top 5 process metrics."""
    if trigger_alert:
        cpu = round(random.uniform(86.0, 98.5), 1)
        ram = round(random.uniform(91.0, 97.5), 1)
        top_processes = [
            {"name": "stress_worker.exe", "memory_percent": round(random.uniform(30.0, 38.0), 2)},
            {"name": "render_engine.exe", "memory_percent": round(random.uniform(22.0, 28.0), 2)},
            {"name": "db_indexer.exe", "memory_percent": round(random.uniform(15.0, 20.0), 2)},
            {"name": "cache_node.exe", "memory_percent": round(random.uniform(8.0, 12.0), 2)},
            {"name": "log_collector.exe", "memory_percent": round(random.uniform(3.0, 6.0), 2)},
        ]
    else:
        cpu = round(random.uniform(15.0, 40.0), 1)
        ram = round(random.uniform(30.0, 55.0), 1)
        top_processes = [
            {"name": "system_idle.exe", "memory_percent": round(random.uniform(8.0, 12.0), 2)},
            {"name": "web_service.exe", "memory_percent": round(random.uniform(6.0, 10.0), 2)},
            {"name": "db_service.exe", "memory_percent": round(random.uniform(5.0, 8.0), 2)},
            {"name": "monitor_agent.exe", "memory_percent": round(random.uniform(2.0, 4.0), 2)},
            {"name": "log_collector.exe", "memory_percent": round(random.uniform(1.0, 2.5), 2)},
        ]

    return cpu, ram, top_processes


def start_sim_agent():
    mode_label = "ALERT MODE (High Load)" if TRIGGER_ALERT else "NORMAL MODE"
    print(f"Alert Test Bot started for Server ID [{TARGET_SERVER_ID}] - {mode_label}")
    print("Press Ctrl+C or close this window to stop simulation.")

    while True:
        try:
            cpu, ram, top_processes = generate_simulated_metrics(TRIGGER_ALERT)

            payload = {
                "server_id": TARGET_SERVER_ID,
                "cpu_percent": cpu,
                "memory_percent": ram,
                "top_processes": top_processes,
            }

            response = requests.post(API_URL, json=payload, timeout=5)
            if response.status_code == 201:
                print(f"[SIM] Sent to Server ID {TARGET_SERVER_ID} - CPU: {cpu}%, RAM: {ram}%")
            else:
                print(f"[SIM] Server returned status {response.status_code}: {response.text}")

        except Exception as e:
            print(f"[SIM] Failed to connect to central server: {e}")

        time.sleep(2)


if __name__ == "__main__":
    start_sim_agent()