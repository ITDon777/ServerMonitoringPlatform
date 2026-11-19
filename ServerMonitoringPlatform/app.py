from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import requests
import sqlite3
import threading
import time
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = 'super_secret_admin_key_for_monitoring'
db_write_lock = threading.Lock()

# Global alert thresholds, stale timeout, and Telegram cooldown (seconds)
CPU_THRESHOLD = 85.0
RAM_THRESHOLD = 90.0
ALERT_STALE_SECONDS = 5
TELEGRAM_ALERT_COOLDOWN = 60
last_telegram_alert = {}


def get_db_connection():
    conn = sqlite3.connect('monitoring.db', timeout=15.0)
    conn.execute('PRAGMA busy_timeout = 15000;')
    conn.row_factory = sqlite3.Row
    return conn


def init_db_extensions():
    conn = get_db_connection()
    # Enable WAL mode persistently for better read/write concurrency
    conn.execute('PRAGMA journal_mode=WAL;')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hostname TEXT NOT NULL,
            ip_address TEXT NOT NULL
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS metrics_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER,
            cpu_percent REAL,
            memory_percent REAL,
            timestamp DATETIME,
            FOREIGN KEY(server_id) REFERENCES servers(id)
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS server_processes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER,
            process_name TEXT,
            memory_percent REAL,
            timestamp DATETIME,
            FOREIGN KEY(server_id) REFERENCES servers(id)
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

    # Auto-migrate legacy server_processes table if timestamp column is missing
    proc_columns = [row['name'] for row in conn.execute('PRAGMA table_info(server_processes)').fetchall()]
    if 'timestamp' not in proc_columns:
        conn.execute('ALTER TABLE server_processes ADD COLUMN timestamp DATETIME')

    # Ensure default server IDs 1, 2, and 3 exist without overwriting renamed devices
    default_servers = [
        (1, 'WIN-NODE-01', '192.168.1.101'),
        (2, 'WIN-NODE-02', '192.168.1.102'),
        (3, 'alert_test_bot', '192.168.1.250'),
    ]
    for sid, hostname, ip in default_servers:
        conn.execute(
            'INSERT OR IGNORE INTO servers (id, hostname, ip_address) VALUES (?, ?, ?)',
            (sid, hostname, ip)
        )

    conn.commit()
    conn.close()


init_db_extensions()


def send_telegram_message(bot_token, chat_id, message):
    """Send Telegram alert in a background thread so API ingestion is never blocked."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=5)
        if resp.status_code != 200:
            print(f"[Telegram] API returned {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[Telegram] Failed to send alert: {e}")


def trigger_telegram_alert_if_needed(server_id, hostname, cpu, ram):
    """Query SQLite settings and notify Telegram when thresholds are exceeded."""
    reasons = []
    if cpu is not None and cpu > CPU_THRESHOLD:
        reasons.append(f"CPU: {cpu}% (>{int(CPU_THRESHOLD)}%)")
    if ram is not None and ram > RAM_THRESHOLD:
        reasons.append(f"RAM: {ram}% (>{int(RAM_THRESHOLD)}%)")

    if not reasons:
        return

    # Prevent alert flooding with per-server cooldown
    now = time.time()
    if now - last_telegram_alert.get(server_id, 0) < TELEGRAM_ALERT_COOLDOWN:
        return

    conn = get_db_connection()
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('telegram_bot_token', 'telegram_chat_id')"
    ).fetchall()
    conn.close()

    cfg = {row['key']: row['value'] for row in rows}
    bot_token = cfg.get('telegram_bot_token', '').strip()
    chat_id = cfg.get('telegram_chat_id', '').strip()

    if bot_token and chat_id:
        last_telegram_alert[server_id] = now
        alert_text = (
            f"[Server Alert] {hostname}\n"
            f"Triggered: {', '.join(reasons)}\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        threading.Thread(
            target=send_telegram_message,
            args=(bot_token, chat_id, alert_text),
            daemon=True
        ).start()


# --- Authentication Routes ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'admin' and password == 'admin':
            session['logged_in'] = True
            return redirect(url_for('index'))
        error = 'Invalid credentials. Please try again.'
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))


# --- Frontend Page Routes ---
@app.route('/')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = get_db_connection()
    servers = conn.execute('SELECT * FROM servers').fetchall()
    conn.close()
    return render_template('dashboard.html', servers=servers)


@app.route('/history')
def history():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = get_db_connection()
    servers = conn.execute('SELECT * FROM servers').fetchall()
    conn.close()
    return render_template('history.html', servers=servers)


# --- Device Management Routes ---
@app.route('/devices', methods=['GET', 'POST'])
def manage_devices():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = get_db_connection()
    error = None

    if request.method == 'POST':
        original_hostname = request.form['hostname'].strip()
        ip_address = request.form['ip_address'].strip()

        hostname = original_hostname
        counter = 1

        # Auto-append sequential suffix if hostname already exists
        with db_write_lock:
            while True:
                existing_device = conn.execute('SELECT id FROM servers WHERE hostname = ?', (hostname,)).fetchone()
                if not existing_device:
                    break
                hostname = f"{original_hostname} ({counter})"
                counter += 1

            conn.execute('INSERT INTO servers (hostname, ip_address) VALUES (?, ?)', (hostname, ip_address))
            conn.commit()
        conn.close()
        return redirect(url_for('manage_devices'))

    devices = conn.execute('SELECT * FROM servers').fetchall()
    conn.close()
    return render_template('devices.html', devices=devices, error=error)


@app.route('/devices/edit/<int:device_id>', methods=['POST'])
def edit_device(device_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    original_hostname = request.form['hostname'].strip()
    ip_address = request.form['ip_address'].strip()

    if not original_hostname or not ip_address:
        return redirect(url_for('manage_devices'))

    hostname = original_hostname
    counter = 1

    # Auto-append sequential suffix if another device already uses this hostname
    with db_write_lock:
        conn = get_db_connection()
        try:
            while True:
                existing_device = conn.execute(
                    'SELECT id FROM servers WHERE hostname = ? AND id != ?',
                    (hostname, device_id)
                ).fetchone()
                if not existing_device:
                    break
                hostname = f"{original_hostname} ({counter})"
                counter += 1

            conn.execute(
                'UPDATE servers SET hostname = ?, ip_address = ? WHERE id = ?',
                (hostname, ip_address, device_id)
            )
            conn.commit()
        finally:
            conn.close()

    return redirect(url_for('manage_devices'))


@app.route('/devices/delete/<int:device_id>', methods=['POST'])
def delete_device(device_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = get_db_connection()
    with db_write_lock:
        conn.execute('DELETE FROM metrics_history WHERE server_id = ?', (device_id,))
        conn.execute('DELETE FROM server_processes WHERE server_id = ?', (device_id,))
        conn.execute('DELETE FROM servers WHERE id = ?', (device_id,))
        conn.commit()
    conn.close()
    return redirect(url_for('manage_devices'))


# --- Data Ingestion & Query APIs ---
@app.route('/api/push', methods=['POST'])
def push_data():
    data = request.json
    server_id = data.get('server_id')
    hostname = data.get('hostname')
    cpu = data.get('cpu_percent')
    ram = data.get('memory_percent')
    top_processes = data.get('top_processes', [])

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Serialize SQLite writes across concurrent agents to avoid lock timeouts
    with db_write_lock:
        conn = get_db_connection()
        try:
            # Resolve server_id via hostname when server_id is omitted
            if not server_id and hostname:
                row = conn.execute('SELECT id, hostname FROM servers WHERE hostname = ?', (hostname,)).fetchone()
                if not row:
                    return jsonify({"error": "Server hostname not found"}), 404
                server_id = row['id']
                hostname = row['hostname']
            elif server_id and not hostname:
                row = conn.execute('SELECT hostname FROM servers WHERE id = ?', (server_id,)).fetchone()
                if not row:
                    valid_rows = conn.execute('SELECT id, hostname FROM servers ORDER BY id').fetchall()
                    valid_info = ", ".join([f"{r['id']}:{r['hostname']}" for r in valid_rows])
                    return jsonify({"error": f"Server ID {server_id} not found. Valid IDs -> {valid_info}"}), 404
                hostname = row['hostname']

            conn.execute('''
                INSERT INTO metrics_history (server_id, cpu_percent, memory_percent, timestamp)
                VALUES (?, ?, ?, ?)
            ''', (server_id, cpu, ram, current_time))

            conn.execute('DELETE FROM server_processes WHERE server_id = ?', (server_id,))
            for proc in top_processes:
                conn.execute('''
                    INSERT INTO server_processes (server_id, process_name, memory_percent, timestamp)
                    VALUES (?, ?, ?, ?)
                ''', (server_id, proc['name'], proc['memory_percent'], current_time))

            conn.commit()
        finally:
            conn.close()

    trigger_telegram_alert_if_needed(server_id, hostname, cpu, ram)
    return jsonify({"status": "success"}), 201


@app.route('/api/metrics', methods=['GET'])
def get_metrics():
    server_id = request.args.get('server_id', 1)
    conn = get_db_connection()
    query = '''
        SELECT cpu_percent, memory_percent, timestamp
        FROM metrics_history
        WHERE server_id = ?
        ORDER BY timestamp DESC LIMIT 30
    '''
    records = conn.execute(query, (server_id,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in records])


@app.route('/api/servers/overview', methods=['GET'])
def get_servers_overview():
    conn = get_db_connection()
    query = '''
        SELECT s.hostname, m.cpu_percent
        FROM servers s
        LEFT JOIN (
            SELECT server_id, cpu_percent, MAX(timestamp)
            FROM metrics_history
            GROUP BY server_id
        ) m ON s.id = m.server_id
        WHERE m.cpu_percent IS NOT NULL
    '''
    records = conn.execute(query).fetchall()
    conn.close()
    return jsonify([dict(r) for r in records])


@app.route('/api/processes', methods=['GET'])
def get_processes():
    hostname = request.args.get('hostname')
    conn = get_db_connection()
    query = '''
        SELECT p.process_name, p.memory_percent
        FROM server_processes p
        JOIN servers s ON p.server_id = s.id
        WHERE s.hostname = ?
        ORDER BY p.memory_percent DESC
    '''
    records = conn.execute(query, (hostname,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in records])


@app.route('/api/history_data', methods=['GET'])
def get_history_data():
    server_id = request.args.get('server_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    query = "SELECT cpu_percent, memory_percent, timestamp FROM metrics_history WHERE server_id = ?"
    params = [server_id]

    if start_date:
        query += " AND timestamp >= ?"
        params.append(f"{start_date} 00:00:00")
    if end_date:
        query += " AND timestamp <= ?"
        params.append(f"{end_date} 23:59:59")

    query += " ORDER BY timestamp DESC LIMIT 1000"

    conn = get_db_connection()
    records = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in records])


@app.route('/api/alert_history', methods=['GET'])
def get_alert_history():
    # Query historical alerts across all servers (or a specific server if provided)
    server_id = request.args.get('server_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    query = """
        SELECT s.hostname, m.cpu_percent, m.memory_percent, m.timestamp
        FROM metrics_history m
        JOIN servers s ON m.server_id = s.id
        WHERE (m.cpu_percent > ? OR m.memory_percent > ?)
    """
    params = [CPU_THRESHOLD, RAM_THRESHOLD]

    if server_id and server_id != 'all':
        query += " AND m.server_id = ?"
        params.append(server_id)
    if start_date:
        query += " AND m.timestamp >= ?"
        params.append(f"{start_date} 00:00:00")
    if end_date:
        query += " AND m.timestamp <= ?"
        params.append(f"{end_date} 23:59:59")

    query += " ORDER BY m.timestamp DESC LIMIT 100"

    conn = get_db_connection()
    records = conn.execute(query, params).fetchall()
    conn.close()

    alerts = []
    for r in records:
        reasons = []
        if r['cpu_percent'] is not None and r['cpu_percent'] > CPU_THRESHOLD:
            reasons.append(f"CPU > {int(CPU_THRESHOLD)}%")
        if r['memory_percent'] is not None and r['memory_percent'] > RAM_THRESHOLD:
            reasons.append(f"RAM > {int(RAM_THRESHOLD)}%")
        alerts.append({
            "hostname": r['hostname'],
            "timestamp": r['timestamp'],
            "cpu_percent": r['cpu_percent'],
            "memory_percent": r['memory_percent'],
            "reason": ", ".join(reasons)
        })

    return jsonify(alerts)


@app.route('/api/alerts', methods=['GET'])
def get_global_alerts():
    # Only evaluate servers that reported metrics within the last 5 seconds
    cutoff_time = (datetime.now() - timedelta(seconds=ALERT_STALE_SECONDS)).strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db_connection()
    query = '''
        SELECT s.id, s.hostname, m.cpu_percent, m.memory_percent, m.timestamp
        FROM servers s
        JOIN metrics_history m ON m.id = (
            SELECT MAX(id) FROM metrics_history WHERE server_id = s.id
        )
        WHERE m.timestamp >= ?
    '''
    records = conn.execute(query, (cutoff_time,)).fetchall()
    conn.close()

    alerts = []
    for row in records:
        reasons = []
        if row['cpu_percent'] is not None and row['cpu_percent'] > CPU_THRESHOLD:
            reasons.append(f"CPU {row['cpu_percent']}% (>{int(CPU_THRESHOLD)}%)")
        if row['memory_percent'] is not None and row['memory_percent'] > RAM_THRESHOLD:
            reasons.append(f"RAM {row['memory_percent']}% (>{int(RAM_THRESHOLD)}%)")

        if reasons:
            alerts.append({
                "server_id": row['id'],
                "hostname": row['hostname'],
                "cpu_percent": row['cpu_percent'],
                "memory_percent": row['memory_percent'],
                "reasons": reasons
            })

    return jsonify(alerts)


@app.route('/api/settings', methods=['GET', 'POST'])
def manage_settings():
    if not session.get('logged_in'):
        return jsonify({"error": "Unauthorized"}), 401

    if request.method == 'POST':
        data = request.json or {}
        bot_token = data.get('telegram_bot_token', '').strip()
        chat_id = data.get('telegram_chat_id', '').strip()

        with db_write_lock:
            conn = get_db_connection()
            try:
                conn.execute(
                    'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
                    ('telegram_bot_token', bot_token)
                )
                conn.execute(
                    'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
                    ('telegram_chat_id', chat_id)
                )
                conn.commit()
            finally:
                conn.close()

        return jsonify({"status": "saved"})

    conn = get_db_connection()
    rows = conn.execute('SELECT key, value FROM settings').fetchall()
    conn.close()
    settings = {row['key']: row['value'] for row in rows}
    return jsonify({
        "telegram_bot_token": settings.get('telegram_bot_token', ''),
        "telegram_chat_id": settings.get('telegram_chat_id', '')
    })


if __name__ == '__main__':
    # Test Page URL: http://127.0.0.1:5000/ (Login: admin / admin)
    print("Test Dashboard URL: http://127.0.0.1:5000/ (Login: admin / admin)")
    app.run(host='0.0.0.0', port=5000, debug=True)