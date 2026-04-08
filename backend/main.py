import asyncio
import atexit
import base64
import datetime
import hashlib
import hmac
import logging
import os
import posixpath
import secrets
import threading
import urllib.parse
from typing import List, Optional

import config
from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel
from sqlalchemy import Column, Float, Integer, String, create_engine, desc, func, inspect as sa_inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker

try:
    from rfid_active_server import parse_antennas as parse_active_antennas
    from rfid_active_server import run_forever as run_active_reader_forever
    ACTIVE_READER_IMPORT_ERROR = None
except BaseException as exc:  # noqa: BLE001
    parse_active_antennas = None
    run_active_reader_forever = None
    ACTIVE_READER_IMPORT_ERROR = exc

app = FastAPI(title="Jewellery RFID Professional MSSQL")
cors_cfg = getattr(config, "CORS_SETTINGS", {})
cors_allow_origins = cors_cfg.get("ALLOW_ORIGINS", ["*"])
cors_allow_credentials = bool(cors_cfg.get("ALLOW_CREDENTIALS", False))
if "*" in cors_allow_origins and cors_allow_credentials:
    cors_allow_credentials = False
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
EVENT_LOG_FILE = os.path.join(BASE_DIR, "reader_events.log")

global_loop: Optional[asyncio.AbstractEventLoop] = None
tag_queue: asyncio.Queue[tuple[str, int, Optional[int]]] = asyncio.Queue()
last_scan_tracker: dict = {}
pending_tag_events: dict[str, dict] = {}
pending_tag_tasks: dict[str, asyncio.Task] = {}
pending_tag_lock: Optional[asyncio.Lock] = None
reader_lock_handle = None
LOGGER = logging.getLogger("rfid_backend")


def log_status(message: str) -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {message}"
    print(line, flush=True)
    try:
        with open(EVENT_LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass


def internal_error_detail(exc: Exception) -> str:
    if getattr(config, "HIDE_INTERNAL_ERRORS", False):
        return "Internal server error"
    return str(exc)


def apply_no_cache_headers(response: Response) -> Response:
    # Prevent browser from serving stale dashboard/login HTML from cache.
    for key, value in getattr(config, "CACHE_HEADERS", {}).items():
        response.headers[key] = value
    return response


def validate_runtime_config() -> List[str]:
    issues: List[str] = []
    if getattr(config, "IS_PRODUCTION", False):
        if SESSION_SECRET_KEY in {"change-me-in-production", "change-me-before-production"}:
            issues.append("SECURITY_SETTINGS.SECRET_KEY is using default value.")
        if SESSION_COOKIE_SECURE is False:
            issues.append("COOKIE_SECURE is false in production.")
        if "*" in cors_allow_origins and cors_allow_credentials:
            issues.append("CORS wildcard origin cannot be used with credentials=true.")
        if "*" in cors_allow_origins:
            issues.append("CORS_ALLOW_ORIGINS should not contain '*' in production.")
    return issues


def parse_antenna_id(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 1


def parse_rssi_value(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def clean_tag_id(raw_data) -> str:
    try:
        # Normalize any scanner payload into a strict 24-char uppercase EPC.
        if isinstance(raw_data, dict):
            raw_data = raw_data.get("EPC-96") or raw_data.get("EPCData") or raw_data.get("EPC")
        if isinstance(raw_data, bytes):
            try:
                raw_data = raw_data.decode("ascii", errors="ignore")
            except Exception:  # noqa: BLE001
                raw_data = raw_data.hex()

        tag = "".join(ch for ch in str(raw_data).upper() if ch.isalnum())
        return tag[:24]
    except Exception:  # noqa: BLE001
        return str(raw_data)[:24]


def to_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_user_role(raw_role, default: str = "staff") -> str:
    role = str(raw_role or "").strip().lower()
    if role in {"admin", "staff"}:
        return role
    if default in {"admin", "staff"}:
        return default
    return "staff"


security_cfg = getattr(config, "SECURITY_SETTINGS", {})
SESSION_COOKIE_NAME = security_cfg.get("COOKIE_NAME", getattr(config, "COOKIE_NAME", "session_id"))
SESSION_MAX_AGE_SECONDS = int(security_cfg.get("SESSION_MAX_AGE_SECONDS", 60 * 60 * 8))
SESSION_COOKIE_SAMESITE = str(security_cfg.get("COOKIE_SAMESITE", "lax")).lower()
SESSION_COOKIE_SECURE = bool(security_cfg.get("COOKIE_SECURE", False))
SESSION_COOKIE_PATH = str(security_cfg.get("COOKIE_PATH", "/"))
SESSION_SECRET_KEY = str(security_cfg.get("SECRET_KEY", "change-me-in-production"))
PASSWORD_HASH_ITERATIONS = int(security_cfg.get("PASSWORD_HASH_ITERATIONS", 180000))
SESSION_SIGNER = URLSafeTimedSerializer(SESSION_SECRET_KEY, salt="rfid-admin-session")
SESSION_BOOT_NONCE = secrets.token_urlsafe(16)
if SESSION_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    SESSION_COOKIE_SAMESITE = "lax"


def is_password_hashed(stored: str) -> bool:
    return isinstance(stored, str) and stored.startswith("pbkdf2_sha256$")


def hash_password(password: str) -> str:
    salt = secrets.token_hex(8)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    )
    encoded = base64.urlsafe_b64encode(dk).decode("ascii").rstrip("=")
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}${encoded}"


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    if is_password_hashed(stored):
        try:
            _, iters_raw, salt, expected = stored.split("$", 3)
            iters = int(iters_raw)
            candidate = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt.encode("utf-8"),
                iters,
            )
            candidate_hex = candidate.hex()
            if hmac.compare_digest(candidate_hex, expected):
                return True
            candidate_b64 = base64.urlsafe_b64encode(candidate).decode("ascii").rstrip("=")
            return hmac.compare_digest(candidate_b64, expected)
        except (TypeError, ValueError):
            return False
    return hmac.compare_digest(password, stored)


def create_session_token(username: str, role: str) -> str:
    return SESSION_SIGNER.dumps(
        {
            "username": username,
            "role": role,
            "boot_nonce": SESSION_BOOT_NONCE,
            "issued_at": int(datetime.datetime.now().timestamp()),
        }
    )


def parse_session_token(token: str) -> Optional[dict]:
    try:
        payload = SESSION_SIGNER.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
        if (
            isinstance(payload, dict)
            and payload.get("username")
            and payload.get("boot_nonce") == SESSION_BOOT_NONCE
        ):
            return payload
    except (BadSignature, SignatureExpired):
        return None
    return None


def normalize_request_path(path: str) -> str:
    normalized = posixpath.normpath(path or "/")
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


@app.middleware("http")
async def enforce_dashboard_auth(request: Request, call_next):
    path = normalize_request_path(request.url.path)
    protected_paths = {
        "/",
        "/index.html",
        "/static/index.html",
        "/dashboard",
        "/dashboard/index.html",
    }
    is_protected_dashboard_path = (
        path in protected_paths
        or (path.startswith("/static/") and path.endswith("/index.html"))
    )
    if request.method in {"GET", "HEAD"} and is_protected_dashboard_path:
        token = request.cookies.get(SESSION_COOKIE_NAME)
        if not token or parse_session_token(token) is None:
            return apply_no_cache_headers(RedirectResponse(url="/login"))
    return await call_next(request)


def acquire_reader_lock(lock_path: str) -> bool:
    global reader_lock_handle
    if reader_lock_handle is not None:
        return True

    lock_dir = os.path.dirname(lock_path)
    if lock_dir:
        os.makedirs(lock_dir, exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        handle.seek(0)
        if handle.tell() == 0:
            handle.write("1")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False

    reader_lock_handle = handle
    return True


def release_reader_lock() -> None:
    global reader_lock_handle
    if reader_lock_handle is None:
        return
    try:
        if os.name == "nt":
            import msvcrt

            reader_lock_handle.seek(0)
            msvcrt.locking(reader_lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(reader_lock_handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        reader_lock_handle.close()
    except OSError:
        pass
    reader_lock_handle = None


atexit.register(release_reader_lock)


def build_odbc_conn_str() -> str:
    settings = config.DB_SETTINGS
    server = (settings.get("SERVER") or "").strip()
    port = str(settings.get("PORT") or "").strip()
    if port and "," not in server:
        server = f"{server},{port}"

    parts = [
        f"Driver={settings.get('DRIVER', '{ODBC Driver 17 for SQL Server}')};",
        f"Server={server};",
        f"Database={settings.get('DATABASE', 'jewellery_db')};",
    ]

    trusted = to_bool(settings.get("TRUSTED_CONNECTION", "yes"), default=True)
    if trusted:
        parts.append("Trusted_Connection=yes;")
    else:
        parts.append(f"UID={settings.get('USERNAME', '')};")
        parts.append(f"PWD={settings.get('PASSWORD', '')};")

    encrypt = str(settings.get("ENCRYPT", "")).strip()
    trust_cert = str(settings.get("TRUST_SERVER_CERTIFICATE", "")).strip()
    if encrypt:
        parts.append(f"Encrypt={encrypt};")
    if trust_cert:
        parts.append(f"TrustServerCertificate={trust_cert};")

    return "".join(parts)


def build_mssql_sqlalchemy_url() -> str:
    params = urllib.parse.quote_plus(build_odbc_conn_str())
    return f"mssql+pyodbc:///?odbc_connect={params}"


def build_sqlalchemy_url() -> str:
    override = str(getattr(config, "SQLALCHEMY_DATABASE_URL", "") or "").strip()
    if override:
        return override
    return build_mssql_sqlalchemy_url()


SQLALCHEMY_DATABASE_URL = build_sqlalchemy_url()
engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class JewelleryItem(Base):
    __tablename__ = "inventory"
    tag_id = Column(String(50), primary_key=True, index=True)
    item_name = Column(String(255))
    category = Column(String(100))
    metal_type = Column(String(50))
    purity = Column(String(50))
    weight = Column(Float)
    huid = Column(String(50))
    piece = Column(Float)
    timestamp = Column(String(100))


class ScanLog(Base):
    __tablename__ = "scan_logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tag_id = Column(String(50))
    item_name = Column(String(255))
    category = Column(String(100))
    weight = Column(Float)
    piece = Column(Float)
    huid = Column(String(50))
    direction = Column(String(10))
    timestamp = Column(String(50))
    date = Column(String(50))


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    password = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="staff")


def initialize_sqlalchemy_schema() -> None:
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:  # noqa: BLE001
        log_status(f"Database schema init skipped: {exc}")


def ensure_users_schema() -> None:
    try:
        inspector = sa_inspect(engine)
        if not inspector.has_table("users"):
            User.__table__.create(bind=engine, checkfirst=True)
            return

        columns_info = inspector.get_columns("users")
        columns = {col["name"].lower() for col in columns_info}
        if "role" not in columns:
            with engine.begin() as conn:
                if engine.dialect.name.startswith("mssql"):
                    conn.execute(
                        text(
                            "ALTER TABLE users ADD role NVARCHAR(20) NOT NULL "
                            "CONSTRAINT DF_users_role_legacy DEFAULT 'staff' WITH VALUES"
                        )
                    )
                else:
                    conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'staff'"))

    except Exception as exc:  # noqa: BLE001
        log_status(f"Users schema check skipped: {exc}")


def ensure_piece_schema() -> None:
    try:
        inspector = sa_inspect(engine)

        def _ensure_piece_column(table_name: str) -> None:
            columns_info = inspector.get_columns(table_name)
            columns = {col["name"].lower() for col in columns_info}
            has_piece = "piece" in columns
            has_price = "price" in columns

            with engine.begin() as conn:
                if not has_piece:
                    if engine.dialect.name.startswith("mssql"):
                        conn.execute(text(f"ALTER TABLE {table_name} ADD piece FLOAT NULL"))
                    else:
                        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN piece FLOAT"))

                if has_price:
                    conn.execute(
                        text(
                            f"UPDATE {table_name} "
                            "SET piece = price "
                            "WHERE piece IS NULL AND price IS NOT NULL"
                        )
                    )

        if inspector.has_table("inventory"):
            _ensure_piece_column("inventory")
        if inspector.has_table("scan_logs"):
            _ensure_piece_column("scan_logs")

    except Exception as exc:  # noqa: BLE001
        log_status(f"Piece schema check skipped: {exc}")


class ItemRegister(BaseModel):
    tag_id: str
    item_name: str
    category: str
    metal_type: str
    purity: str
    weight: float
    huid: Optional[str] = None
    piece: float


class ItemUpdate(BaseModel):
    item_name: Optional[str] = None
    category: Optional[str] = None
    metal_type: Optional[str] = None
    purity: Optional[str] = None
    weight: Optional[float] = None
    huid: Optional[str] = None
    piece: Optional[float] = None


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "staff"


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:  # noqa: BLE001
                dead_connections.append(connection)

        for connection in dead_connections:
            self.disconnect(connection)


manager = ConnectionManager()


async def get_current_user(session_token: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME)):
    # Read and validate signed session cookie for normal HTTP requests.
    if not session_token:
        return None
    payload = parse_session_token(session_token)
    if not payload:
        return None
    return payload


def require_authenticated_user(user=Depends(get_current_user)):
    # Central auth guard for protected APIs/routes.
    if user is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


def require_admin_user(user=Depends(require_authenticated_user)):
    role = normalize_user_role(user.get("role"), default="staff")
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def get_websocket_user(websocket: WebSocket) -> Optional[dict]:
    token = websocket.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return parse_session_token(token)


def ensure_bootstrap_admin() -> None:
    bootstrap_cfg = security_cfg.get("BOOTSTRAP_ADMIN", {})
    admin_username = str(bootstrap_cfg.get("USERNAME", "")).strip()
    admin_password = str(bootstrap_cfg.get("PASSWORD", "")).strip()
    admin_role = normalize_user_role(bootstrap_cfg.get("ROLE"), default="admin")

    db = SessionLocal()
    try:
        total_users = db.query(User.id).count()
        if not admin_username or not admin_password:
            if not getattr(config, "IS_PRODUCTION", False) and total_users == 0:
                admin_username = "admin"
                admin_password = "admin123"
                admin_role = "admin"
                log_status("Development bootstrap admin is enabled: username=admin password=admin123")
            else:
                return

        existing = db.query(User).filter(func.lower(User.username) == admin_username.lower()).first()
        if existing:
            current_role = normalize_user_role(existing.role, default="staff")
            if current_role != admin_role:
                existing.role = admin_role
                db.commit()
            return

        user = User(
            username=admin_username,
            password=hash_password(admin_password),
            role=admin_role,
        )
        db.add(user)
        db.commit()
        log_status(f"Bootstrap admin created: {admin_username}")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log_status(f"Bootstrap admin setup skipped: {exc}")
    finally:
        db.close()


def verify_rfid_push_token(request: Request) -> None:
    expected = str(config.RFID_ACTIVE_SETTINGS.get("PUSH_TOKEN", "")).strip()
    if not expected:
        return
    received = (request.headers.get("x-rfid-token") or "").strip()
    if not hmac.compare_digest(received, expected):
        raise HTTPException(status_code=401, detail="Invalid RFID push token")


def normalize_date_or_raise(raw_date: str, field_name: str) -> str:
    try:
        return datetime.datetime.strptime(raw_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field_name} format. Use YYYY-MM-DD") from exc


def parse_scan_log_datetime(date_value: str, time_value: str) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.strptime(
            f"{str(date_value).strip()} {str(time_value).strip()}",
            "%Y-%m-%d %I:%M:%S %p",
        )
    except (TypeError, ValueError):
        return None


def compute_transition_stats(rows) -> dict:
    # Build IN/OUT totals from valid state transitions per tag.
    state_by_tag: dict[str, str] = {}
    in_count = 0
    out_count = 0
    ignored_count = 0

    for row in rows:
        tag_id = clean_tag_id(getattr(row, "tag_id", ""))
        direction = str(getattr(row, "direction", "") or "").strip().upper()
        if not tag_id or direction not in {"IN", "OUT"}:
            ignored_count += 1
            continue

        previous = state_by_tag.get(tag_id, "NONE")
        if direction == "IN":
            if previous != "IN":
                in_count += 1
                state_by_tag[tag_id] = "IN"
            else:
                ignored_count += 1
            continue

        if previous == "IN":
            out_count += 1
            state_by_tag[tag_id] = "OUT"
        else:
            ignored_count += 1

    pending_count = sum(1 for state in state_by_tag.values() if state == "IN")
    return {
        "in": in_count,
        "out": out_count,
        "pending": pending_count,
        "ignored": ignored_count,
    }


def pick_preferred_event(existing_event: dict, candidate_event: dict) -> dict:
    existing_rssi = existing_event.get("rssi")
    candidate_rssi = candidate_event.get("rssi")

    if isinstance(existing_rssi, int) and isinstance(candidate_rssi, int):
        if candidate_rssi > existing_rssi:
            return candidate_event
        return existing_event

    if isinstance(candidate_rssi, int) and not isinstance(existing_rssi, int):
        return candidate_event

    return existing_event


async def flush_pending_tag_event(tag_id: str, delay_seconds: float) -> None:
    try:
        await asyncio.sleep(delay_seconds)
        if pending_tag_lock is None:
            return

        async with pending_tag_lock:
            payload = pending_tag_events.pop(tag_id, None)
            pending_tag_tasks.pop(tag_id, None)

        if not payload:
            return

        await tag_queue.put((payload["tag_id"], payload["antenna_id"], payload.get("rssi")))
    except asyncio.CancelledError:
        return


async def enqueue_scan_event(tag_id: str, antenna_id: int, rssi: Optional[int]) -> None:
    coalesce_window_ms = max(0, int(getattr(config, "ANTENNA_COALESCE_WINDOW_MS", 0)))
    if coalesce_window_ms <= 0 or pending_tag_lock is None:
        await tag_queue.put((tag_id, antenna_id, rssi))
        return

    candidate_event = {
        "tag_id": tag_id,
        "antenna_id": antenna_id,
        "rssi": rssi,
    }

    async with pending_tag_lock:
        existing_event = pending_tag_events.get(tag_id)
        if existing_event is None:
            pending_tag_events[tag_id] = candidate_event
            pending_tag_tasks[tag_id] = asyncio.create_task(
                flush_pending_tag_event(tag_id, coalesce_window_ms / 1000.0)
            )
            return

        pending_tag_events[tag_id] = pick_preferred_event(existing_event, candidate_event)


async def process_scan_event(tag_id: str, antenna_id: int, rssi: Optional[int] = None):
    tag_id = clean_tag_id(tag_id)
    if not tag_id:
        return

    direction = str(config.ANTENNA_MAP.get(str(antenna_id), "IN") or "IN").strip().upper()
    if direction not in {"IN", "OUT"}:
        direction = "IN"
    current_time = datetime.datetime.now()
    db = SessionLocal()

    try:
        item = db.query(JewelleryItem).filter(func.trim(JewelleryItem.tag_id) == tag_id).first()
        if not item:
            log_status(f"Unregistered tag read: {tag_id} (antenna={antenna_id})")
            return

        latest_log = (
            db.query(ScanLog)
            .filter(func.trim(ScanLog.tag_id) == tag_id)
            .order_by(desc(ScanLog.id))
            .first()
        )
        latest_direction = str(latest_log.direction).upper() if latest_log and latest_log.direction else ""

        cache_entry_raw = last_scan_tracker.get(tag_id)
        cache_entry = cache_entry_raw if isinstance(cache_entry_raw, dict) else {}
        cache_direction = str(cache_entry.get("direction", "")).strip().upper()
        cache_seen_at = cache_entry.get("time")
        cache_antenna_raw = cache_entry.get("antenna_id")
        cache_antenna_id: Optional[int] = None
        if cache_antenna_raw is not None:
            cache_antenna_id = parse_antenna_id(cache_antenna_raw)
        if not isinstance(cache_seen_at, datetime.datetime):
            cache_seen_at = None

        direction_switch_guard_seconds = max(
            0,
            int(
                getattr(
                    config,
                    "DIRECTION_SWITCH_GUARD_SECONDS",
                    getattr(config, "OUT_BLOCK_AFTER_IN_SECONDS", 0),
                )
            ),
        )
        if (
            direction_switch_guard_seconds > 0
            and cache_seen_at is not None
            and cache_antenna_id == antenna_id
            and cache_direction in {"IN", "OUT"}
            and cache_direction != direction
        ):
            elapsed = (current_time - cache_seen_at).total_seconds()
            if elapsed < direction_switch_guard_seconds:
                wait_left = int(direction_switch_guard_seconds - elapsed)
                log_status(
                    "Ignored rapid direction flip: "
                    f"tag={tag_id} from={cache_direction} to={direction} "
                    f"antenna={antenna_id} wait={wait_left}s"
                )
                return

        if direction == "IN" and latest_direction == "IN":
            cross_antenna_cache_hit = (
                cache_direction == "IN"
                and cache_antenna_id is not None
                and cache_antenna_id != antenna_id
            )
            allow_pending_in_relog = bool(getattr(config, "ALLOW_PENDING_IN_RELOG", False))

            if not allow_pending_in_relog and not cross_antenna_cache_hit:
                log_status(f"Ignored duplicate IN without OUT: tag={tag_id} antenna={antenna_id}")
                return

            if allow_pending_in_relog and not cross_antenna_cache_hit:
                relog_seconds = max(0, int(getattr(config, "PENDING_IN_RELOG_SECONDS", 0)))
                if relog_seconds > 0 and latest_log:
                    latest_in_seen_at = parse_scan_log_datetime(latest_log.date, latest_log.timestamp)
                    if latest_in_seen_at is not None:
                        elapsed = (current_time - latest_in_seen_at).total_seconds()
                        if elapsed < relog_seconds:
                            wait_left = int(relog_seconds - elapsed)
                            log_status(
                                f"Ignored duplicate IN due to relog window: "
                                f"tag={tag_id} antenna={antenna_id} wait={wait_left}s"
                            )
                            return

        if direction == "IN":
            in_cooldown_seconds = max(
                0,
                int(getattr(config, "IN_REENTRY_COOLDOWN_SECONDS", config.SCAN_COOLDOWN_SECONDS)),
            )
            if in_cooldown_seconds > 0:
                latest_in_log = (
                    db.query(ScanLog)
                    .filter(func.trim(ScanLog.tag_id) == tag_id)
                    .filter(func.upper(func.trim(ScanLog.direction)) == "IN")
                    .order_by(desc(ScanLog.id))
                    .first()
                )

                latest_in_seen_at: Optional[datetime.datetime] = None
                if latest_in_log:
                    latest_in_seen_at = parse_scan_log_datetime(latest_in_log.date, latest_in_log.timestamp)

                if (
                    not latest_in_seen_at
                    and cache_direction == "IN"
                    and cache_antenna_id == antenna_id
                    and cache_seen_at is not None
                ):
                    latest_in_seen_at = cache_seen_at

                if latest_in_seen_at:
                    elapsed = (current_time - latest_in_seen_at).total_seconds()
                    if elapsed < in_cooldown_seconds:
                        wait_left = int(in_cooldown_seconds - elapsed)
                        log_status(
                            f"Ignored IN due to re-entry cooldown: tag={tag_id} wait={wait_left}s"
                        )
                        return

        scan_cooldown_seconds = max(0, int(getattr(config, "SCAN_COOLDOWN_SECONDS", 0)))
        if (
            scan_cooldown_seconds > 0
            and cache_seen_at is not None
            and cache_direction == direction
            and cache_antenna_id == antenna_id
        ):
            time_diff = (current_time - cache_seen_at).total_seconds()
            if time_diff < scan_cooldown_seconds:
                wait_left = int(scan_cooldown_seconds - time_diff)
                log_status(
                    f"Ignored {direction} due to scan cooldown: "
                    f"tag={tag_id} antenna={antenna_id} wait={wait_left}s"
                )
                return

        if direction == "OUT":
            if latest_direction != "IN":
                log_status(
                    f"Ignored OUT without pending IN: tag={tag_id} antenna={antenna_id}"
                )
                return

            out_block_seconds = max(0, int(getattr(config, "OUT_BLOCK_AFTER_IN_SECONDS", 0)))
            if out_block_seconds > 0:
                latest_seen_at: Optional[datetime.datetime] = None
                if latest_log and latest_direction == "IN":
                    latest_seen_at = parse_scan_log_datetime(latest_log.date, latest_log.timestamp)

                if not latest_seen_at and cache_direction == "IN" and cache_seen_at is not None:
                    latest_seen_at = cache_seen_at

                if latest_seen_at:
                    elapsed = (current_time - latest_seen_at).total_seconds()
                    if elapsed < out_block_seconds:
                        wait_left = int(out_block_seconds - elapsed)
                        log_status(
                            f"Ignored OUT due to lock window: tag={tag_id} wait={wait_left}s"
                        )
                        return

        new_log = ScanLog(
            tag_id=tag_id,
            item_name=item.item_name,
            category=item.category,
            weight=item.weight,
            piece=item.piece,
            huid=item.huid,
            direction=direction,
            timestamp=current_time.strftime("%I:%M:%S %p"),
            date=current_time.strftime("%Y-%m-%d"),
        )
        db.add(new_log)
        db.commit()
        db.refresh(new_log)

        last_scan_tracker[tag_id] = {
            "time": current_time,
            "direction": direction,
            "antenna_id": antenna_id,
        }

        await manager.broadcast(
            {
                "source": "MONITORING",
                "id": new_log.id,
                "tag_id": tag_id,
                "item_name": item.item_name,
                "direction": direction,
                "timestamp": new_log.timestamp,
                "date": new_log.date,
                "huid": item.huid,
                "piece": item.piece,
                "weight": item.weight,
                "category": item.category,
                "antenna_id": antenna_id,
            }
        )
        log_status(
            "REAL-TIME LOG: "
            f"tag={tag_id} item={item.item_name} antenna={antenna_id} "
            f"direction={direction} rssi={rssi if rssi is not None else '-'} "
            f"weight={item.weight} piece={item.piece}"
        )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log_status(f"Process error: {exc}")
    finally:
        db.close()


async def queue_worker():
    while True:
        payload = await tag_queue.get()
        try:
            if len(payload) >= 3:
                tag_id, antenna_id, rssi = payload[0], payload[1], payload[2]
            else:
                tag_id, antenna_id = payload[0], payload[1]
                rssi = None
            await process_scan_event(tag_id, antenna_id, rssi)
        finally:
            tag_queue.task_done()


def start_embedded_active_reader() -> None:
    active_cfg = config.RFID_ACTIVE_SETTINGS
    enabled = to_bool(active_cfg.get("ENABLED", True), default=True)
    if not enabled:
        log_status("Embedded rfid_active_server reader is disabled in config.")
        return

    if run_active_reader_forever is None or parse_active_antennas is None:
        log_status(f"rfid_active_server import failed: {ACTIVE_READER_IMPORT_ERROR}")
        return

    reader_host = str(active_cfg.get("READER_HOST", "169.254.4.161"))
    reader_port = int(active_cfg.get("READER_PORT", 5084))
    raw_antennas = active_cfg.get("ANTENNAS", [1, 2, 3, 4])
    if isinstance(raw_antennas, str):
        antennas = parse_active_antennas(raw_antennas)
    else:
        antennas = [int(a) for a in raw_antennas]

    push_url = str(active_cfg.get("PUSH_URL") or f"http://{config.APP_LOOPBACK_HOST}:{config.APP_PORT}/rfid-read")
    reconnect_delay = float(active_cfg.get("RECONNECT_DELAY", 3.0))
    report_every_n_tags = int(active_cfg.get("REPORT_EVERY_N_TAGS", 1))
    report_timeout_ms = int(active_cfg.get("REPORT_TIMEOUT_MS", 0))
    session = int(active_cfg.get("SESSION", 0))
    antenna_cycle_seconds = float(active_cfg.get("ANTENNA_CYCLE_SECONDS", 0.0))
    stale_grace_seconds = float(active_cfg.get("STALE_GRACE_SECONDS", 1.0))
    api_timeout_seconds = float(active_cfg.get("API_TIMEOUT_SECONDS", 1.0))
    drop_stale_reports = to_bool(active_cfg.get("DROP_STALE_REPORTS", False), default=False)
    raw_tx_power_dbm = active_cfg.get("TX_POWER_DBM", {})
    tx_power_dbm: Optional[dict[int, float]] = None
    if isinstance(raw_tx_power_dbm, dict) and raw_tx_power_dbm:
        parsed_tx_power_dbm: dict[int, float] = {}
        for ant, value in raw_tx_power_dbm.items():
            try:
                parsed_tx_power_dbm[int(ant)] = float(value)
            except (TypeError, ValueError):
                continue
        if parsed_tx_power_dbm:
            expected_antennas = set(antennas)
            if set(parsed_tx_power_dbm.keys()) == expected_antennas:
                tx_power_dbm = parsed_tx_power_dbm
            else:
                log_status(
                    "Ignoring RFID_ACTIVE_TX_POWER_DBM: antennas mismatch "
                    f"expected={sorted(expected_antennas)} got={sorted(parsed_tx_power_dbm.keys())}"
                )
    push_token = str(active_cfg.get("PUSH_TOKEN", "")).strip()
    single_instance_lock = to_bool(active_cfg.get("SINGLE_INSTANCE_LOCK", True), default=True)
    lock_file_path = str(active_cfg.get("LOCK_FILE_PATH", "reader.lock"))
    if not os.path.isabs(lock_file_path):
        lock_file_path = os.path.join(BASE_DIR, lock_file_path)

    if single_instance_lock and not acquire_reader_lock(lock_file_path):
        log_status(f"Embedded reader lock already held by another process ({lock_file_path}).")
        return

    def _runner():
        try:
            run_active_reader_forever(
                host=reader_host,
                port=reader_port,
                antennas=antennas,
                reconnect_delay=reconnect_delay,
                report_every_n_tags=report_every_n_tags,
                report_timeout_ms=report_timeout_ms,
                session=session,
                antenna_cycle_seconds=antenna_cycle_seconds,
                drop_stale_reports=drop_stale_reports,
                stale_grace_seconds=stale_grace_seconds,
                api_url=push_url,
                api_timeout_seconds=api_timeout_seconds,
                api_token=push_token,
                tx_power_dbm=tx_power_dbm,
            )
        except Exception as exc:  # noqa: BLE001
            log_status(f"Embedded reader crashed: {exc}")

    thread = threading.Thread(target=_runner, daemon=True, name="rfid-active-reader")
    thread.start()
    log_status(
        "Embedded reader started "
        f"(host={reader_host}:{reader_port}, antennas={antennas}, push_url={push_url}, "
        f"drop_stale_reports={drop_stale_reports}, cycle_s={antenna_cycle_seconds}, "
        f"tx_power_dbm={tx_power_dbm}, "
        f"single_lock={single_instance_lock})"
    )


async def handle_fixed_reader(reader, writer):
    peer = writer.get_extra_info("peername")
    try:
        while True:
            data = await reader.read(1024)
            if not data:
                break
            try:
                raw = data.decode("utf-8", errors="ignore").strip()
                for line in raw.splitlines():
                    if "," not in line:
                        continue
                    parts = line.split(",")
                    if len(parts) < 2:
                        continue
                    tag_id = clean_tag_id(parts[0])
                    antenna_id = parse_antenna_id(parts[1])
                    await enqueue_scan_event(tag_id, antenna_id, None)
            except Exception:  # noqa: BLE001
                continue
    except asyncio.CancelledError:
        raise
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        log_status(f"Fixed reader disconnected: {peer}")


@app.on_event("startup")
async def startup():
    global global_loop, pending_tag_lock
    global_loop = asyncio.get_running_loop()
    pending_tag_lock = asyncio.Lock()

    issues = validate_runtime_config()
    for issue in issues:
        log_status(f"WARNING: {issue}")

    if issues and getattr(config, "APP_FAIL_FAST", False):
        raise RuntimeError("Unsafe production configuration. Review config.py / env settings.")

    initialize_sqlalchemy_schema()
    ensure_piece_schema()
    ensure_users_schema()
    ensure_bootstrap_admin()
    asyncio.create_task(queue_worker())
    log_status(
        "Runtime config: "
        f"env_file={getattr(config, 'LOADED_ENV_FILE', '') or 'not-found'} "
        f"app_env={getattr(config, 'APP_ENV', 'development')} "
        f"scan_cooldown={config.SCAN_COOLDOWN_SECONDS}s "
        f"in_reentry_cooldown={getattr(config, 'IN_REENTRY_COOLDOWN_SECONDS', config.SCAN_COOLDOWN_SECONDS)}s "
        f"out_block_after_in={getattr(config, 'OUT_BLOCK_AFTER_IN_SECONDS', 0)}s "
        "direction_switch_guard="
        f"{getattr(config, 'DIRECTION_SWITCH_GUARD_SECONDS', getattr(config, 'OUT_BLOCK_AFTER_IN_SECONDS', 0))}s "
        f"antenna_coalesce_window={getattr(config, 'ANTENNA_COALESCE_WINDOW_MS', 0)}ms"
    )

    log_status("Handheld reader server disabled by configuration.")

    try:
        fixed_server = await asyncio.start_server(
            handle_fixed_reader,
            config.HOST,
            config.PORT_FIXED_READER,
        )
        asyncio.create_task(fixed_server.serve_forever())
    except Exception as exc:  # noqa: BLE001
        log_status(f"Fixed reader server start failed: {exc}")

    start_embedded_active_reader()
    log_status("Real-Time Monitoring is live.")


@app.post("/login")
async def do_login(response: Response, username: str = Form(...), password: str = Form(...)):
    db = SessionLocal()
    try:
        user = db.query(User).filter(func.lower(User.username) == username.strip().lower()).first()
        if not user or not verify_password(password, user.password):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        normalized_role = normalize_user_role(user.role, default="staff")
        if str(user.role or "").strip().lower() != normalized_role:
            user.role = normalized_role
            db.commit()

        if not is_password_hashed(user.password):
            user.password = hash_password(password)
            db.commit()

        session_token = create_session_token(user.username, normalized_role)
        res = JSONResponse(content={"status": "success"})
        res.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session_token,
            httponly=True,
            secure=SESSION_COOKIE_SECURE,
            samesite=SESSION_COOKIE_SAMESITE,
            path=SESSION_COOKIE_PATH,
        )
        return res
    except HTTPException:
        raise
    except (OperationalError, SQLAlchemyError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable. Check MSSQL host, auth, and encryption settings.",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=internal_error_detail(exc)) from exc
    finally:
        db.close()


@app.get("/api/users")
def list_users(_user=Depends(require_admin_user)):
    db = SessionLocal()
    try:
        rows = db.query(User).order_by(User.username.asc()).all()
        return [
            {"username": row.username, "role": normalize_user_role(row.role, default="staff")}
            for row in rows
        ]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=internal_error_detail(exc)) from exc
    finally:
        db.close()


@app.get("/api/me")
def get_me(user=Depends(require_authenticated_user)):
    return {
        "username": user.get("username"),
        "role": normalize_user_role(user.get("role"), default="staff"),
    }


@app.post("/api/add-user")
def add_user(payload: UserCreate, _user=Depends(require_admin_user)):
    username = (payload.username or "").strip()
    password = (payload.password or "").strip()
    role = (payload.role or "staff").strip().lower()

    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")
    if role not in {"admin", "staff"}:
        raise HTTPException(status_code=400, detail="Role must be admin or staff")

    db = SessionLocal()
    try:
        existing = db.query(User.id).filter(func.lower(User.username) == username.lower()).first()
        if existing:
            raise HTTPException(status_code=409, detail="Username already exists")

        db.add(
            User(
                username=username,
                password=hash_password(password),
                role=role,
            )
        )
        db.commit()
        return {"status": "success", "username": username, "role": role}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=500, detail=internal_error_detail(exc)) from exc
    finally:
        db.close()


@app.get("/api/stats")
def get_stats(_user=Depends(require_authenticated_user)):
    db = SessionLocal()
    try:
        transition_rows = (
            db.query(ScanLog.id, ScanLog.tag_id, ScanLog.direction)
            .order_by(ScanLog.id.asc())
            .all()
        )
        normalized = compute_transition_stats(transition_rows)
        return {
            "total": db.query(JewelleryItem).count(),
            "in": normalized["in"],
            "out": normalized["out"],
        }
    finally:
        db.close()


@app.get("/health/live")
def health_live():
    return {"status": "ok", "time": datetime.datetime.utcnow().isoformat() + "Z"}


@app.get("/health/ready")
def health_ready():
    issues: List[str] = []
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        issues.append(f"database: {exc}")
    finally:
        db.close()

    if issues:
        return JSONResponse(status_code=503, content={"status": "degraded", "issues": issues})
    return {"status": "ready"}


@app.get("/api/logs")
def get_logs(
    date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 10000,
    _user=Depends(require_authenticated_user),
):
    db = SessionLocal()
    try:
        query = db.query(ScanLog)
        safe_limit = max(1, min(limit, config.MAX_LOG_LIMIT))

        if date:
            if start_date or end_date:
                raise HTTPException(
                    status_code=400,
                    detail="Use either 'date' or 'start_date/end_date', not both.",
                )
            normalized = normalize_date_or_raise(date, "date")
            query = query.filter(ScanLog.date == normalized)
        else:
            normalized_start = normalize_date_or_raise(start_date, "start_date") if start_date else None
            normalized_end = normalize_date_or_raise(end_date, "end_date") if end_date else None

            if normalized_start and normalized_end and normalized_start > normalized_end:
                raise HTTPException(status_code=400, detail="start_date must be less than or equal to end_date")
            if normalized_start:
                query = query.filter(ScanLog.date >= normalized_start)
            if normalized_end:
                query = query.filter(ScanLog.date <= normalized_end)

        return query.order_by(desc(ScanLog.id)).limit(safe_limit).all()
    finally:
        db.close()


@app.get("/api/report-summary")
def report_summary(target_date: str, _user=Depends(require_authenticated_user)):
    normalized_date = normalize_date_or_raise(target_date, "target_date")
    db = SessionLocal()
    try:
        day_logs = (
            db.query(ScanLog.id, ScanLog.tag_id, ScanLog.direction)
            .filter(ScanLog.date == normalized_date)
            .order_by(ScanLog.id.asc())
            .all()
        )
        normalized = compute_transition_stats(day_logs)

        return {
            "target_date": normalized_date,
            "in_count": normalized["in"],
            "out_count": normalized["out"],
            "pending_count": normalized["pending"],
        }
    finally:
        db.close()


@app.get("/api/missing-items")
def missing_items(target_date: str, _user=Depends(require_authenticated_user)):
    normalized_date = normalize_date_or_raise(target_date, "target_date")
    db = SessionLocal()
    try:
        day_logs = (
            db.query(ScanLog)
            .filter(ScanLog.date == normalized_date)
            .order_by(desc(ScanLog.id))
            .all()
        )

        latest_by_tag = {}
        for row in day_logs:
            if row.tag_id not in latest_by_tag:
                latest_by_tag[row.tag_id] = row

        pending_tag_ids = [tag_id for tag_id, row in latest_by_tag.items() if row.direction == "IN"]
        if not pending_tag_ids:
            return []

        items = (
            db.query(JewelleryItem)
            .filter(JewelleryItem.tag_id.in_(pending_tag_ids))
            .all()
        )
        item_by_tag = {item.tag_id: item for item in items}

        result = []
        for tag_id in pending_tag_ids:
            row = latest_by_tag[tag_id]
            item = item_by_tag.get(tag_id)
            result.append(
                {
                    "tag_id": tag_id,
                    "item_name": item.item_name if item else row.item_name,
                    "category": item.category if item else row.category,
                    "huid": item.huid if item else row.huid,
                    "weight": item.weight if item else row.weight,
                    "piece": item.piece if item else row.piece,
                    "last_direction": row.direction,
                    "last_timestamp": row.timestamp,
                    "date": normalized_date,
                }
            )

        return result
    finally:
        db.close()


@app.post("/register-item")
def register_item(item: ItemRegister, _user=Depends(require_authenticated_user)):
    # Create a new inventory row after duplicate tag validation.
    db = SessionLocal()
    try:
        normalized_tag = clean_tag_id(item.tag_id)
        if not normalized_tag:
            raise HTTPException(status_code=400, detail="Invalid tag_id")
        exists = (
            db.query(JewelleryItem.tag_id)
            .filter(func.trim(JewelleryItem.tag_id) == normalized_tag)
            .first()
        )
        if exists:
            raise HTTPException(status_code=409, detail="Tag is already registered")

        payload = item.model_dump()
        payload["tag_id"] = normalized_tag
        new_entry = JewelleryItem(
            **payload,
            timestamp=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        db.add(new_entry)
        db.commit()
        return {"status": "success", "message": "Item registered", "tag_id": normalized_tag}
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=500, detail=internal_error_detail(exc)) from exc
    finally:
        db.close()


@app.patch("/update-item/{tag_id}")
def update_item(tag_id: str, item: ItemUpdate, _user=Depends(require_admin_user)):
    # Update only mutable fields; tag_id remains the stable primary key.
    db = SessionLocal()
    try:
        normalized_tag = clean_tag_id(tag_id)
        if not normalized_tag:
            raise HTTPException(status_code=400, detail="Invalid tag_id")

        existing_item = (
            db.query(JewelleryItem)
            .filter(func.trim(JewelleryItem.tag_id) == normalized_tag)
            .first()
        )
        if not existing_item:
            raise HTTPException(status_code=404, detail="Item not found")

        updates = item.model_dump(exclude_unset=True, exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="At least one field is required to update")

        for field, value in updates.items():
            if isinstance(value, str):
                value = value.strip()
            setattr(existing_item, field, value)

        existing_item.timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        db.commit()
        return {"status": "success", "message": "Item updated", "tag_id": normalized_tag}
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=500, detail=internal_error_detail(exc)) from exc
    finally:
        db.close()


@app.get("/api/item/{tag_id}")
def get_item_by_tag(tag_id: str, _user=Depends(require_authenticated_user)):
    # Return one registered item so the UI can auto-fill update form fields.
    db = SessionLocal()
    try:
        normalized_tag = clean_tag_id(tag_id)
        if not normalized_tag:
            raise HTTPException(status_code=400, detail="Invalid tag_id")

        item = (
            db.query(JewelleryItem)
            .filter(func.trim(JewelleryItem.tag_id) == normalized_tag)
            .first()
        )
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        return {
            "tag_id": item.tag_id,
            "item_name": item.item_name,
            "category": item.category,
            "metal_type": item.metal_type,
            "purity": item.purity,
            "weight": item.weight,
            "huid": item.huid,
            "piece": item.piece,
            "timestamp": item.timestamp,
        }
    finally:
        db.close()


@app.get("/api/hello")
def hello():
    return {"message": "Hello! The RFID IN/OUT API is up and running."}


@app.get("/api/all-inventory")
def get_all_inventory(_user=Depends(require_authenticated_user)):
    db = SessionLocal()
    try:
        return db.query(JewelleryItem).all()
    finally:
        db.close()


@app.post("/reader/data")
async def receive_data(request: Request, data: dict):
    verify_rfid_push_token(request)
    tag_id = clean_tag_id(data.get("tag_id"))
    if not tag_id:
        raise HTTPException(status_code=400, detail="tag_id is required")

    antenna_raw = data.get("antenna_id", data.get("antenna", 1))
    antenna_id = parse_antenna_id(antenna_raw)
    rssi = parse_rssi_value(data.get("rssi", data.get("peak_rssi")))
    await enqueue_scan_event(tag_id, antenna_id, rssi)
    return {"status": "ok", "tag_id": tag_id, "antenna_id": antenna_id, "rssi": rssi}


@app.post("/rfid-read")
async def receive_rfid_read(request: Request, data: dict):
    return await receive_data(request, data)


@app.websocket("/ws/live-monitoring")
async def websocket_endpoint(websocket: WebSocket):
    user = get_websocket_user(websocket)
    if user is None:
        await websocket.close(code=1008)
        return
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/index.html")
async def read_index_file(user=Depends(get_current_user)):
    if user is None:
        return apply_no_cache_headers(RedirectResponse(url="/login"))
    return apply_no_cache_headers(FileResponse(os.path.join(STATIC_DIR, "index.html")))


@app.get("/static/index.html")
async def read_static_index(user=Depends(get_current_user)):
    if user is None:
        return apply_no_cache_headers(RedirectResponse(url="/login"))
    return apply_no_cache_headers(FileResponse(os.path.join(STATIC_DIR, "index.html")))


if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def read_index(user=Depends(get_current_user)):
    if user is None:
        return apply_no_cache_headers(RedirectResponse(url="/login"))
    return apply_no_cache_headers(FileResponse(os.path.join(STATIC_DIR, "index.html")))


@app.get("/login")
def login_page():
    return apply_no_cache_headers(FileResponse(os.path.join(STATIC_DIR, "login.html")))


@app.get("/logout")
async def logout():
    res = RedirectResponse(url="/login")
    res.delete_cookie(SESSION_COOKIE_NAME, path=SESSION_COOKIE_PATH)
    return apply_no_cache_headers(res)
