import json
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state.json"
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

# GNS3 3.x exposes the controller API under /v3 and requires a login token.
# GNS3 2.x still uses /v2. If GNS3_URL is not set we probe both so the same
# scripts work with either server version.
_HOST = os.environ.get("GNS3_HOST", "http://127.0.0.1:3080").strip().rstrip("/")
_USER = (os.environ.get("GNS3_USER") or os.environ.get("GNS3_USERNAME") or "admin").strip()
_PASSWORD = os.environ.get("GNS3_PASSWORD", "admin")
_API_KEY = os.environ.get("GNS3_API_KEY", "").strip()

_TOKEN = ""


def _detect_base():
    raw = os.environ.get("GNS3_URL", "").strip().rstrip("/")
    if raw:
        if raw.endswith("/v2") or raw.endswith("/v3"):
            candidates = [raw]
        else:
            candidates = [raw + "/v3", raw + "/v2"]
    else:
        candidates = [_HOST + "/v3", _HOST + "/v2"]

    errors = []
    probe_headers = (
        {"Authorization": f"Bearer {_API_KEY}"} if _API_KEY else {}
    )
    for base in candidates:
        version = 3 if base.endswith("/v3") else 2
        try:
            response = requests.get(
                base + "/version", timeout=5, headers=probe_headers
            )
        except requests.RequestException as exc:
            errors.append(f"{base} ({exc})")
            continue
        if response.ok:
            return base, version
        errors.append(f"{base} (HTTP {response.status_code})")

    raise RuntimeError(
        "Không kết nối được GNS3 API. Hãy mở GNS3 GUI/server trước, hoặc đặt "
        "GNS3_URL (ví dụ http://127.0.0.1:3080). Đã thử: " + "; ".join(errors)
    )


BASE, API_VERSION = _detect_base()
SESSION = requests.Session()
SESSION.headers.update({"Content-Type": "application/json"})
if _API_KEY:
    SESSION.headers["Authorization"] = f"Bearer {_API_KEY}"


def login(force=False):
    """Authenticate against GNS3 3.x and store the bearer token."""
    global _TOKEN
    if API_VERSION < 3:
        return
    if _TOKEN and not force:
        SESSION.headers["Authorization"] = f"Bearer {_TOKEN}"
        return

    url = BASE + "/access/users/authenticate"
    try:
        response = SESSION.post(
            url,
            json={"username": _USER, "password": _PASSWORD},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Không gọi được GNS3 login API: {exc}") from exc

    if not response.ok:
        raise RuntimeError(
            "Đăng nhập GNS3 thất bại "
            f"(HTTP {response.status_code}: {response.text[:300]}). "
            "Đặt GNS3_USER/GNS3_PASSWORD đúng với tài khoản trong GNS3 "
            "(mặc định script dùng admin/admin)."
        )

    try:
        _TOKEN = response.json()["access_token"]
    except (ValueError, KeyError) as exc:
        raise RuntimeError("GNS3 login API trả về dữ liệu không hợp lệ.") from exc

    SESSION.headers["Authorization"] = f"Bearer {_TOKEN}"


def api(method, path, **kwargs):
    if API_VERSION >= 3:
        login()

    url = BASE + path
    timeout = kwargs.pop("timeout", 60)
    response = SESSION.request(method, url, timeout=timeout, **kwargs)

    # Token can expire while a long setup is running.
    if response.status_code == 401 and API_VERSION >= 3:
        login(force=True)
        response = SESSION.request(method, url, timeout=timeout, **kwargs)

    if not response.ok:
        raise RuntimeError(
            f"GNS3 API {method} {path} failed: "
            f"{response.status_code} {response.text[:800]}"
        )
    if response.status_code == 204 or not response.text.strip():
        return None
    return response.json()


def save_state(data):
    STATE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_state():
    if not STATE.exists():
        raise RuntimeError("Không tìm thấy state.json. Hãy chạy ./setup.sh trước.")
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"state.json bị hỏng: {exc}") from exc


def wait_for(predicate, timeout=180, interval=3, description="điều kiện"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception:
            pass
        time.sleep(interval)
    raise TimeoutError(f"Hết thời gian chờ {description} sau {timeout}s")


def log_path(name):
    return LOGS / name
