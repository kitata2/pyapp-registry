# PyApp Registry

Web UI (Streamlit) to keep an inventory of Python programs on a Linux VPS, register them to specific ports, manage them as **systemd user services**, and optionally expose them via **Tailscale Serve**.

## Features

- Inventory of registered apps (name, description, CWD, command, port, status, PID)
- One-click Start / Stop / Restart
- Automatic systemd user unit generation (`~/.config/systemd/user/<name>.service`)
- Restart policies (on-failure / always / no)
- Port occupancy check + next free port suggestion
- Optional Tailscale Serve (`tailscale serve --bg`, with `--set-path` support)
- Recent logs via `journalctl --user`
- SQLite persistence in `~/.app_registry/registry.db`

## Quick Start (on your VPS)

```bash
# Install dependencies
uv add streamlit psutil

# Or run directly
uv run --with streamlit --with psutil streamlit run app_registry.py --server.port 8500
```

Then open `http://localhost:8500` (or expose the registry itself via Tailscale).

### Recommended one-time setup

```bash
# Allow user services to start at boot even without an active login
loginctl enable-linger $USER
```

## Run PyApp Registry as an Auto-Start Service

To keep the registry itself running in the background and start automatically on boot:

```bash
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/pyapp-registry.service << 'EOF'
[Unit]
Description=PyApp Registry (Streamlit)
After=network.target

[Service]
Type=simple
WorkingDirectory=%h/myworks/pyapp-registry
ExecStart=/usr/bin/env uv run streamlit run app_registry.py --server.port 8500 --server.address 0.0.0.0
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

# Adjust WorkingDirectory above if your clone is in a different path.
# If `uv` is not in the default PATH for systemd, replace `/usr/bin/env uv` with the full path (`which uv`).

systemctl --user daemon-reload
systemctl --user enable --now pyapp-registry.service
```

### Check status and logs

```bash
systemctl --user status pyapp-registry.service
journalctl --user -u pyapp-registry.service -f
```

### Optional: expose via Tailscale

```bash
tailscale serve --bg --set-path=/registry 8500
# or
tailscale serve --bg 8500
```

After this the registry will start on boot, restart on failure, and stay running when you log out.

## Typical Workflow

1. Open the **Add / Edit App** tab.
2. Fill in:
   - Name (becomes the systemd unit name)
   - Working directory
   - Full ExecStart command (include the port if the app needs it)
   - Local listen port
   - Optional Tailscale path (e.g. `/trading`)
3. Save → unit file is written, service is enabled and restarted, Tailscale Serve is configured if requested.
4. Manage everything from the **Inventory** tab.

## Tailscale Examples

```bash
# Expose local port 8502 under path /trading
tailscale serve --bg --set-path=/trading http://127.0.0.1:8502

# Check current configuration
tailscale serve status
```

Access inside your tailnet at `https://<your-machine>.<tailnet>.ts.net/trading`.

## Notes

- Runs entirely as your user — no root required.
- Multiple apps can share the same Tailscale hostname via different `--set-path` values.
- The registry itself can be registered as one of the apps.
