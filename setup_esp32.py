#!/usr/bin/env python3
"""
AmpPulse AI - one-shot ESP32 + laptop connection script.

The standalone sketch (arduino/amppulse_esp32/amppulse_esp32.ino) runs its OWN
web server on port 80. The dashboard connects to it DIRECTLY by IP for live
telemetry + relay control, and uses the FastAPI backend for login, analytics,
history and bill prediction. This script gets everything up with the only
input being the Wi-Fi SSID/password:

  1. Installs backend Python dependencies if missing.
  2. Fixes backend/.env: generates a real JWT_SECRET if needed.
  3. Starts the FastAPI backend if it isn't already running.
  4. Writes the real SSID/password into arduino/.../amppulse_esp32.ino.
  5. Uploads the sketch to the ESP32 via arduino-cli (auto-installs it if
     missing and you allow it) or prints manual Arduino IDE steps.
  6. Verifies end-to-end: probes the ESP32's own /data endpoint over the
     local Wi-Fi and prints the live reading.

Usage:
    python3 setup_esp32.py                           # interactive menu (recommended)
    python3 setup_esp32.py --gui                     # start backend + frontend now
    python3 setup_esp32.py --status                  # show current state
    python3 setup_esp32.py --ssid MyWifi --password hunter2   # config + upload
    python3 setup_esp32.py --ssid MyWifi --password hunter2 --install-cli
    python3 setup_esp32.py --esp32-ip 192.168.1.7 --skip-upload   # just verify /data

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

SKETCH_PLACEHOLDERS = {
    "WIFI_SSID": ("HOME", "YOUR_WIFI_SSID", ""),
    "WIFI_PASSWORD": ("Home@4127", "YOUR_WIFI_PASSWORD", ""),
}

# The standalone sketch stores Wi-Fi credentials as `const char* WIFI_SSID = "...";`
SKETCH_DEFINES = {
    "WIFI_SSID": r'const char\* WIFI_SSID\s*=\s*"((?:[^"\\]|\\.)*)"',
    "WIFI_PASSWORD": r'const char\* WIFI_PASSWORD\s*=\s*"((?:[^"\\]|\\.)*)"',
}

PLACEHOLDER_PREFIXES = frozenset([
    "CHANGE_", "YOUR_", "PASTE", "DEMO", "Home@4127",
])


def log(msg: str) -> None:
    print(f"\n== {msg}")


def unescape_c(value: str) -> str:
    return re.sub(r'\\(["\\])', r'\1', value)


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
            comment = " --- Server ---\n# Host the backend on all interfaces so the dashboard / devices can reach it.\n"
        elif key == "DATABASE_URL":
            comment = " --- Database (SQLite file for MVP; swap to Postgres URL for production) ---\n"
        elif key == "JWT_SECRET":
            comment = " --- Security ---\n# Secret used to sign user session (JWT) tokens.\n"
        elif key == "DEVICE_PROVISION_KEY":
            comment = ("# Master key used ONLY to register brand-new devices for the backend "
                       "telemetry path. The standalone sketch does NOT need it.\n")
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


def ensure_env_secrets(env: dict) -> bool:
    """Generate real credentials if the placeholders are still in place."""
    changed = False
    if env.get("JWT_SECRET", "").startswith("change_"):
        env["JWT_SECRET"] = secrets.token_hex(32)
        print("[env] Generated new JWT_SECRET")
        changed = True
    if env.get("DEVICE_PROVISION_KEY", "").startswith("change_"):
        env["DEVICE_PROVISION_KEY"] = f"provision_{secrets.token_urlsafe(24)}"
        print("[env] Generated DEVICE_PROVISION_KEY (backend telemetry path only)")
        changed = True
    if changed:
        write_env(env)
    return changed


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


# ---------------------------------------------------------------- sketch helpers

def read_sketch_defines() -> dict:
    values = {}
    if not SKETCH_FILE.exists():
        return values
    text = SKETCH_FILE.read_text()
    for name, pattern in SKETCH_DEFINES.items():
        m = re.search(pattern, text)
        if m:
            values[name] = unescape_c(m.group(1))
    return values


def patch_sketch(values: dict) -> None:
    text = SKETCH_FILE.read_text()
    count = 0
    for name, new_value in values.items():
        pattern = SKETCH_DEFINES[name]
        replace = f'const char* {name} = "{c_escape(new_value)}";'
        text, n = re.subn(pattern, lambda _m: replace, text, count=1)
        count += n
        if n != 1:
            sys.exit(f"Could not find {name} in {SKETCH_FILE}")
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


# ---------------------------------------------------------------- ESP32 verify

def probe_esp32(ip: str, port: int = 80, timeout: int = 4) -> dict:
    """GET http://<ip>:<port>/data on the ESP32's own web server."""
    url = f"http://{ip}:{port}/data"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body = resp.read().decode().strip()
    return json.loads(body) if body else {}


def verify_esp32(ip: str, wait_s: int = 30) -> None:
    """Wait until the ESP32's /data endpoint answers, then print its reading."""
    print(f"[verify] Probing ESP32 web server at http://{ip}:80/data ...")
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            data = probe_esp32(ip)
            if "voltage" in data:
                print(f"  [verify] Live reading: V={data.get('voltage')} V  "
                      f"I={data.get('current')} A  P={data.get('power')} W  "
                      f"T={data.get('temperature')} C  "
                      f"relay1={data.get('relay1')} relay2={data.get('relay2')}")
                print("[verify] SUCCESS - ESP32 web server is reachable and serving data.")
                return
        except Exception:
            pass
        time.sleep(2)
    print(f"[verify] No response from {ip} within {wait_s}s.")
    print("  Check: same Wi-Fi network, ESP32 powered, and read its IP from the")
    print("  Serial Monitor (115200 baud) - then retry or use the dashboard's")
    print("  \"ESP32 Connect\" bar to enter it.")


def prompt_esp32_ip(existing: dict, arg=None) -> str:
    ip = arg or ""
    if not ip:
        hint = ""
        if "WIFI_SSID" in existing:
            hint = " (tip: read the IP from the Serial Monitor, 115200 baud)"
        ip = input(f"ESP32 IP address{hint}: ").strip()
    if not ip:
        sys.exit("An ESP32 IP address is required to verify the connection.")
    return ip


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


def backend_port() -> int:
    try:
        return int(read_env().get("PORT", "8000"))
    except ValueError:
        return 8000


def run_gui(open_win: bool = True) -> None:
    """Bring everything up: backend + frontend, then open the browser."""
    port = backend_port()
    start_backend(port)
    url = start_frontend_server()
    if open_win:
        open_browser(url)
    print(f"\nFrontend: {url}   (login: demo@amppulse.ai / Demo@12345)")
    print(f"API docs: http://localhost:{port}/docs")
    print("Then enter your ESP32's IP in the dashboard's \"ESP32 Connect\" bar.")


# ---------------------------------------------------------------- reusable steps

def prompt_wifi(existing: dict, ssid_arg=None, password_arg=None) -> tuple[str, str]:
    ssid = ssid_arg or existing.get("WIFI_SSID", "")
    if not ssid or ssid in SKETCH_PLACEHOLDERS["WIFI_SSID"]:
        ssid = input("Wi-Fi SSID: ").strip()
    password = password_arg or existing.get("WIFI_PASSWORD", "")
    if not password or password in SKETCH_PLACEHOLDERS["WIFI_PASSWORD"]:
        password = input("Wi-Fi password: ").strip()
    if not ssid or not password:
        sys.exit("Wi-Fi SSID and password are required.")
    return ssid, password


def configure_sketch(ssid_arg=None, password_arg=None) -> dict:
    """Write the real Wi-Fi credentials into the standalone sketch."""
    if not SKETCH_FILE.exists():
        sys.exit(f"Sketch not found: {SKETCH_FILE}")

    existing = read_sketch_defines()
    ssid, password = prompt_wifi(existing, ssid_arg, password_arg)

    values = {"WIFI_SSID": ssid, "WIFI_PASSWORD": password}
    log("Writing configuration into the sketch")
    patch_sketch(values)

    print("\n== Summary")
    print(f"  Wi-Fi   : {ssid}")
    print(f"  Sketch  : {SKETCH_FILE}")
    print("  Wiring (GPIO): relay ch1=25 ch2=26 (active-LOW), ZMPT101B=34, DHT22=4")
    print("  Serial  : 115200 baud (read the assigned IP from the Serial Monitor)")
    return values


def show_status() -> None:
    port = backend_port()
    sketch = read_sketch_defines() or {}
    up = api_is_up(port)
    lan = detect_lan_ip()
    print("\n== Current status")
    print(f"  Backend     : {'running at http://127.0.0.1:' + str(port) if up else 'NOT running'}")
    print(f"  Frontend    : http://localhost:5500 (start with menu 4)")
    if lan:
        print(f"  Laptop LAN IP: {lan}")
    print(f"  Serial port : {find_serial_port() or 'none detected'}")
    print(f"  Sketch      : {SKETCH_FILE}")
    for k in ("WIFI_SSID", "WIFI_PASSWORD"):
        v = sketch.get(k)
        if v and v not in SKETCH_PLACEHOLDERS.get(k, ()):
            masked = v if k == "WIFI_SSID" else "*****"
            print(f"    {k:<12}: {masked}")
    if not sketch.get("WIFI_SSID") or sketch["WIFI_SSID"] in SKETCH_PLACEHOLDERS["WIFI_SSID"]:
        print("    WIFI_SSID  : (not configured yet - menu 2)")
    print("  ESP32 web   : connect from the dashboard via its IP (read from Serial)")


# ---------------------------------------------------------------- interactive menu

def menu() -> int:
    print("""
  ⚡ AmpPulse AI — ESP32 + laptop setup
  ─────────────────────────────────────────────
  1) FULL AUTO : configure Wi-Fi in sketch → upload → verify direct connection
  2) Configure Wi-Fi credentials in the sketch
  3) Upload code to ESP32 (uses current sketch config)
  4) Run GUI   : start backend + frontend and open the browser
  5) Verify ESP32 web server (/data) by IP
  6) Show current status (backend, serial port, sketch Wi-Fi)
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
                configure_sketch()
                if do_upload():
                    ip = prompt_esp32_ip(read_sketch_defines())
                    verify_esp32(ip)
            elif choice == "2":
                configure_sketch()
            elif choice == "3":
                if not SKETCH_FILE.exists():
                    sys.exit(f"Sketch not found: {SKETCH_FILE}")
                do_upload()
            elif choice == "4":
                run_gui()
            elif choice == "5":
                ip = prompt_esp32_ip(read_sketch_defines())
                verify_esp32(ip)
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
                    help="Show current backend/frontend/serial status")
    ap.add_argument("--gui", action="store_true",
                    help="Start backend + frontend and open the browser")
    ap.add_argument("--ssid", help="Wi-Fi SSID (prompted if not given)")
    ap.add_argument("--password", help="Wi-Fi password (prompted if not given)")
    ap.add_argument("--esp32-ip", help="ESP32 IP to verify /data against")
    ap.add_argument("--port", help="ESP32 serial port (auto-detected)")
    ap.add_argument("--skip-upload", action="store_true",
                    help="Configure Wi-Fi in the sketch but don't upload")
    ap.add_argument("--install-cli", action="store_true",
                    help="Auto-install arduino-cli and upload without prompting")
    ap.add_argument("--no-verify", action="store_true",
                    help="Skip the /data verification after upload")
    args = ap.parse_args()

    if args.menu or len(sys.argv) == 1:
        return menu()

    if args.status:
        show_status()
        return 0

    if args.gui:
        run_gui()
        return 0

    # Ensure the backend .env is ready (GUI relies on it) even for one-shot runs.
    ensure_env_secrets(read_env())

    configure_sketch(ssid_arg=args.ssid, password_arg=args.password)

    uploaded = False
    if args.skip_upload:
        print("\nSkipped upload. Open the sketch in Arduino IDE and press Upload.")
    else:
        uploaded = do_upload(args.port, install_arg=args.install_cli)

    if uploaded and not args.no_verify:
        verify_esp32(prompt_esp32_ip(read_sketch_defines(), args.esp32_ip))

    print("\nNext: open http://localhost:5500, log in (demo@amppulse.ai / Demo@12345),")
    print("and enter the ESP32's IP in the \"ESP32 Connect\" bar to start the live dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())