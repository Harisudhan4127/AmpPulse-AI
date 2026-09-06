#!/usr/bin/env python3
"""
AmpPulse AI - one-shot ESP32 + laptop connection script.

Every step needed to get an ESP32 streaming data to this laptop's backend,
with only the Wi-Fi SSID/password required from the user:

  1. Installs backend Python dependencies if missing.
  2. Fixes backend/.env: generates a real DEVICE_PROVISION_KEY if needed.
  3. Starts the FastAPI backend if it isn't already running.
  4. Auto-generates a device_id and registers it -> gets device_key.
  5. Picks the laptop's LAN IP automatically (override with --host-ip).
  6. Writes the real SSID/password/host/id/key into arduino/.../amppulse_esp32.ino.
  7. Uploads the sketch to the ESP32 via arduino-cli (auto-installs it if
     missing and you allow it) or prints manual Arduino IDE steps.
  8. Verifies end-to-end: waits for the first telemetry reading to arrive
     at the backend.

Usage:
    python3 setup_esp32.py                 # interactive menu (recommended)
    python3 setup_esp32.py --gui           # start backend + frontend now
    python3 setup_esp32.py --status        # show current state
    python3 setup_esp32.py --ssid MyWifi --password hunter2   # one-shot full setup
    python3 setup_esp32.py --ssid MyWifi --password hunter2 --install-cli
    python3 setup_esp32.py --port /dev/ttyUSB0 --skip-upload

Only Python 3 standard library is required.
"""
import argparse
import glob
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
ENV_FILE = BACKEND_DIR / ".env"
SKETCH_FILE = ROOT / "arduino" / "amppulse_esp32" / "amppulse_esp32.ino"
BACKEND_LOG = BACKEND_DIR / "uvicorn.log"
FRONTEND_LOG = ROOT / "frontend" / "server.log"

PLACEHOLDERS = {
    "WIFI_SSID": ("YOUR_WIFI_SSID",),
    "WIFI_PASSWORD": ("YOUR_WIFI_PASSWORD",),
    "BACKEND_HOST": ("192.168.1.100",),
    "DEVICE_ID": ("ESP32_001",),
    "DEVICE_KEY": ("PASTE_YOUR_DEVICE_KEY_HERE",),
}

PROVISION_PLACEHOLDERS = (
    "", "change_this_provisioning_key", "insecure-dev-provision-key",
    "PASTE_YOUR_DEVICE_KEY_HERE", "change_this_to_a_long_random_value",
)

DEFINES = {
    "WIFI_SSID": r'#define\s+WIFI_SSID\s+"((?:[^"\\]|\\.)*)"',
    "WIFI_PASSWORD": r'#define\s+WIFI_PASSWORD\s+"((?:[^"\\]|\\.)*)"',
    "BACKEND_HOST": r'#define\s+BACKEND_HOST\s+"((?:[^"\\]|\\.)*)"',
    "BACKEND_PORT": r'#define\s+BACKEND_PORT\s+(\d+)',
    "DEVICE_ID": r'#define\s+DEVICE_ID\s+"((?:[^"\\]|\\.)*)"',
    "DEVICE_KEY": r'#define\s+DEVICE_KEY\s+"((?:[^"\\]|\\.)*)"',
}


def unescape_c(value: str) -> str:
    return re.sub(r'\\(["\\])', r'\1', value)


def log(msg: str) -> None:
    print(f"\n== {msg}")


def c_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------- .env helpers

def read_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def write_env(env: dict) -> None:
    lines = []
    for key in sorted(env.keys()):
        val = env[key]
        comment = ""
        if key == "HOST":
            comment = " --- Server ---\n# Host the backend on all interfaces so the ESP32 can reach it.\n"
        elif key == "DATABASE_URL":
            comment = " --- Database (SQLite file for MVP; swap to Postgres URL for production) ---\n"
        elif key == "JWT_SECRET":
            comment = " --- Security ---\n# Secret used to sign user session (JWT) tokens.\n"
        elif key == "DEVICE_PROVISION_KEY":
            comment = ("# Master key used ONLY to register brand-new devices. The script ships a "
                       "generated value.\n")
        elif key == "CORS_ORIGINS":
            comment = " --- CORS (frontend origins allowed to call this API) ---\n"
        elif key == "SEED_USER_EMAIL":
            comment = " --- Seed demo user (created on first run if it doesn't exist) ---\n"
        lines.append(f"{comment}{key}={val}\n")
    ENV_FILE.write_text("".join(lines))
    print(f"[env] Wrote {ENV_FILE.name} ({len(env)} keys)")


# ---------------------------------------------------------------- HTTP helpers

def http_json(method: str, url: str, data=None, headers: dict | None = None,
              timeout: int = 5):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Content-Type": "application/json",
        **(headers or {}),
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
        return resp.status, (json.loads(raw) if raw else {})


def api_is_up(port: int) -> bool:
    try:
        _, _ = http_json("GET", f"http://127.0.0.1:{port}/api/v1/health")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- environment

def backend_python() -> str:
    """Return the venv interpreter, creating+installing it on first call."""
    venv = BACKEND_DIR / ".venv"
    py = venv / "bin" / "python"
    if not py.exists():
        log("Creating backend virtualenv (.venv)")
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    try:
        subprocess.run([str(py), "-c", "import fastapi, uvicorn"], check=True,
                       capture_output=True)
    except subprocess.CalledProcessError:
        log("Installing backend dependencies into .venv")
        subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip"],
                       check=True)
        subprocess.run([str(py), "-m", "pip", "install", "-r",
                        str(BACKEND_DIR / "requirements.txt")], check=True)
    return str(py)


def start_backend(port: int) -> None:
    if api_is_up(port):
        print(f"[backend] Already running at 127.0.0.1:{port} - reusing it.")
        return
    log(f"Starting backend on 0.0.0.0:{port} (log: {BACKEND_LOG.name})")
    with BACKEND_LOG.open("w") as fh:
        proc = subprocess.Popen(
            [backend_python(), "-m", "uvicorn", "app.main:app",
             "--host", "0.0.0.0", "--port", str(port)],
            cwd=str(BACKEND_DIR), stdout=fh, stderr=subprocess.STDOUT,
        )
    deadline = time.time() + 60
    while time.time() < deadline:
        if api_is_up(port):
            print("[backend] Backend is up.")
            return
        if proc.poll() is not None:
            print(BACKEND_LOG.read_text()[-3000:] if BACKEND_LOG.exists() else "")
            sys.exit("Backend process exited early - see uvicorn.log above.")
        time.sleep(1)
    sys.exit("Backend did not become healthy within 60s - see uvicorn.log.")


# ---------------------------------------------------------------- .ino helpers

def read_sketch_defines() -> dict:
    values = {}
    if not SKETCH_FILE.exists():
        return values
    text = SKETCH_FILE.read_text()
    for name, pattern in DEFINES.items():
        m = re.search(pattern, text)
        if m:
            values[name] = unescape_c(m.group(1))
    return values


def patch_sketch(values: dict) -> None:
    text = SKETCH_FILE.read_text()
    count = 0
    for name, new_value in values.items():
        pattern = DEFINES[name]
        if new_value.lstrip("-").isdigit():
            replace = f'#define {name}  {new_value}'
        else:
            replace = f'#define {name}  "{c_escape(new_value)}"'
        text, n = re.subn(pattern, lambda _m: replace, text, count=1)
        count += n
        if n != 1:
            sys.exit(f"Could not find {name} define in {SKETCH_FILE}")
    SKETCH_FILE.write_text(text)
    print(f"[sketch] Configured {len(values)} values in {SKETCH_FILE}")


# ---------------------------------------------------------------- networking

def detect_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def find_serial_port() -> str:
    for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return str(hits[0])
    return ""


# ---------------------------------------------------------------- provisioning

def ensure_provision_key(env: dict) -> tuple[str, bool]:
    key = env.get("DEVICE_PROVISION_KEY", "")
    changed = key in PROVISION_PLACEHOLDERS
    if changed:
        key = f"provision_{secrets.token_urlsafe(24)}"
        env["DEVICE_PROVISION_KEY"] = key
        print(f"[env] Generated new DEVICE_PROVISION_KEY: {key}")
    if env.get("JWT_SECRET", "") in PROVISION_PLACEHOLDERS:
        env["JWT_SECRET"] = secrets.token_hex(32)
        print("[env] Generated new JWT_SECRET")
        changed = True
    if changed:
        write_env(env)
    return key, changed


def register_device(port: int, provision_key: str, device_id: str,
                    name: str) -> str:
    log(f"Registering device {device_id} with the backend")
    try:
        _, resp = http_json(
            "POST", f"http://127.0.0.1:{port}/api/v1/devices/register",
            data={"device_id": device_id, "name": name,
                  "firmware_version": "amppulse_esp32_1.0"},
            headers={"X-Provision-Key": provision_key},
        )
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"registration failed ({e.code}): {e.read().decode()}")
    device_key = resp.get("device_key")
    if not device_key:
        raise RuntimeError(f"unexpected register response: {resp}")
    print(f"[device] id={device_id} key={device_key}")
    return device_key


def find_pid_listening(port: int) -> int | None:
    port_hex = f"{port:04X}"
    inodes = set()
    try:
        with open("/proc/net/tcp") as fh:
            fh.readline()
            for line in fh:
                parts = line.split()
                if (len(parts) > 9 and parts[1].endswith(":" + port_hex)
                        and parts[3] == "0A"):
                    inodes.add(parts[9])
    except OSError:
        return None
    if not inodes:
        return None
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        fd_dir = f"/proc/{entry}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"{fd_dir}/{fd}")
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in inodes:
                return int(entry)
    return None


def restart_backend(port: int) -> None:
    pid = find_pid_listening(port)
    if pid:
        print(f"[backend] Stopping stale backend (pid {pid}) so the new "
              f"provisioning key takes effect.")
        import signal
        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + 10
        while time.time() < deadline:
            if find_pid_listening(port) is None:
                break
            time.sleep(0.3)
    if api_is_up(port):
        # Process exiting; give it a moment to release the port.
        time.sleep(2)
    start_backend(port)


# ---------------------------------------------------------------- upload

def have_arduino_cli() -> bool:
    return shutil.which("arduino-cli") is not None


def install_arduino_cli() -> str:
    dest_dir = Path.home() / ".local" / "bin"
    dest_dir.mkdir(parents=True, exist_ok=True)
    exe = dest_dir / "arduino-cli"
    if exe.exists():
        return str(exe)
    arch_map = {"x86_64": "64bit", "aarch64": "ARM64", "armv7l": "ARMv7",
                "armv6l": "ARMv6"}
    arch = arch_map.get(platform.machine(), "64bit")
    tag = "v1.1.1"
    file_ver = "1.1.1"
    url = (f"https://github.com/arduino/arduino-cli/releases/download/{tag}/"
           f"arduino-cli_{file_ver}_Linux_{arch}.tar.gz")
    log(f"Downloading arduino-cli {tag} ({arch})")
    tmp = dest_dir / "arduino-cli.tar.gz"
    with urllib.request.urlopen(url, timeout=180) as r, tmp.open("wb") as f:
        shutil.copyfileobj(r, f)
    subprocess.run(["tar", "-xzf", str(tmp), "-C", str(dest_dir), "arduino-cli"],
                   check=True)
    exe.chmod(0o755)
    tmp.unlink()
    return str(exe)


def upload_sketch(cli: str, port: str) -> bool:
    fqbn = "esp32:esp32:esp32"
    sketch_dir = SKETCH_FILE.parent
    espressif_index = "https://espressif.github.io/arduino-esp32/package_esp32_index.json"

    subprocess.run([cli, "config", "init"], capture_output=True)
    # Register the Espressif board index so `core install esp32:esp32:esp32` works.
    subprocess.run([cli, "config", "add",
                    "board_manager.additional_urls", espressif_index],
                   check=True)
    print("[cli] Installing Arduino libraries...")
    subprocess.run([cli, "lib", "install", "ArduinoJson",
                    "DHT sensor library", "Adafruit Unified Sensor"],
                   capture_output=True)
    print("[cli] Installing ESP32 core (first run downloads the toolchain)...")
    subprocess.run([cli, "core", "update-index"], check=True)
    plat = ":".join(fqbn.split(":")[:2])
    subprocess.run([cli, "core", "install", fqbn],
                   capture_output=True)  # best-effort: arduino-cli 1.1.1 can
                                         # report the platform as "invalid item"
                                         # even when it installed fine.
    listed = subprocess.run([cli, "core", "list"], capture_output=True,
                            text=True).stdout
    if plat not in listed:
        print(f"[cli] FATAL: {plat} not installed after `core install`.")
        sys.exit(1)
    print(f"[cli] {plat} ready.")

    log("Compiling sketch (this can take a few minutes)")
    subprocess.run([cli, "compile", "--fqbn", fqbn, str(sketch_dir)], check=True)

    log(f"Uploading to ESP32 on {port}")
    subprocess.run([cli, "upload", "-p", port, "--fqbn", fqbn, str(sketch_dir)],
                   check=True)
    return True


# ---------------------------------------------------------------- verification

def verify_telemetry(port: int, device_id: str, wait_s: int = 90) -> None:
    env = read_env()
    email = env.get("SEED_USER_EMAIL", "demo@amppulse.ai")
    password = env.get("SEED_USER_PASSWORD", "Demo@12345")
    try:
        _, auth = http_json("POST", f"http://127.0.0.1:{port}/api/v1/auth/login",
                            data={"email": email, "password": password})
    except urllib.error.HTTPError as e:
        print(f"  [verify] login failed ({e.code}) - skipping verification\n{e.read().decode()}")
        return
    token = auth.get("access_token")
    if not token:
        print("  [verify] no token returned - skipping verification")
        return
    url = f"http://127.0.0.1:{port}/api/v1/devices/{device_id}/status"
    headers = {"Authorization": f"Bearer {token}"}
    print(f"[verify] Waiting up to {wait_s}s for first telemetry from {device_id}...")
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            _, status = http_json("GET", url, headers=headers)
            latest = status.get("latest_reading")
            if latest and latest.get("created_at"):
                print(f"  [verify] FIRST READING received at {latest['created_at']}: "
                      f"V={latest['voltage']}V I={latest['current']}A "
                      f"P={latest['power']}W T={latest['temperature']}C")
                print("[verify] SUCCESS - ESP32 and laptop are fully connected.")
                return
        except Exception:
            pass
        time.sleep(3)
    print(f"[verify] No telemetry within {wait_s}s. Check: same Wi-Fi network, "
          f"BACKEND_HOST={read_sketch_defines().get('BACKEND_HOST')} reachable "
          f"from the ESP32, and serial monitor for [WiFi]/[Telemetry] logs.")


# ---------------------------------------------------------------- frontend/GUI

def _page_ok(base: str, path: str) -> bool:
    import urllib.request, urllib.error
    try:
        with urllib.request.urlopen(base + path, timeout=2) as r:
            return b"AmpPulse AI" in r.read(4096)
    except Exception:
        return False


def start_frontend_server() -> str:
    for path in ("/frontend/", "/"):
        if _page_ok("http://127.0.0.1:5500", path):
            url = "http://localhost:5500" + ("" if path == "/" else path.rstrip("/"))
            print(f"[frontend] Already serving on {url} (VS Code/Live Server or yours)")
            return url
    for port in range(5500, 5511):
        log(f"Starting frontend server on 0.0.0.0:{port} (log: {FRONTEND_LOG.name})")
        try:
            with FRONTEND_LOG.open("w") as fh:
                subprocess.Popen([sys.executable, "-m", "http.server", str(port),
                                  "--bind", "0.0.0.0"],
                                 cwd=str(FRONTEND_DIR), stdout=fh,
                                 stderr=subprocess.STDOUT)
            time.sleep(1.2)
            if _page_ok(f"http://127.0.0.1:{port}", "/"):
                print(f"[frontend] Frontend is up on http://localhost:{port}")
                return f"http://localhost:{port}"
        except OSError:
            continue
    print("[frontend] Could not start a frontend server - run "
          "`python3 -m http.server 5500 --directory frontend` yourself.")
    return "http://localhost:5500"


def open_browser(url: str) -> None:
    for cmd in (["xdg-open"], ["open"], ["cmd", "/c", "start", ""]):
        exe = shutil.which(cmd[0])
        if exe:
            try:
                subprocess.Popen(cmd + [url],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except OSError:
                continue
    print(f"Open this URL in your browser: {url}")


def run_gui(open_win: bool = True) -> None:
    """Bring everything up: backend + frontend, then open the browser."""
    port = backend_port()
    start_backend(port)
    url = start_frontend_server()
    if open_win:
        open_browser(url)
    print(f"\nFontend : {url}   (login: demo@amppulse.ai / Demo@12345)")
    print(f"API docs: http://localhost:{port}/docs")


# ---------------------------------------------------------------- reusable steps

def backend_port() -> int:
    try:
        return int(read_env().get("PORT", "8000"))
    except ValueError:
        return 8000


def prompt_wifi(existing: dict, ssid_arg=None, password_arg=None) -> tuple[str, str]:
    ssid = ssid_arg or existing.get("WIFI_SSID", "")
    if not ssid or ssid in PLACEHOLDERS["WIFI_SSID"]:
        ssid = input("Wi-Fi SSID: ").strip()
    password = password_arg or existing.get("WIFI_PASSWORD", "")
    if not password or password in PLACEHOLDERS["WIFI_PASSWORD"]:
        password = input("Wi-Fi password: ").strip()
    if not ssid or not password:
        sys.exit("Wi-Fi SSID and password are required.")
    return ssid, password


def resolve_host_ip(existing: dict, host_arg=None) -> str:
    host_ip = host_arg or existing.get("BACKEND_HOST", "")
    if not host_ip or host_ip in PLACEHOLDERS["BACKEND_HOST"]:
        host_ip = detect_lan_ip()
    if not host_ip:
        host_ip = input("Laptop LAN IP (from `ip addr`/`ipconfig`): ").strip()
    if not host_ip:
        sys.exit("Could not determine the laptop's LAN IP.")
    return host_ip


def register_and_configure(ssid_arg=None, password_arg=None, host_arg=None,
                           device_id_arg=None, name="Home Energy Monitor"):
    """Ensure backend is up, ask for Wi-Fi, register a device, and write the
    sketch config. Returns (backend_port, device_id, sketch_values)."""
    if not SKETCH_FILE.exists():
        sys.exit(f"Sketch not found: {SKETCH_FILE}")

    env = read_env()
    port = backend_port()
    provision_key, _key_changed = ensure_provision_key(env)

    if _key_changed:
        # A new provisioning key was written; a backend running with the old
        # key would reject registration. Restart if one is already up.
        if api_is_up(port):
            print("[backend] Provisioning key changed - restarting backend.")
            restart_backend(port)
        else:
            start_backend(port)
    else:
        start_backend(port)

    existing = read_sketch_defines()

    ssid, password = prompt_wifi(existing, ssid_arg, password_arg)
    host_ip = resolve_host_ip(existing, host_arg)
    print(f"[net] Laptop will advertise BACKEND_HOST={host_ip}:{port}")

    device_id = (device_id_arg or existing.get("DEVICE_ID", "")).strip()
    device_key = (existing.get("DEVICE_KEY", "") or "").strip()
    if (device_id and device_id not in PLACEHOLDERS["DEVICE_ID"]
            and device_key and not any(p in device_key for p in ("PASTE",))):
        print(f"[device] Reusing configured device_id={device_id} and its key.")
    else:
        if not device_id or device_id in PLACEHOLDERS["DEVICE_ID"]:
            device_id = f"ESP32_{secrets.token_hex(3).upper()}"
        try:
            device_key = register_device(port, provision_key, device_id, name)
        except RuntimeError as e:
            if "401" not in str(e) or not api_is_up(port):
                sys.exit(str(e))
            print(f"[device] {e}; a stale backend may hold the old provisioning "
                  f"key - restarting it and retrying.")
            restart_backend(port)
            device_key = register_device(port, provision_key, device_id, name)

    values = {
        "WIFI_SSID": ssid,
        "WIFI_PASSWORD": password,
        "BACKEND_HOST": host_ip,
        "BACKEND_PORT": str(port),
        "DEVICE_ID": device_id,
        "DEVICE_KEY": device_key,
    }
    log("Writing configuration into the sketch")
    patch_sketch(values)

    print("\n== Summary")
    print(f"  Wi-Fi        : {ssid}")
    print(f"  Backend      : http://{host_ip}:{port}")
    print(f"  Device ID    : {device_id}")
    print(f"  Device Key   : {device_key}")
    print(f"  Sketch       : {SKETCH_FILE}")
    print("  Wiring (GPIO): relay ch1=26 ch2=27, ZMPT101B=34, DHT22=4")
    print("  Serial       : 115200 baud")
    return port, device_id, values


def do_upload(port_arg=None, install_arg=False) -> bool:
    port = port_arg or find_serial_port()
    if not port:
        print("  Serial port detected: none. Plug in the ESP32 via USB and retry.")
        return False
    if have_arduino_cli():
        return upload_sketch(shutil.which("arduino-cli"), port)
    if install_arg:
        cli = install_arduino_cli()
        return upload_sketch(cli, port)
    print("\nNo arduino-cli or PlatformIO found on this machine.")
    choice = input("Auto-install arduino-cli and upload now? "
                   "(y/N, first run downloads ~1GB of ESP32 toolchains): ").strip().lower()
    if choice in ("y", "yes"):
        cli = install_arduino_cli()
        return upload_sketch(cli, port)
    print("Manual: open the sketch in Arduino IDE (Board: ESP32 Dev Module) "
          "and press Upload.")
    print(f"  Serial port detected: {port}")
    return False


def show_status() -> None:
    port = backend_port()
    sketch = read_sketch_defines() or {}
    up = api_is_up(port)
    print("\n== Current status")
    print(f"  Backend        : {'running at http://127.0.0.1:'+str(port) if up else 'NOT running'}")
    print(f"  Serial port    : {find_serial_port() or 'none detected'}")
    print(f"  Sketch         : {SKETCH_FILE.name or 'missing'}")
    for k in ("WIFI_SSID", "BACKEND_HOST", "BACKEND_PORT", "DEVICE_ID"):
        v = sketch.get(k)
        if v and v not in PLACEHOLDERS.get(k, ()):
            print(f"    {k:<13}: {v}")
    print(f"    DEVICE_KEY  : {'configured' if sketch.get('DEVICE_KEY') and 'PASTE' not in sketch['DEVICE_KEY'] else 'MISSING'}")

    did = sketch.get("DEVICE_ID")
    key = sketch.get("DEVICE_KEY")
    if did and key and "PASTE" not in key and up:
        try:
            env = read_env()
            _, auth = http_json(
                "POST", f"http://127.0.0.1:{port}/api/v1/auth/login",
                data={"email": env.get("SEED_USER_EMAIL", "demo@amppulse.ai"),
                      "password": env.get("SEED_USER_PASSWORD", "Demo@12345")})
            tok = auth["access_token"]
            _, status = http_json(
                "GET", f"http://127.0.0.1:{port}/api/v1/devices/{did}/status",
                headers={"Authorization": f"Bearer {tok}"})
            print(f"  Device {did}    : {status.get('status')} "
                  f"(IP {status.get('ip_address') or 'not reported yet'}, "
                  f"last seen {status.get('last_seen_at') or 'never'})")
        except Exception as e:
            print(f"  Device {did}    : could not query status ({e})")
    elif did:
        if up:
            print(f"  Device {did}    : registered but never reported data yet - "
                  f"flash the firmware and watch the Monitor tab for its IP.")
        else:
            print(f"  Device {did}    : backend is down - start it (menu 4) to "
                  f"check registration.")


# ---------------------------------------------------------------- interactive menu

def menu() -> int:
    print("""
  ⚡ AmpPulse AI — ESP32 + laptop setup
  ─────────────────────────────────────────────
  1) FULL AUTO   : register device (generates ID+key) → configure sketch → upload to ESP32
  2) Register device & configure sketch (asks Wi-Fi name/password)
  3) Upload code to ESP32 (uses current sketch config)
  4) Run GUI : start backend + frontend and open the browser
  5) Configure Wi-Fi credentials in the sketch only
  6) Show current status (backend, device, serial port)
  7) Install arduino-cli toolchain (needed for auto-upload)
  8) Quit
  ─────────────────────────────────────────────
""")
    while True:
        try:
            raw = input("Choose an option [1-8] (Enter=4): ")
        except (KeyboardInterrupt, EOFError):
            print("\nBye 👋")
            return 0
        choice = raw.strip() or "4"
        try:
            if choice == "1":
                port, device_id, _ = register_and_configure()
                if do_upload(install_arg=False):
                    verify_telemetry(port, device_id)
            elif choice == "2":
                register_and_configure()
            elif choice == "3":
                if not SKETCH_FILE.exists():
                    sys.exit(f"Sketch not found: {SKETCH_FILE}")
                do_upload()
            elif choice == "4":
                run_gui()
            elif choice == "5":
                existing = read_sketch_defines()
                ssid, password = prompt_wifi(existing)
                patch_sketch({"WIFI_SSID": ssid, "WIFI_PASSWORD": password})
                print("Wi-Fi credentials written to the sketch.")
            elif choice == "6":
                show_status()
            elif choice == "7":
                cli = install_arduino_cli()
                print(f"arduino-cli ready at {cli}")
            elif choice == "8":
                print("\nBye 👋")
                return 0
            else:
                print("Invalid option. Enter a number 1-8.")
                continue
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return 1
        except SystemExit as e:
            print(f"Stopped: {e}")
        except Exception as e:
            print(f"Error: {e}")
        print()


# ---------------------------------------------------------------- main flow

def main() -> int:
    ap = argparse.ArgumentParser(
        description="AmpPulse AI ESP32 setup - run with no arguments for the "
                    "interactive menu, or use the flags below for one-shot CLI.")
    ap.add_argument("--menu", action="store_true",
                    help="Force the interactive menu")
    ap.add_argument("--status", action="store_true",
                    help="Show current backend/device/serial status")
    ap.add_argument("--gui", action="store_true",
                    help="Start backend + frontend and open the browser")
    ap.add_argument("--ssid", help="Wi-Fi SSID (prompted if not given)")
    ap.add_argument("--password", help="Wi-Fi password (prompted if not given)")
    ap.add_argument("--host-ip", help="Laptop LAN IP the ESP32 will talk to "
                                      "(auto-detected if not given)")
    ap.add_argument("--device-id", help="Override auto-generated device_id")
    ap.add_argument("--name", default="Home Energy Monitor", help="Device name")
    ap.add_argument("--port", help="ESP32 serial port (auto-detected)")
    ap.add_argument("--skip-upload", action="store_true",
                    help="Configure everything but don't upload")
    ap.add_argument("--install-cli", action="store_true",
                    help="Auto-install arduino-cli and upload without prompting")
    ap.add_argument("--no-verify", action="store_true",
                    help="Skip end-to-end telemetry verification")
    args = ap.parse_args()

    if args.menu or len(sys.argv) == 1:
        return menu()

    if args.status:
        show_status()
        return 0

    if args.gui:
        run_gui()
        return 0

    port, device_id, _values = register_and_configure(
        ssid_arg=args.ssid, password_arg=args.password,
        host_arg=args.host_ip, device_id_arg=args.device_id, name=args.name)

    # ----- Upload -----
    uploaded = False
    if args.skip_upload:
        print("\nSkipped upload. Open the sketch in Arduino IDE and press Upload.")
    else:
        uploaded = do_upload(args.port, install_arg=args.install_cli)

    if uploaded and not args.no_verify:
        verify_telemetry(port, device_id)

    print("\nAll done. Dashboard: http://localhost:5500 "
          "(backend also at http://localhost:%d/docs)" % port)
    return 0


if __name__ == "__main__":
    sys.exit(main())