from __future__ import annotations

import argparse
import asyncio
import atexit
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Optional

BACKEND_PROCESS: Optional[subprocess.Popen] = None
BACKEND_LOG_HANDLE = None
SHOULD_STOP_BACKEND = False


def resolve_backend_dir() -> Path:
    candidates: list[Path] = []
    if not getattr(sys, "frozen", False):
        candidates.append(Path(__file__).resolve().parent)

    exe_dir = Path(sys.executable).resolve().parent
    candidates.extend([exe_dir, exe_dir.parent, Path.cwd(), Path.cwd() / "backend"])

    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)

        if (resolved / "main.py").exists() and (resolved / "static").exists():
            return resolved
        if (resolved / "backend" / "main.py").exists() and (resolved / "backend" / "static").exists():
            return resolved / "backend"

    raise RuntimeError("Unable to locate backend directory containing main.py and static/.")


def load_env_defaults(backend_dir: Path) -> Optional[Path]:
    configured = os.getenv("CONFIG_ENV_FILE", "").strip()
    env_path = backend_dir / ".env"
    prod_path = backend_dir / ".env.production"

    if configured:
        candidates = [Path(configured)]
    else:
        app_env = os.getenv("APP_ENV", "").strip().lower()
        candidates = [prod_path, env_path] if app_env == "production" else [env_path, prod_path]

    for env_file in candidates:
        if not env_file.exists():
            continue
        with env_file.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, value)
        return env_file
    return None


def get_runtime_target() -> tuple[str, int, str]:
    app_host = (os.getenv("APP_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    loopback_host = (os.getenv("APP_LOOPBACK_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    raw_port = (os.getenv("APP_PORT") or "8000").strip()
    try:
        app_port = int(raw_port)
    except ValueError:
        app_port = 8000
    return app_host, app_port, loopback_host


def choose_python_executable(backend_dir: Path) -> str:
    venv_python = backend_dir / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)

    exe_name = Path(sys.executable).name.lower()
    if exe_name.startswith("python"):
        return sys.executable
    return "python"


def build_base_url(loopback_host: str, app_port: int) -> str:
    return f"http://{loopback_host}:{app_port}"


def health_check(base_url: str, timeout_seconds: float = 2.0) -> bool:
    try:
        request = urllib.request.Request(
            f"{base_url}/health/ready",
            headers={"Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return int(response.status) == 200
    except Exception:
        return False


def start_backend_process(backend_dir: Path, app_host: str, app_port: int) -> subprocess.Popen:
    global BACKEND_LOG_HANDLE

    python_executable = choose_python_executable(backend_dir)
    command = [
        python_executable,
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        app_host,
        "--port",
        str(app_port),
    ]

    log_path = backend_dir / "desktop_backend.log"
    BACKEND_LOG_HANDLE = log_path.open("a", encoding="utf-8")
    BACKEND_LOG_HANDLE.write(f"\n[{dt.datetime.now().isoformat()}] Desktop launcher start\n")
    BACKEND_LOG_HANDLE.flush()

    creationflags = 0
    if os.name == "nt":
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)

    return subprocess.Popen(
        command,
        cwd=str(backend_dir),
        env=os.environ.copy(),
        stdout=BACKEND_LOG_HANDLE,
        stderr=BACKEND_LOG_HANDLE,
        creationflags=creationflags,
    )


def wait_for_readiness(base_url: str, process: subprocess.Popen, timeout_seconds: int = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Backend process exited before readiness check passed.")
        try:
            request = urllib.request.Request(
                f"{base_url}/health/ready",
                headers={"Cache-Control": "no-cache"},
            )
            with urllib.request.urlopen(request, timeout=2.0) as response:
                if int(response.status) == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"Backend readiness timed out after {timeout_seconds}s. Last error: {last_error}")


def stop_backend_process() -> None:
    global BACKEND_PROCESS, BACKEND_LOG_HANDLE

    process = BACKEND_PROCESS
    if process is None:
        if BACKEND_LOG_HANDLE is not None:
            BACKEND_LOG_HANDLE.flush()
            BACKEND_LOG_HANDLE.close()
            BACKEND_LOG_HANDLE = None
        return

    if process.poll() is None and SHOULD_STOP_BACKEND:
        if os.name == "nt":
            ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
            if ctrl_break is not None:
                try:
                    os.kill(process.pid, ctrl_break)
                    process.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    pass

            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass

            if process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        else:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    BACKEND_PROCESS = None
    if BACKEND_LOG_HANDLE is not None:
        BACKEND_LOG_HANDLE.flush()
        BACKEND_LOG_HANDLE.close()
        BACKEND_LOG_HANDLE = None


def register_exit_hooks() -> None:
    def _handle_signal(_signum, _frame) -> None:
        stop_backend_process()
        raise SystemExit(0)

    atexit.register(stop_backend_process)
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            signal.signal(sig, _handle_signal)


def collect_cookie_header(cookie_jar: CookieJar) -> str:
    cookies: list[str] = []
    for cookie in cookie_jar:
        cookies.append(f"{cookie.name}={cookie.value}")
    return "; ".join(cookies)


def urlopen_with_status(
    opener: urllib.request.OpenerDirector,
    request: urllib.request.Request,
    timeout: float = 8.0,
) -> tuple[int, str, str]:
    with opener.open(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="ignore")
        return int(response.status), body, response.geturl()


async def probe_websocket(base_url: str, cookie_header: str) -> None:
    import websockets

    parsed = urllib.parse.urlparse(base_url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_url = f"{ws_scheme}://{parsed.netloc}/ws/live-monitoring"
    async with websockets.connect(
        ws_url,
        extra_headers=[("Cookie", cookie_header)],
        open_timeout=6,
    ) as websocket:
        await websocket.send("desktop-smoke-probe")
        await asyncio.sleep(0.2)


def run_smoke_checks(base_url: str, username: str, password: str) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    cookie_jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    def append_result(name: str, passed: bool, details: str) -> None:
        results.append((name, passed, details))

    login_page_request = urllib.request.Request(f"{base_url}/login")
    try:
        status, body, _ = urlopen_with_status(opener, login_page_request)
        append_result(
            "Login page loads",
            status == 200 and "<form" in body.lower(),
            f"HTTP {status}",
        )
    except Exception as exc:  # noqa: BLE001
        append_result("Login page loads", False, str(exc))
        return results

    login_payload = urllib.parse.urlencode({"username": username, "password": password}).encode("utf-8")
    login_request = urllib.request.Request(
        f"{base_url}/login",
        data=login_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        status, body, _ = urlopen_with_status(opener, login_request)
        data = json.loads(body) if body.strip() else {}
        is_ok = status == 200 and data.get("status") == "success"
        append_result("Login API works", is_ok, f"HTTP {status}")
    except Exception as exc:  # noqa: BLE001
        append_result("Login API works", False, str(exc))
        return results

    dashboard_request = urllib.request.Request(f"{base_url}/")
    try:
        status, body, final_url = urlopen_with_status(opener, dashboard_request)
        is_ok = status == 200 and not final_url.endswith("/login")
        has_shell = "History Summary Lens" in body or "Inventory Command Deck" in body
        append_result("Dashboard loads", is_ok and has_shell, f"HTTP {status}")
    except Exception as exc:  # noqa: BLE001
        append_result("Dashboard loads", False, str(exc))

    cookie_header = collect_cookie_header(cookie_jar)
    try:
        asyncio.run(probe_websocket(base_url, cookie_header))
        append_result("WebSocket live monitoring route works", True, "Connected and sent probe frame")
    except Exception as exc:  # noqa: BLE001
        append_result("WebSocket live monitoring route works", False, str(exc))

    me_request = urllib.request.Request(f"{base_url}/api/me")
    try:
        status, _, _ = urlopen_with_status(opener, me_request)
        append_result("Authenticated session works", status == 200, f"HTTP {status}")
    except Exception as exc:  # noqa: BLE001
        append_result("Authenticated session works", False, str(exc))

    invalid_register_request = urllib.request.Request(
        f"{base_url}/register-item",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        status, _, _ = urlopen_with_status(opener, invalid_register_request)
        append_result("Registration flow route reachable", status in {200, 400, 409, 422}, f"HTTP {status}")
    except urllib.error.HTTPError as exc:
        append_result(
            "Registration flow route reachable",
            int(exc.code) in {400, 409, 422},
            f"HTTP {exc.code}",
        )
    except Exception as exc:  # noqa: BLE001
        append_result("Registration flow route reachable", False, str(exc))

    invalid_update_request = urllib.request.Request(
        f"{base_url}/update-item/INVALIDTAGID",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        status, _, _ = urlopen_with_status(opener, invalid_update_request)
        append_result("Update flow route reachable", status in {200, 400, 404, 422}, f"HTTP {status}")
    except urllib.error.HTTPError as exc:
        append_result(
            "Update flow route reachable",
            int(exc.code) in {400, 404, 422},
            f"HTTP {exc.code}",
        )
    except Exception as exc:  # noqa: BLE001
        append_result("Update flow route reachable", False, str(exc))

    today = dt.date.today().strftime("%Y-%m-%d")
    report_request = urllib.request.Request(f"{base_url}/api/report-summary?target_date={today}")
    missing_request = urllib.request.Request(f"{base_url}/api/missing-items?target_date={today}")
    inventory_request = urllib.request.Request(f"{base_url}/api/all-inventory")

    try:
        status, _, _ = urlopen_with_status(opener, report_request)
        append_result("Report summary API works", status == 200, f"HTTP {status}")
    except Exception as exc:  # noqa: BLE001
        append_result("Report summary API works", False, str(exc))

    try:
        status, _, _ = urlopen_with_status(opener, missing_request)
        append_result("Missing items/export API works", status == 200, f"HTTP {status}")
    except Exception as exc:  # noqa: BLE001
        append_result("Missing items/export API works", False, str(exc))

    try:
        status, _, _ = urlopen_with_status(opener, inventory_request)
        append_result("Full inventory/export API works", status == 200, f"HTTP {status}")
    except Exception as exc:  # noqa: BLE001
        append_result("Full inventory/export API works", False, str(exc))

    return results


def print_smoke_results(results: list[tuple[str, bool, str]]) -> bool:
    all_passed = True
    print("Desktop smoke test results:")
    for name, passed, details in results:
        status = "PASS" if passed else "FAIL"
        print(f"- [{status}] {name}: {details}")
        if not passed:
            all_passed = False
    return all_passed


def run_window(base_url: str, title: str) -> None:
    import webview

    window = webview.create_window(
        title,
        f"{base_url}/login",
        width=1440,
        height=900,
        min_size=(1024, 700),
    )

    def on_closing() -> bool:
        stop_backend_process()
        return True

    window.events.closing += on_closing
    webview.start(debug=False, private_mode=True)


def main() -> int:
    global BACKEND_PROCESS, SHOULD_STOP_BACKEND

    parser = argparse.ArgumentParser(description="Desktop wrapper launcher for existing FastAPI web app.")
    parser.add_argument("--smoke", action="store_true", help="Run smoke validations and exit.")
    parser.add_argument("--headless", action="store_true", help="Run backend + readiness only, no desktop window.")
    parser.add_argument("--title", default="KOL Jewellery Desktop")
    parser.add_argument("--readiness-timeout", type=int, default=60)
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    backend_dir = resolve_backend_dir()
    os.chdir(backend_dir)
    load_env_defaults(backend_dir)
    app_host, app_port, loopback_host = get_runtime_target()
    base_url = build_base_url(loopback_host, app_port)
    username = (
        args.username
        or os.getenv("DESKTOP_SMOKE_USERNAME", "")
        or os.getenv("BOOTSTRAP_ADMIN_USERNAME", "")
    )
    password = (
        args.password
        or os.getenv("DESKTOP_SMOKE_PASSWORD", "")
        or os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    )

    register_exit_hooks()

    try:
        if health_check(base_url):
            SHOULD_STOP_BACKEND = False
            BACKEND_PROCESS = None
        else:
            BACKEND_PROCESS = start_backend_process(backend_dir, app_host, app_port)
            SHOULD_STOP_BACKEND = True
            wait_for_readiness(base_url, BACKEND_PROCESS, timeout_seconds=args.readiness_timeout)
    except Exception:  # noqa: BLE001
        stop_backend_process()
        raise

    if args.smoke:
        if not username or not password:
            print("Smoke mode requires credentials (DESKTOP_SMOKE_USERNAME / DESKTOP_SMOKE_PASSWORD).")
            return 1
        results = run_smoke_checks(base_url, username, password)
        passed = print_smoke_results(results)
        stop_backend_process()
        return 0 if passed else 1

    if args.headless:
        print(f"Backend ready at {base_url}. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_backend_process()
            return 0

    run_window(base_url, args.title)
    stop_backend_process()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
