import socket
import threading
import requests
import urllib3
import time
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

LISTEN_PORT = 25565
TARGET_PORT = 25500
IDLE_TIMEOUT_MINUTES = 20
MC_VERSION_NAME = "1.21.1"
MC_PROTOCOL = 767
MOTD_TITLE = "Crafty Proxy"
MAX_PLAYERS = 5
START_COOLDOWN_SECONDS = 120
STOP_COOLDOWN_SECONDS = 120
LOG_CONNECTIONS = False
STARTUP_GRACE_SECONDS = 180
CONNECT_RETRY_SECONDS = 6
CONNECT_RETRY_INTERVAL = 0.3

urllib3.disable_warnings()

def _load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)

CONFIG = _load_config()
API_TOKEN = CONFIG.get("api_token")
SERVER_ID = CONFIG.get("server_id")
if not API_TOKEN or not SERVER_ID:
    raise RuntimeError("Missing api_token or server_id in config.json")

API_BASE_URL = f"https://127.0.0.1:8443/api/v2/servers/{SERVER_ID}"
headers = {"Authorization": f"Bearer {API_TOKEN}"}
empty_minutes = 0
last_start_request = 0
last_stop_request = 0
last_started_at = 0

def _to_int(value, default=0):
    try:
        if isinstance(value, str) and value.lower() == "false":
            return default
        return int(value)
    except Exception:
        return default

def _to_float(value, default=0.0):
    try:
        if isinstance(value, str) and value.lower() == "false":
            return default
        return float(value)
    except Exception:
        return default

def _to_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default

LISTEN_PORT = _to_int(CONFIG.get("listen_port"), LISTEN_PORT)
TARGET_PORT = _to_int(CONFIG.get("target_port"), TARGET_PORT)
IDLE_TIMEOUT_MINUTES = _to_int(CONFIG.get("idle_timeout_minutes"), IDLE_TIMEOUT_MINUTES)
MAX_PLAYERS = _to_int(CONFIG.get("max_players"), MAX_PLAYERS)
MC_PROTOCOL = _to_int(CONFIG.get("mc_protocol"), MC_PROTOCOL)
START_COOLDOWN_SECONDS = _to_int(CONFIG.get("start_cooldown_seconds"), START_COOLDOWN_SECONDS)
STOP_COOLDOWN_SECONDS = _to_int(CONFIG.get("stop_cooldown_seconds"), STOP_COOLDOWN_SECONDS)
STARTUP_GRACE_SECONDS = _to_int(CONFIG.get("startup_grace_seconds"), STARTUP_GRACE_SECONDS)
CONNECT_RETRY_SECONDS = _to_int(CONFIG.get("connect_retry_seconds"), CONNECT_RETRY_SECONDS)
CONNECT_RETRY_INTERVAL = _to_float(CONFIG.get("connect_retry_interval"), CONNECT_RETRY_INTERVAL)
LOG_CONNECTIONS = _to_bool(CONFIG.get("log_connections"), LOG_CONNECTIONS)

mc_version_name = CONFIG.get("mc_version_name")
if mc_version_name:
    MC_VERSION_NAME = str(mc_version_name)

motd_title = CONFIG.get("motd_title")
if motd_title:
    MOTD_TITLE = str(motd_title)

def _extract_online_players(data):
    if not isinstance(data, dict):
        return 0
    if "online" in data:
        return _to_int(data.get("online"), 0)
    if "online_players" in data:
        return _to_int(data.get("online_players"), 0)
    if "players" in data:
        return _to_int(data.get("players"), 0)
    return 0

def _read_exact(sock, length):
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            return None
        data += chunk
    return data

def _read_varint_from_socket(sock):
    num = 0
    shift = 0
    raw = b""
    while True:
        b = sock.recv(1)
        if not b:
            return None, raw
        raw += b
        val = b[0]
        num |= (val & 0x7F) << shift
        if (val & 0x80) == 0:
            return num, raw
        shift += 7
        if shift > 35:
            return None, raw

def _read_varint_from_bytes(data, index):
    num = 0
    shift = 0
    while True:
        if index >= len(data):
            return None, index
        val = data[index]
        index += 1
        num |= (val & 0x7F) << shift
        if (val & 0x80) == 0:
            return num, index
        shift += 7
        if shift > 35:
            return None, index

def _encode_varint(value):
    out = b""
    while True:
        b = value & 0x7F
        value >>= 7
        if value != 0:
            out += bytes([b | 0x80])
        else:
            out += bytes([b])
            break
    return out

def _send_packet(sock, packet_id, payload_bytes):
    data = _encode_varint(packet_id) + payload_bytes
    packet = _encode_varint(len(data)) + data
    sock.sendall(packet)

def _handle_status_request(sock, description_text, version_name="Status", online_players=0, max_players=0):
    length, _raw = _read_varint_from_socket(sock)
    if length is None:
        return
    payload = _read_exact(sock, length)
    if payload is None:
        return

    index = 0
    packet_id, index = _read_varint_from_bytes(payload, index)
    if packet_id != 0x00:
        return

    if isinstance(description_text, dict):
        description = description_text
    else:
        description = {"text": description_text}

    status = {
        "version": {"name": f"{version_name} ({MC_VERSION_NAME})", "protocol": MC_PROTOCOL},
        "players": {"max": max_players, "online": online_players},
        "description": description,
    }
    status_json = json.dumps(status, ensure_ascii=False)
    status_bytes = status_json.encode("utf-8")
    _send_packet(sock, 0x00, _encode_varint(len(status_bytes)) + status_bytes)

    length, _raw = _read_varint_from_socket(sock)
    if length is None:
        return
    payload = _read_exact(sock, length)
    if payload is None:
        return
    index = 0
    packet_id, index = _read_varint_from_bytes(payload, index)
    if packet_id == 0x01:
        pong_payload = payload[index:]
        _send_packet(sock, 0x01, pong_payload)

def _handle_status_response(sock, state, description_text=None):
    description = description_text if description_text is not None else _status_description(state)
    online_players = state["players"] if state else 0
    _handle_status_request(
        sock,
        description,
        _version_name(state),
        online_players=online_players,
        max_players=MAX_PLAYERS,
    )
    sock.close()

def _confirm_server_empty():
    try:
        r = requests.get(f"{API_BASE_URL}/stats", headers=headers, verify=False, timeout=5)
        if r.status_code != 200:
            return False
        data = r.json().get('data', {})
        if not data.get('running', False) or data.get('waiting_start', False):
            return False
        players = _extract_online_players(data)
        return players == 0
    except Exception:
        return False

def _get_server_state():
    try:
        r = requests.get(f"{API_BASE_URL}/stats", headers=headers, verify=False, timeout=2)
        if r.status_code != 200:
            return None
        data = r.json().get("data", {})
        now = time.time()
        running = data.get("running", False)
        waiting_start = data.get("waiting_start", False)
        players = _extract_online_players(data)
        joinable = running and not waiting_start and (now - last_started_at >= STARTUP_GRACE_SECONDS)
        return {
            "running": running,
            "waiting_start": waiting_start,
            "players": players,
            "joinable": joinable,
        }
    except Exception:
        return None

def _status_description(state):
    if not state:
        subtitle = "Proxy online. Unable to fetch server status."
        color = "gray"
    elif state["waiting_start"] or (state["running"] and not state["joinable"]):
        subtitle = "Server is starting..."
        color = "yellow"
    elif not state["running"]:
        subtitle = "Server offline. Join to wake it."
        color = "red"
    else:
        subtitle = "Server online."
        color = "green"

    return {
        "text": MOTD_TITLE,
        "color": "gold",
        "bold": True,
        "extra": [
            {"text": "\n"},
            {"text": subtitle, "color": color, "bold": True},
        ],
    }

def _version_name(state):
    return "Online"

def _connect_with_retry(host, port, timeout_seconds):
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, port))
            return sock
        except Exception as e:
            last_error = e
            time.sleep(CONNECT_RETRY_INTERVAL)
    if last_error:
        raise last_error
    raise OSError("Unable to connect")

def _kick_with_message(sock, message):
    _drain_one_packet(sock)
    send_kick_message(sock, message)

def _maybe_start_server():
    global last_start_request, last_started_at
    now = time.time()
    if now - last_start_request < START_COOLDOWN_SECONDS:
        return
    if now - last_started_at < STARTUP_GRACE_SECONDS:
        return
    requests.post(f"{API_BASE_URL}/action/start_server", headers=headers, verify=False)
    last_start_request = now
    last_started_at = now

def _drain_one_packet(sock, timeout_seconds=0.2):
    try:
        sock.settimeout(timeout_seconds)
        length, _raw = _read_varint_from_socket(sock)
        if length is None:
            return
        _read_exact(sock, length)
    except Exception:
        pass
    finally:
        try:
            sock.settimeout(None)
        except Exception:
            pass

def _peek_handshake(sock):
    length, len_raw = _read_varint_from_socket(sock)
    if length is None:
        return None, len_raw
    payload = _read_exact(sock, length)
    if payload is None:
        return None, len_raw
    raw = len_raw + payload

    index = 0
    packet_id, index = _read_varint_from_bytes(payload, index)
    if packet_id is None or packet_id != 0x00:
        return None, raw

    _protocol, index = _read_varint_from_bytes(payload, index)
    if _protocol is None:
        return None, raw

    host_len, index = _read_varint_from_bytes(payload, index)
    if host_len is None or index + host_len > len(payload):
        return None, raw
    index += host_len

    if index + 2 > len(payload):
        return None, raw
    index += 2

    next_state, index = _read_varint_from_bytes(payload, index)
    return next_state, raw

def send_kick_message(client_socket, message):
    try:
        msg_json = json.dumps({"text": message, "color": "yellow", "bold": True}, ensure_ascii=False)
        
        def varint(d):
            o = b''
            while True:
                b = d & 0x7F
                d >>= 7
                if d != 0:
                    o += bytes([b | 0x80])
                else:
                    o += bytes([b])
                    break
            return o

        msg_bytes = msg_json.encode("utf-8")
        data = b'\x00' + varint(len(msg_bytes)) + msg_bytes
        packet = varint(len(data)) + data
        client_socket.send(packet)
    except:
        pass
    finally:
        try:
            client_socket.shutdown(socket.SHUT_WR)
            time.sleep(0.05)
        except Exception:
            pass
        client_socket.close()

def monitor_idle():
    global empty_minutes, last_stop_request, last_started_at
    last_state = None
    print("[Monitor] Checking Crafty API every 60s...")
    while True:
        time.sleep(60)
        try:
            r = requests.get(f"{API_BASE_URL}/stats", headers=headers, verify=False, timeout=5)
            if r.status_code == 200:
                data = r.json().get('data', {})
                now = time.time()
                joinable = data.get('running', False) and not data.get('waiting_start', False)
                joinable = joinable and (now - last_started_at >= STARTUP_GRACE_SECONDS)
                if joinable:
                    players = _extract_online_players(data)
                    if players == 0:
                        empty_minutes += 1
                        state = f"empty:{empty_minutes}"
                        if state != last_state:
                            print(f"[Monitor] Empty: {empty_minutes}/{IDLE_TIMEOUT_MINUTES} min.")
                            last_state = state
                        if empty_minutes >= IDLE_TIMEOUT_MINUTES:
                            now = time.time()
                            if now - last_stop_request >= STOP_COOLDOWN_SECONDS:
                                if _confirm_server_empty():
                                    print("[Monitor] Stopping server...")
                                    requests.post(f"{API_BASE_URL}/action/stop_server", headers=headers, verify=False)
                                    last_stop_request = now
                                    empty_minutes = 0
                                else:
                                    empty_minutes = 0
                    else:
                        state = f"players:{players}"
                        if state != last_state:
                            print(f"[Monitor] Players: {players}. Resetting timer.")
                            last_state = state
                        empty_minutes = 0
        except Exception as e:
            print(f"[Monitor Error] {e}")

def forward(source, destination):
    try:
        while True:
            data = source.recv(4096)
            if not data: break
            destination.sendall(data)
    except: pass
    finally:
        source.close()
        destination.close()

def handle_client(client_socket):
    try:
        next_state = None
        raw_handshake = b""
        try:
            client_socket.settimeout(2)
            next_state, raw_handshake = _peek_handshake(client_socket)
        except Exception:
            pass
        finally:
            client_socket.settimeout(None)

        state = _get_server_state()
        if state and state.get("waiting_start", False):
            if next_state == 2:
                _kick_with_message(client_socket, "§6[Wake] §fServer is starting...\n§7Try again in about 60 seconds!")
            else:
                _handle_status_response(client_socket, state)
            return

        if not (state and state.get("running", False)):
            if next_state == 2:
                print("[Wake] Server is sleeping. Waking it now!")
                _maybe_start_server()
                _kick_with_message(client_socket, "§6[Wake] §fServer is starting...\n§7Try again in about 60 seconds!")
            else:
                _handle_status_response(client_socket, state)
            return

        try:
            server_socket = _connect_with_retry("127.0.0.1", TARGET_PORT, CONNECT_RETRY_SECONDS)
        except Exception:
            if next_state == 2:
                _kick_with_message(client_socket, "§6[Wake] §fServer is starting...\n§7Try again in a moment!")
            else:
                _handle_status_response(client_socket, state, "Server is starting...")
            return

        if raw_handshake:
            server_socket.sendall(raw_handshake)
        
        threading.Thread(target=forward, args=(client_socket, server_socket), daemon=True).start()
        threading.Thread(target=forward, args=(server_socket, client_socket), daemon=True).start()
        if LOG_CONNECTIONS:
            print("[Proxy] Player connected.")
    except:
        client_socket.close()

def start_proxy():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', LISTEN_PORT))
    server.listen(15)
    print(f"[System] Wake Proxy active on port {LISTEN_PORT}")
    threading.Thread(target=monitor_idle, daemon=True).start()

    while True:
        client_sock, addr = server.accept()
        threading.Thread(target=handle_client, args=(client_sock,), daemon=True).start()

if __name__ == "__main__":
    start_proxy()