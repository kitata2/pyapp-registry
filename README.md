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
