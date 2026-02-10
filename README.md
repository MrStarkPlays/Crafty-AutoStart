# Crafty Wake Proxy

A lightweight Minecraft wake proxy for Crafty Controller. It shows a live MOTD status even when the server is offline, wakes the server on join attempts, and forwards traffic once the server is ready.

## Features

- Live MOTD while the server sleeps
- Wake-on-join for offline servers
- Auto-stop after idle timeout

## Requirements

- Python 3
- Crafty Controller v4 (API v2)

## Installation

1) Clone or download the repository.
2) Create `config.json` next to `main.py` (use the example file).

## Configuration

Example `config.json`:

```json
{
  "api_token": "REPLACE_WITH_YOUR_TOKEN",
  "server_id": "REPLACE_WITH_SERVER_ID",
  "idle_timeout_minutes": 20,
  "listen_port": 25565,
  "target_port": 25500,
  "mc_version_name": "1.21.1",
  "mc_protocol": 767,
  "motd_title": "Crafty Proxy",
  "max_players": 5,
  "start_cooldown_seconds": 120,
  "stop_cooldown_seconds": 120,
  "startup_grace_seconds": 180,
  "connect_retry_seconds": 6,
  "connect_retry_interval": 0.3,
  "log_connections": false
}
```

Notes:

- `idle_timeout_minutes` is optional. It controls how long the server can be empty before stopping.
- `listen_port` is the port for the proxy.
- `target_port` is the port of the actual Minecraft server.
- `mc_version_name` and `mc_protocol` control the version shown in the server list.
- `motd_title` and `max_players` control the MOTD and player max shown in the server list.
- `start_cooldown_seconds`, `stop_cooldown_seconds`, `startup_grace_seconds` tune start/stop behavior.
- `connect_retry_seconds` and `connect_retry_interval` tune the wait time for the local server socket.
- `log_connections` enables connection logs when set to true.

## Usage

```bash
python3 main.py
```

## Systemd (optional)

Example unit file:

```
[Unit]
Description=Crafty Wake Proxy Script
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/crafty-proxy
ExecStart=/usr/bin/python3 /opt/crafty-proxy/main.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Reload and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wake_proxy.service
sudo journalctl -u wake_proxy.service -f
```

## License

MIT
