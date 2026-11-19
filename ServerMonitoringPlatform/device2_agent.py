import psutil
import requests

API_URL = "http://127.0.0.1:5000/api/push"
SERVER_ID = 2


def start_agent():
    print(f"Device 2 Agent started (Server ID: {SERVER_ID})")

    while True:
        try:
            # 1-second interval also acts as the loop delay
            cpu = psutil.cpu_percent(interval=1)
            ram = psutil.virtual_memory().percent

            # Collect top 5 memory-consuming processes
            processes = []
            for proc in psutil.process_iter(['name', 'memory_percent']):
                try:
                    mem = proc.info['memory_percent']
                    if mem is not None:
                        processes.append({
                            'name': proc.info['name'],
                            'memory_percent': round(mem, 2)
                        })
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

            top_processes = sorted(processes, key=lambda p: p['memory_percent'], reverse=True)[:5]

            payload = {
                "server_id": SERVER_ID,
                "cpu_percent": cpu,
                "memory_percent": ram,
                "top_processes": top_processes
            }

            requests.post(API_URL, json=payload, timeout=5)
            print(f"Sent - CPU: {cpu}%, RAM: {ram}%")

        except Exception as e:
            print(f"Failed to connect to central server: {e}")


if __name__ == "__main__":
    start_agent()