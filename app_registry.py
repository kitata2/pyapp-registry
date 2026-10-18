"""
PyApp Registry — Web UI to inventory and manage Python apps on a Linux VPS
with systemd user services and optional Tailscale Serve exposure.

Run with:
  uv run --with streamlit --with psutil streamlit run app_registry.py --server.port 8500
"""

import os
import sqlite3
import subprocess
import socket
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

import streamlit as st
import psutil

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HOME = Path.home()
DATA_DIR = HOME / ".app_registry"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "registry.db"
SYSTEMD_USER_DIR = HOME / ".config" / "systemd" / "user"
SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PORT_START = 8000
PORT_RANGE_END = 9000

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS apps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                cwd TEXT NOT NULL,
                command TEXT NOT NULL,
                port INTEGER NOT NULL UNIQUE,
                restart_policy TEXT DEFAULT 'on-failure',
                tailscale_enabled INTEGER DEFAULT 0,
                tailscale_path TEXT,
                tailscale_https_port INTEGER,
                enabled INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()

init_db()

# ---------------------------------------------------------------------------
# System helpers
# ---------------------------------------------------------------------------
def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0

def find_next_free_port(start: int = DEFAULT_PORT_START) -> int:
    for p in range(start, PORT_RANGE_END):
        if not is_port_in_use(p):
            # Also check if already assigned in DB
            with get_db() as conn:
                if conn.execute("SELECT 1 FROM apps WHERE port = ?", (p,)).fetchone() is None:
                    return p
    return start

def run_cmd(cmd: List[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)

def systemctl_user(*args: str) -> subprocess.CompletedProcess:
    return run_cmd(["systemctl", "--user"] + list(args))

def get_service_status(name: str) -> str:
    result = systemctl_user("is-active", f"{name}.service")
    return result.stdout.strip() or "unknown"

def get_service_pid(name: str) -> Optional[int]:
    result = systemctl_user("show", "-p", "MainPID", "--value", f"{name}.service")
    try:
        pid = int(result.stdout.strip())
        return pid if pid > 0 else None
    except ValueError:
        return None

def unit_file_path(name: str) -> Path:
    return SYSTEMD_USER_DIR / f"{name}.service"

def generate_unit_file(app: Dict[str, Any]) -> str:
    restart = app["restart_policy"] if app["restart_policy"] in ("always", "on-failure", "on-abnormal", "on-abort", "on-success", "no") else "on-failure"
    content = f"""[Unit]
Description={app['description'] or app['name']}
After=network.target

[Service]
Type=simple
WorkingDirectory={app['cwd']}
ExecStart={app['command']}
Restart={restart}
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""
    return content

def write_unit_file(app: Dict[str, Any]):
    path = unit_file_path(app["name"])
    path.write_text(generate_unit_file(app))
    return path

def reload_and_manage(name: str, action: str):
    systemctl_user("daemon-reload")
    if action == "enable":
        systemctl_user("enable", f"{name}.service")
    elif action == "disable":
        systemctl_user("disable", f"{name}.service")
    elif action in ("start", "stop", "restart"):
        systemctl_user(action, f"{name}.service")

def tailscale_serve(app: Dict[str, Any], enable: bool):
    """Configure or remove Tailscale Serve for this app."""
    if not enable:
        # Best-effort remove. User may need to clean manually if complex.
        path = app.get("tailscale_path") or f"/{app['name']}"
        run_cmd(["tailscale", "serve", "--https=443", path, "off"])
        return

    # Enable
    local_target = f"http://127.0.0.1:{app['port']}"
    ts_path = app.get("tailscale_path") or f"/{app['name']}"
    https_port = app.get("tailscale_https_port")

    cmd = ["tailscale", "serve", "--bg"]
    if https_port and https_port != 443:
        cmd.extend(["--https", str(https_port)])
    if ts_path and ts_path != "/":
        cmd.extend(["--set-path", ts_path])
    cmd.append(local_target)

    result = run_cmd(cmd)
    return result

# ---------------------------------------------------------------------------
# DB CRUD
# ---------------------------------------------------------------------------
def list_apps() -> List[Dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM apps ORDER BY name").fetchall()
        return [dict(r) for r in rows]

def get_app(name: str) -> Optional[Dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM apps WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

def upsert_app(data: Dict[str, Any]):
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM apps WHERE name = ?", (data["name"],)).fetchone()
        if existing:
            data["updated_at"] = now
            cols = ", ".join(f"{k}=?" for k in data if k != "name")
            vals = [data[k] for k in data if k != "name"] + [data["name"]]
            conn.execute(f"UPDATE apps SET {cols} WHERE name = ?", vals)
        else:
            data["created_at"] = now
            data["updated_at"] = now
            cols = ", ".join(data.keys())
            placeholders = ", ".join("?" * len(data))
            conn.execute(f"INSERT INTO apps ({cols}) VALUES ({placeholders})", list(data.values()))
        conn.commit()

def delete_app(name: str):
    # Stop and disable service, remove unit file
    systemctl_user("stop", f"{name}.service")
    systemctl_user("disable", f"{name}.service")
    unit = unit_file_path(name)
    if unit.exists():
        unit.unlink()
    systemctl_user("daemon-reload")
    # Remove Tailscale if present
    app = get_app(name)
    if app and app.get("tailscale_enabled"):
        tailscale_serve(app, enable=False)
    with get_db() as conn:
        conn.execute("DELETE FROM apps WHERE name = ?", (name,))
        conn.commit()

# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------
st.set_page_config(page_title="PyApp Registry", page_icon="📦", layout="wide")
st.title("📦 PyApp Registry")
st.caption("Inventory, systemd user services, and Tailscale Serve for your Python apps on this VPS")

tab_inventory, tab_add, tab_settings = st.tabs(["Inventory", "Add / Edit App", "Settings & Help"])

# ----- Inventory Tab -----
with tab_inventory:
    apps = list_apps()
    if not apps:
        st.info("No apps registered yet. Use the **Add / Edit App** tab to register your first one.")
    else:
        for app in apps:
            status = get_service_status(app["name"])
            pid = get_service_pid(app["name"])
            port_status = "🟢" if is_port_in_use(app["port"]) else "🔴"

            with st.container(border=True):
                col1, col2, col3, col4 = st.columns([3, 2, 2, 3])
                with col1:
                    st.markdown(f"**{app['name']}**")
                    st.caption(app.get("description") or "")
                    st.code(app["command"], language="bash")
                with col2:
                    st.metric("Port", f"{app['port']} {port_status}")
                    st.caption(f"CWD: `{app['cwd']}`")
                with col3:
                    color = "green" if status == "active" else "red" if status == "failed" else "orange"
                    st.markdown(f"Status: :{color}[{status}]")
                    st.caption(f"PID: {pid or '—'}")
                    if app.get("tailscale_enabled"):
                        ts_path = app.get("tailscale_path") or f"/{app['name']}"
                        st.caption(f"Tailscale: `{ts_path}`")
                with col4:
                    c1, c2, c3 = st.columns(3)
                    if c1.button("Start", key=f"start_{app['name']}"):
                        write_unit_file(app)
                        reload_and_manage(app["name"], "start")
                        if app.get("tailscale_enabled"):
                            tailscale_serve(app, True)
                        st.rerun()
                    if c2.button("Stop", key=f"stop_{app['name']}"):
                        reload_and_manage(app["name"], "stop")
                        st.rerun()
                    if c3.button("Restart", key=f"restart_{app['name']}"):
                        write_unit_file(app)
                        reload_and_manage(app["name"], "restart")
                        if app.get("tailscale_enabled"):
                            tailscale_serve(app, True)
                        st.rerun()

                    c4, c5, c6 = st.columns(3)
                    if c4.button("Logs", key=f"logs_{app['name']}"):
                        st.session_state["show_logs"] = app["name"]
                    if c5.button("Edit", key=f"edit_{app['name']}"):
                        st.session_state["edit_name"] = app["name"]
                        st.switch_page  # just set state; user goes to Add tab
                    if c6.button("Delete", key=f"del_{app['name']}", type="primary"):
                        delete_app(app["name"])
                        st.rerun()

        # Logs expander
        if "show_logs" in st.session_state:
            name = st.session_state["show_logs"]
            st.subheader(f"Recent logs for {name}")
            result = run_cmd(["journalctl", "--user", "-u", f"{name}.service", "-n", "80", "--no-pager", "-o", "cat"])
            st.code(result.stdout or result.stderr or "(no logs)", language="text")
            if st.button("Close logs"):
                del st.session_state["show_logs"]
                st.rerun()

# ----- Add / Edit Tab -----
with tab_add:
    edit_name = st.session_state.get("edit_name")
    existing = get_app(edit_name) if edit_name else None

    st.subheader("Edit App" if existing else "Register New App")

    with st.form("app_form"):
        name = st.text_input("Name (systemd unit name, no spaces)", value=existing["name"] if existing else "", disabled=bool(existing))
        description = st.text_input("Description", value=existing.get("description") or "" if existing else "")
        cwd = st.text_input("Working Directory", value=existing["cwd"] if existing else str(HOME))
        command = st.text_area("ExecStart command", value=existing["command"] if existing else "uv run python main.py", height=80,
                               help="Full command, e.g. `/home/user/.venv/bin/python -m streamlit run app.py --server.port 8502`")
        suggested = find_next_free_port()
        port = st.number_input("Local listen port", min_value=1024, max_value=65535,
                               value=existing["port"] if existing else suggested)
        restart_policy = st.selectbox("Restart policy", ["on-failure", "always", "no"],
                                      index=["on-failure", "always", "no"].index(existing.get("restart_policy", "on-failure")) if existing else 0)

        st.markdown("**Tailscale Serve**")
        ts_enabled = st.checkbox("Expose via Tailscale Serve", value=bool(existing.get("tailscale_enabled")) if existing else False)
        ts_path = st.text_input("Tailscale path (optional)", value=existing.get("tailscale_path") or "" if existing else "",
                                help="e.g. /trading or leave empty for /name. Use / for root (only one app can own root).")
        ts_https_port = st.number_input("Tailscale HTTPS port (optional, default 443)", min_value=0, max_value=65535,
                                        value=existing.get("tailscale_https_port") or 0 if existing else 0)

        submitted = st.form_submit_button("Save & Apply")

        if submitted:
            if not name or " " in name or not name.isidentifier():
                st.error("Name must be a valid identifier (letters, numbers, underscore, no spaces).")
            elif not Path(cwd).is_dir():
                st.error("Working directory does not exist.")
            elif not command.strip():
                st.error("Command is required.")
            else:
                # Port conflict check (allow same if editing)
                with get_db() as conn:
                    conflict = conn.execute("SELECT name FROM apps WHERE port = ? AND name != ?", (port, name)).fetchone()
                if conflict:
                    st.error(f"Port {port} is already assigned to '{conflict['name']}'.")
                else:
                    data = {
                        "name": name,
                        "description": description,
                        "cwd": cwd,
                        "command": command.strip(),
                        "port": int(port),
                        "restart_policy": restart_policy,
                        "tailscale_enabled": 1 if ts_enabled else 0,
                        "tailscale_path": ts_path.strip() or None,
                        "tailscale_https_port": int(ts_https_port) if ts_https_port else None,
                        "enabled": 1,
                    }
                    upsert_app(data)
                    app = get_app(name)
                    write_unit_file(app)
                    reload_and_manage(name, "enable")
                    reload_and_manage(name, "restart")
                    if ts_enabled:
                        tailscale_serve(app, True)
                    st.success(f"App '{name}' saved and service restarted.")
                    if "edit_name" in st.session_state:
                        del st.session_state["edit_name"]
                    st.rerun()

    if existing and st.button("Cancel Edit"):
        del st.session_state["edit_name"]
        st.rerun()

# ----- Settings Tab -----
with tab_settings:
    st.subheader("Quick Commands")
    st.code("""# Enable lingering so user services start at boot without login
loginctl enable-linger $USER

# Check Tailscale Serve status
tailscale serve status

# View all user services
systemctl --user list-units --type=service

# This registry itself (example)
uv run --with streamlit --with psutil streamlit run app_registry.py --server.port 8500
""", language="bash")

    st.subheader("How it works")
    st.markdown("""
1. **Register** an app with its working directory, full start command, and local port.
2. A **systemd user unit** is written to `~/.config/systemd/user/<name>.service`.
3. The service is enabled and started with `systemctl --user`.
4. Optionally **Tailscale Serve** is configured with `--bg` so the app is available inside your tailnet
   (usually at `https://<machine>.<tailnet>.ts.net/<path>`).
5. Inventory, status, PID, port occupancy, and recent journalctl logs are shown in the UI.

**Tips**
- Put the exact port in the command if the app needs it (Streamlit: `--server.port 8502`).
- Prefer `--set-path` for multiple apps instead of fighting over port 443.
- Run this registry itself as a systemd service or keep it in a screen/tmux if you prefer.
- All commands run as your user — no root required.
""")

    st.subheader("Current free port suggestion")
    st.metric("Next free port", find_next_free_port())

    if st.button("Refresh systemd daemon"):
        systemctl_user("daemon-reload")
        st.success("daemon-reload done")
