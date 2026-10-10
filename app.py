import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import UniqueConstraint, inspect, text
from sqlalchemy.exc import IntegrityError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

load_dotenv()


def normalize_db_url(url: str | None) -> str:
    """Normalize common Supabase/Postgres URL forms for SQLAlchemy."""
    if not url:
        return "sqlite:///local_dev.db"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


on_railway = any(os.getenv(k) for k in ("RAILWAY_PROJECT_ID", "RAILWAY_ENVIRONMENT_ID", "RAILWAY_PUBLIC_DOMAIN"))
on_render = any(os.getenv(k) for k in ("RENDER_SERVICE_ID", "RENDER_EXTERNAL_HOSTNAME")) or os.getenv("RENDER", "").lower() == "true"
on_hosted_platform = on_railway or on_render
raw_main_db_url = os.getenv("DATABASE_URL_MAIN") or (None if on_hosted_platform else os.getenv("DATABASE_URL"))
if on_hosted_platform and not raw_main_db_url:
    raise RuntimeError("Set DATABASE_URL_MAIN to your Supabase PostgreSQL connection URL in the hosting provider's environment variables.")
main_db_url = normalize_db_url(raw_main_db_url)
courses_db_url = normalize_db_url(os.getenv("DATABASE_URL_COURSES") or main_db_url)
audit_db_url = normalize_db_url(os.getenv("DATABASE_URL_AUDIT") or main_db_url)
configured_secret = os.getenv("SECRET_KEY", "").strip()
placeholder_secret = not configured_secret or "replace-with" in configured_secret.lower() or configured_secret == "development-only-change-me"
if on_hosted_platform and placeholder_secret:
    raise RuntimeError("Set a unique, random SECRET_KEY in the hosting provider's environment variables before deploying.")
if placeholder_secret:
    # Safe for local preview; sessions reset after restart. Replace this in .env.
    configured_secret = secrets.token_urlsafe(48)

cookie_secure_setting = os.getenv("COOKIE_SECURE", "true" if on_hosted_platform else "false").lower() in {"1", "true", "yes"}
if on_hosted_platform:
    # The public host terminates TLS at its trusted edge proxy.
    cookie_secure_setting = True

app = Flask(__name__)
if on_hosted_platform:
    # Render/Railway terminate TLS at their trusted edge proxy; trust one forwarded hop only.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=configured_secret,
    SQLALCHEMY_DATABASE_URI=main_db_url,
    SQLALCHEMY_BINDS={"courses": courses_db_url, "audit": audit_db_url},
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True, "pool_size": 2, "max_overflow": 0},
    WTF_CSRF_ENABLED=os.getenv("WTF_CSRF_ENABLED", "true").lower() in {"1", "true", "yes"},
    # Default is 1 hour; a long lesson page would then fail its heartbeat/forms. The token stays bound to the session.
    WTF_CSRF_TIME_LIMIT=None,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=cookie_secure_setting,
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SAMESITE="Lax",
    REMEMBER_COOKIE_SECURE=cookie_secure_setting,
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "سجّل دخولك أولاً."
login_manager.login_message_category = "info"


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# A device is considered "active now" when it sent a browser heartbeat in this window.
DEVICE_ACTIVE_WINDOW_SECONDS = 120


def env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Anti-sharing controls.
SINGLE_SESSION = env_flag("SINGLE_SESSION", True)        # a newer login signs the older one out
WATERMARK_ENABLED = env_flag("WATERMARK_ENABLED", True)  # moving watermark with the student's identity
FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")
# Native Android sends a random UUID generated per app installation. Never store it raw.
APP_INSTALLATION_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
# Sharing alerts shown at the top of the admin dashboard (stored in the audit log, no extra table).
SHARING_ALERT_EVENT = "sharing_alert"
SHARING_ALERT_DISMISSED_EVENT = "sharing_alert_dismissed"
SHARING_ALERT_WINDOW_DAYS = 7


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    username = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="student")
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending, active, suspended
    whatsapp_number = db.Column(db.String(32), nullable=True)
    # Kept for compatibility with existing schemas; email verification is disabled and ignored.
    email_verified = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)
    session_hash = db.Column(db.String(64), nullable=True)  # hash of the one currently valid login session
    session_version = db.Column(db.Integer, nullable=False, default=0)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def account_is_active(self):
        """Student access remains active until an administrator deactivates it."""
        return self.status == "active"

    @property
    def whatsapp_url(self):
        digits = re.sub(r"\D", "", self.whatsapp_number or "")
        return f"https://wa.me/{digits}" if 7 <= len(digits) <= 15 else None


class Device(db.Model):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("user_id", "token_hash", name="uq_user_device_token"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False)
    label = db.Column(db.String(160), nullable=False, default="متصفح غير معروف")
    user_agent = db.Column(db.String(512), nullable=True)
    first_ip = db.Column(db.String(64), nullable=True)
    last_ip = db.Column(db.String(64), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending")  # approved, pending, blocked
    first_seen_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_active_at = db.Column(db.DateTime, nullable=True, index=True)
    fp_hash = db.Column(db.String(64), nullable=True, index=True)  # SHA-256 of browser/device signals
    app_installation_hash = db.Column(db.String(64), nullable=True)  # SHA-256 of native APK installation UUID
    app_public_key = db.Column(db.String(2048), nullable=True)  # Base64url SubjectPublicKeyInfo; public key only
    app_public_key_hash = db.Column(db.String(64), nullable=True, index=True)  # SHA-256 of canonical public key DER
    relink_count = db.Column(db.Integer, nullable=False, default=0)
    user = db.relationship("User", backref=db.backref("devices", lazy=True, cascade="all, delete-orphan"))


class SecurityEvent(db.Model):
    __bind_key__ = "audit"
    __tablename__ = "security_events"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    event_type = db.Column(db.String(80), nullable=False, index=True)
    details = db.Column(db.String(800), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)


def invalidate_user_sessions(user):
    """Revoke every authenticated session and clear stale device-presence markers."""
    user.session_hash = None
    user.session_version = (user.session_version or 0) + 1
    for device in Device.query.filter_by(user_id=user.id).all():
        device.last_active_at = None


class Course(db.Model):
    __bind_key__ = "courses"
    __tablename__ = "courses"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    is_published = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class Lesson(db.Model):
    __bind_key__ = "courses"
    __tablename__ = "lessons"
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    vdocipher_video_id = db.Column(db.String(128), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=1)
    is_published = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    course = db.relationship("Course", backref=db.backref("lessons", lazy=True, cascade="all, delete-orphan", order_by="Lesson.sort_order"))


class DeviceChallenge(db.Model):
    """Single-use server challenge binding account, app installation, and public key."""
    __tablename__ = "device_challenges"
    id = db.Column(db.String(64), primary_key=True)
    email_hash = db.Column(db.String(64), nullable=False, index=True)
    installation_hash = db.Column(db.String(64), nullable=False, index=True)
    public_key_hash = db.Column(db.String(64), nullable=False, index=True)
    challenge_hash = db.Column(db.String(64), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    used_at = db.Column(db.DateTime, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)


def whatsapp_support_url(user, reason: str = "device") -> str | None:
    digits = re.sub(r"\D", "", os.getenv("SUPPORT_WHATSAPP_NUMBER", ""))
    if not 7 <= len(digits) <= 15:
        return None
    message = (
        "مرحباً، أحتاج مساعدة من Infinite Academy. "
        f"البريد المسجّل: {user.email}. "
        + ("أطلب مراجعة/تفعيل جهاز جديد." if reason == "device" else "أحتاج مساعدة في الحساب.")
    )
    return f"https://wa.me/{digits}?text={quote(message)}"


def validate_password(password: str) -> str | None:
    if len(password) < 12:
        return "كلمة المرور يجب أن تكون 12 محرفاً على الأقل."
    if len(password) > 128:
        return "كلمة المرور طويلة جداً؛ الحد الأقصى 128 محرفاً."
    return None


def valid_email(email: str) -> bool:
    if not email or len(email) > 254 or any(ch.isspace() for ch in email) or email.count("@") != 1:
        return False
    local, domain = email.split("@", 1)
    return bool(local and domain and "." in domain and not domain.startswith(".")
                and not domain.endswith(".") and not local.startswith(".") and not local.endswith("."))


def email_key(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


def device_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def posted_fingerprint() -> str | None:
    """Fingerprint hash sent by static/device-fingerprint.js (login form field or heartbeat header)."""
    value = (request.form.get("fp") or request.headers.get("X-Device-Fingerprint") or "").strip().lower()
    return value if FINGERPRINT_RE.fullmatch(value) else None


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def b64url_decode(value: str, *, max_len: int = 4096) -> bytes:
    if not isinstance(value, str) or not value or len(value) > max_len:
        raise ValueError("invalid base64url value")
    padded = value + ("=" * (-len(value) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def parse_android_installation_id(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError("invalid installation ID")
    value = value.strip().lower()
    if not APP_INSTALLATION_ID_RE.fullmatch(value):
        raise ValueError("invalid installation ID")
    return value, device_token_hash(value)


def parse_android_public_key(value: str):
    """Accept only an Android Keystore EC P-256 public key encoded as DER SPKI/base64url."""
    der = b64url_decode(value, max_len=2048)
    key = serialization.load_der_public_key(der)
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError("Android device key must be EC P-256")
    canonical_der = key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return key, b64url_encode(canonical_der), hashlib.sha256(canonical_der).hexdigest()


def create_device_challenge(payload: dict):
    """Create a short-lived challenge bound to an account email, install ID, and public key."""
    email = str(payload.get("email", "")).strip().lower()[:254]
    if not valid_email(email):
        raise ValueError("invalid email")
    _installation_id, installation_hash = parse_android_installation_id(payload.get("installation_id", ""))
    _public_key, public_key_b64, public_key_hash = parse_android_public_key(payload.get("public_key", ""))
    now = utcnow()
    challenge_id = secrets.token_urlsafe(24)
    nonce = secrets.token_bytes(32)
    row = DeviceChallenge(
        id=challenge_id,
        email_hash=email_key(email),
        installation_hash=installation_hash,
        public_key_hash=public_key_hash,
        challenge_hash=hashlib.sha256(nonce).hexdigest(),
        created_at=now,
        expires_at=now + timedelta(seconds=90),
        ip_address=client_ip(),
    )
    # Bound table growth. A challenge is never reusable and expires after 90 seconds.
    DeviceChallenge.query.filter(DeviceChallenge.created_at < now - timedelta(days=1)).delete(
        synchronize_session=False
    )
    db.session.add(row)
    db.session.commit()
    return {
        "challenge_id": challenge_id,
        "challenge": b64url_encode(nonce),
        "expires_in": 90,
        "public_key": public_key_b64,
    }


def verify_native_device_proof(user_email: str) -> dict | None:
    """Verify one-time ECDSA proof. Returns a normalized device identity or None."""
    try:
        installation_id, installation_hash = parse_android_installation_id(
            request.form.get("app_installation_id", "")
        )
        key, public_key_b64, public_key_hash = parse_android_public_key(
            request.form.get("app_public_key", "")
        )
        challenge_id = request.form.get("app_challenge_id", "").strip()
        nonce = b64url_decode(request.form.get("app_challenge", ""), max_len=256)
        signature = b64url_decode(request.form.get("app_signature", ""), max_len=512)
        if len(nonce) != 32 or not challenge_id or len(challenge_id) > 64:
            return None
        # Lock the challenge on PostgreSQL so two parallel submissions cannot both consume it.
        challenge_query = DeviceChallenge.query.filter_by(id=challenge_id)
        if db.engine.dialect.name == "postgresql":
            challenge_query = challenge_query.with_for_update()
        challenge = challenge_query.first()
        if (
            not challenge
            or challenge.used_at is not None
            or challenge.expires_at < utcnow()
            or not hmac.compare_digest(challenge.email_hash, email_key(user_email))
            or not hmac.compare_digest(challenge.installation_hash, installation_hash)
            or not hmac.compare_digest(challenge.public_key_hash, public_key_hash)
            or not hmac.compare_digest(challenge.challenge_hash, hashlib.sha256(nonce).hexdigest())
        ):
            return None
        key.verify(signature, nonce, ec.ECDSA(hashes.SHA256()))
        challenge.used_at = utcnow()
        db.session.commit()
        return {
            "installation_id": installation_id,
            "installation_hash": installation_hash,
            "public_key": public_key_b64,
            "public_key_hash": public_key_hash,
        }
    except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm, base64.binascii.Error):
        db.session.rollback()
        return None


def watermark_text(user) -> str:
    """Identity text burned into the video. Kept ASCII (email + phone digits) to be safe in the player."""
    phone = re.sub(r"\D", "", user.whatsapp_number or "")
    parts = [user.email] + ([phone] if phone else [])
    return " | ".join(parts)[:120]


def build_otp_payload(user) -> dict:
    payload = {"ttl": 300}
    if WATERMARK_ENABLED:
        # VdoCipher expects `annotate` as a JSON-serialised string inside the JSON body.
        payload["annotate"] = json.dumps([{
            "type": "rtext",
            "text": watermark_text(user),
            "alpha": "0.55",
            "color": "0xFF0000",
            "size": "14",
            "interval": "5000",
        }])
    return payload


def other_active_device_count(user) -> int:
    """Approved devices of this student, other than the current browser, active in the last 2 minutes."""
    cutoff = utcnow() - timedelta(seconds=DEVICE_ACTIVE_WINDOW_SECONDS)
    return Device.query.filter(
        Device.user_id == user.id,
        Device.status == "approved",
        Device.token_hash != device_token_hash(g.device_token),
        Device.last_active_at >= cutoff,
    ).count()


def flag_concurrent_devices(user, kind: str) -> int:
    """Raise an admin-dashboard alert if another device of this student is active right now."""
    if user.role != "student":
        return 0
    count = other_active_device_count(user)
    if count:
        log_event(SHARING_ALERT_EVENT, user.id, f"kind={kind}; other_active_devices={count}")
    return count


def summarize_sharing_events(events) -> dict:
    """Per student: how many open alerts, when the last one happened, and its kind.
    A dismissal (or deactivating the account) closes all earlier alerts of that student."""
    state = {}
    for event in sorted(events, key=lambda ev: ev.created_at):
        if event.event_type == SHARING_ALERT_DISMISSED_EVENT:
            state.pop(event.user_id, None)
        elif event.event_type == SHARING_ALERT_EVENT:
            entry = state.setdefault(event.user_id, {"count": 0})
            entry["count"] += 1
            entry["last_at"] = event.created_at
            entry["kind"] = (event.details or "").split(";")[0].replace("kind=", "").strip()
    return state


def open_sharing_alerts() -> list:
    cutoff = utcnow() - timedelta(days=SHARING_ALERT_WINDOW_DAYS)
    try:
        events = (SecurityEvent.query
                  .filter(SecurityEvent.event_type.in_([SHARING_ALERT_EVENT, SHARING_ALERT_DISMISSED_EVENT]),
                          SecurityEvent.user_id.isnot(None),
                          SecurityEvent.created_at >= cutoff)
                  .order_by(SecurityEvent.created_at.asc(), SecurityEvent.id.asc()).all())
    except Exception:
        db.session.rollback()
        app.logger.exception("Could not read sharing alerts")
        return []
    state = summarize_sharing_events(events)
    if not state:
        return []
    users = {u.id: u for u in User.query.filter(User.id.in_(list(state))).all()}
    alerts = [dict(info, user=users[uid]) for uid, info in state.items() if uid in users]
    alerts.sort(key=lambda a: a["last_at"], reverse=True)
    return alerts


def start_single_session(user):
    """Make this login the only valid session for the student; older sessions get signed out."""
    if user.role != "student":
        return
    flag_concurrent_devices(user, "login_while_other_device_active")
    if not SINGLE_SESSION:
        return
    sid = secrets.token_urlsafe(32)
    session["sid"] = sid
    user.session_hash = device_token_hash(sid)


def client_ip() -> str | None:
    # Use the direct connection address by default. Only trust proxy headers if the
    # hosting proxy is configured explicitly; blindly trusting X-Forwarded-For is unsafe.
    return request.remote_addr


def log_event(event_type, user_id=None, details=None):
    try:
        event = SecurityEvent(
            user_id=user_id,
            event_type=event_type,
            details=(details or "")[:800],
            ip_address=client_ip(),
            user_agent=(request.user_agent.string or "")[:512],
        )
        db.session.add(event)
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Could not write audit event")


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if getattr(current_user, "role", None) != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _rotate_device_cookie_token() -> str:
    """Rotate the browser transport token when it belongs to a different device record."""
    g.device_token = secrets.token_urlsafe(32)
    g.device_token_is_new = True
    return device_token_hash(g.device_token)


def _touch_device_record(record, user, fp=None, app_installation_hash=None) -> bool:
    """Refresh metadata and persist the current cookie token on a resolved device record."""
    current_hash = device_token_hash(g.device_token)
    token_record = Device.query.filter_by(user_id=user.id, token_hash=current_hash).first()
    if token_record and token_record.id != record.id:
        # Do not let a cookie previously associated with another install silently switch identity.
        current_hash = _rotate_device_cookie_token()
    record.token_hash = current_hash
    if app_installation_hash:
        record.app_installation_hash = app_installation_hash
        record.label = "تطبيق Infinite Academy (Android)"
    record.last_seen_at = utcnow()
    record.last_active_at = record.last_seen_at if user.account_is_active and record.status == "approved" else None
    record.last_ip = client_ip()
    record.user_agent = (request.user_agent.string or "")[:512]
    if fp and not record.fp_hash:
        record.fp_hash = fp
    allowed = record.status == "approved"
    db.session.commit()
    if not allowed:
        flag_concurrent_devices(user, "unapproved_device_retry_while_other_active")
    return allowed


def login_device_for_user(user, fp=None, native_proof=None):
    """Resolve device access; Android installs must prove possession of their registered Keystore key."""
    token_hash = device_token_hash(g.device_token)
    app_installation_hash = native_proof.get("installation_hash") if native_proof else None

    if native_proof:
        app_record = Device.query.filter_by(
            user_id=user.id, app_installation_hash=app_installation_hash
        ).first()
        if app_record:
            candidate_key_hash = native_proof["public_key_hash"]
            if app_record.app_public_key_hash:
                if not hmac.compare_digest(app_record.app_public_key_hash, candidate_key_hash):
                    log_event("native_device_key_mismatch", user.id, f"device_id={app_record.id}")
                    return False
                return _touch_device_record(
                    app_record, user, fp, app_installation_hash
                )

            # Upgrade a legacy v9 install only with admin review; never silently bind a fresh key
            # to an already-approved keyless record. Blocked devices stay blocked.
            if app_record.status == "blocked":
                log_event("native_key_enrollment_for_blocked_device", user.id, f"device_id={app_record.id}")
                return False
            app_record.app_public_key = native_proof["public_key"]
            app_record.app_public_key_hash = candidate_key_hash
            if app_record.status == "approved":
                app_record.status = "pending"
                invalidate_user_sessions(user)
            app_record.last_active_at = None
            app_record.last_seen_at = utcnow()
            app_record.last_ip = client_ip()
            app_record.user_agent = (request.user_agent.string or "")[:512]
            db.session.commit()
            log_event("native_key_enrollment_requires_admin_approval", user.id, f"device_id={app_record.id}")
            return False

        # A native install cannot borrow an unrelated browser cookie's approval.
        cookie_record = Device.query.filter_by(user_id=user.id, token_hash=token_hash).first()
        if cookie_record:
            token_hash = _rotate_device_cookie_token()

    else:
        # A saved cookie tied to an Android install is not sufficient for a new login by itself.
        cookie_record = Device.query.filter_by(user_id=user.id, token_hash=token_hash).first()
        if cookie_record and (cookie_record.app_installation_hash or cookie_record.app_public_key_hash):
            log_event("native_device_login_without_key_proof", user.id, f"device_id={cookie_record.id}")
            return False

    record = Device.query.filter_by(user_id=user.id, token_hash=token_hash).first()
    if record:
        # Even a cookie match must not downgrade a previously native-bound device to browser auth.
        if record.app_installation_hash or record.app_public_key_hash:
            log_event("native_device_cookie_only_attempt", user.id, f"device_id={record.id}")
            return False
        return _touch_device_record(record, user, fp)

    # Serialize new-device decisions per student on PostgreSQL. This also protects concurrent first logins.
    if user.role == "student":
        db.session.query(User.id).filter(User.id == user.id).with_for_update().first()
        if native_proof:
            app_record = Device.query.filter_by(
                user_id=user.id, app_installation_hash=app_installation_hash
            ).first()
            if app_record:
                # A competing request may have registered the install while this request waited.
                if app_record.app_public_key_hash and not hmac.compare_digest(
                    app_record.app_public_key_hash, native_proof["public_key_hash"]
                ):
                    log_event("native_device_key_mismatch", user.id, f"device_id={app_record.id}")
                    return False
                if app_record.app_public_key_hash:
                    return _touch_device_record(app_record, user, fp, app_installation_hash)
        record = Device.query.filter_by(user_id=user.id, token_hash=device_token_hash(g.device_token)).first()
        if record:
            if record.app_installation_hash or record.app_public_key_hash:
                log_event("native_device_cookie_only_attempt", user.id, f"device_id={record.id}")
                return False
            return _touch_device_record(record, user, fp)

    existing_device_count = Device.query.filter_by(user_id=user.id).count()
    device_count = Device.query.filter_by(user_id=user.id, status="approved").count()
    try:
        limit = max(1, min(5, int(os.getenv("DEVICE_LIMIT", "1"))))
    except ValueError:
        limit = 1
    first_device = existing_device_count == 0
    no_unresolved_device = existing_device_count == device_count
    status = "approved" if first_device or (device_count < limit and no_unresolved_device) else "pending"
    ua = request.user_agent
    label = (
        "تطبيق Infinite Academy (Android)" if native_proof
        else (" ".join(x for x in [ua.platform, ua.browser, ua.version] if x)[:160] or "متصفح غير معروف")
    )
    record = Device(
        user_id=user.id,
        token_hash=device_token_hash(g.device_token),
        app_installation_hash=app_installation_hash,
        app_public_key=native_proof["public_key"] if native_proof else None,
        app_public_key_hash=native_proof["public_key_hash"] if native_proof else None,
        label=label,
        user_agent=(ua.string or "")[:512],
        first_ip=client_ip(),
        last_ip=client_ip(),
        status=status,
        last_active_at=utcnow() if user.account_is_active and status == "approved" else None,
        fp_hash=fp,
    )
    db.session.add(record)
    db.session.commit()
    log_event(
        "device_registered" if status == "approved" else "new_device_pending",
        user.id,
        f"device_id={record.id}; status={status}; kind={'android_app' if native_proof else 'web'}",
    )
    if status != "approved":
        flag_concurrent_devices(user, "new_device_pending_while_other_active")
    return status == "approved"


def current_device_is_approved(user) -> bool:
    token = getattr(g, "device_token", None)
    if not token:
        return False
    record = Device.query.filter_by(user_id=user.id, token_hash=device_token_hash(token), status="approved").first()
    if record:
        record.last_seen_at = utcnow()
        record.last_active_at = record.last_seen_at
        record.last_ip = client_ip()
        db.session.commit()
        return True
    return False


def student_can_stream(user):
    return user.role == "admin" or (user.account_is_active and current_device_is_approved(user))


def learning_access_denial(user, *, resource: str, resource_id: int, api: bool = False):
    """Enforce account and device policy on every course, lesson and playback path.

    Returns a Flask response when access is denied, otherwise None. Administrators
    intentionally bypass the student device policy so they can review course content.
    """
    if user.role == "admin":
        return None

    if not user.account_is_active:
        log_event("learning_access_denied_inactive_account", user.id,
                  f"resource={resource}; resource_id={resource_id}; status={user.status}")
        message = "مشاهدة المحتوى تتطلب تفعيل حسابك من الإدارة."
        if api:
            return jsonify({"error": "account_inactive", "message": message}), 403
        flash(message, "error")
        return redirect(url_for("dashboard"))

    if not current_device_is_approved(user):
        log_event("unapproved_device_stream_attempt", user.id,
                  f"resource={resource}; resource_id={resource_id}")
        message = "هذا الجهاز غير معتمد. راجع الإدارة لاعتماده."
        if api:
            return jsonify({"error": "device_not_approved", "message": message}), 403
        flash(message, "error")
        return redirect(url_for("dashboard"))

    return None


@app.before_request
def get_or_create_device_token():
    g.device_token = request.cookies.get("course_device") or secrets.token_urlsafe(32)
    g.device_token_is_new = not bool(request.cookies.get("course_device"))


@app.before_request
def enforce_single_session():
    """Invalidate revoked sessions and sign out student sessions replaced by a newer login."""
    if request.endpoint in (None, "static", "health") or not current_user.is_authenticated:
        return None

    if session.get("session_version") != current_user.session_version:
        logout_user()
        if request.endpoint == "login":
            return None
        if request.endpoint == "lesson_playback":
            return jsonify({"error": "session_revoked", "message": "انتهت صلاحية جلسة الدخول. سجّل الدخول مرة أخرى."}), 401
        if request.endpoint == "device_heartbeat":
            return jsonify({"status": "session_revoked"}), 401
        flash("تم إبطال جلسة الدخول لأسباب أمنية. سجّل الدخول مرة أخرى.", "error")
        return redirect(url_for("login"))

    if current_user.role == "student" and current_user.status == "suspended":
        logout_user()
        if request.endpoint == "login":
            flash("حسابك غير مفعّل حالياً. تواصل مع الإدارة.", "error")
            return None
        if request.endpoint == "lesson_playback":
            return jsonify({"error": "account_suspended", "message": "تم إيقاف حسابك من الإدارة."}), 401
        if request.endpoint == "device_heartbeat":
            return jsonify({"status": "account_suspended"}), 401
        flash("تم إيقاف حسابك من الإدارة. سجّل الدخول بعد مراجعة الحالة.", "error")
        return redirect(url_for("login"))

    if current_user.role != "student" or not SINGLE_SESSION:
        return None
    sid = session.get("sid")
    if sid and current_user.session_hash and hmac.compare_digest(device_token_hash(sid), current_user.session_hash):
        return None
    logout_user()
    if request.endpoint == "login":
        return None  # let the normal login page/POST proceed as a fresh visitor
    if request.endpoint == "lesson_playback":
        return jsonify({"error": "session_replaced", "message": "تم تسجيل الدخول لهذا الحساب من جهاز آخر. سجّل الدخول مجدداً."}), 401
    flash("تم تسجيل الدخول لهذا الحساب من جهاز آخر، لذلك أُغلقت هذه الجلسة. سجّل دخولك من جديد.", "error")
    if request.endpoint == "device_heartbeat":
        return jsonify({"status": "session_replaced"}), 401
    return redirect(url_for("login"))


@app.after_request
def add_security_headers(response):
    if getattr(g, "device_token_is_new", False):
        response.set_cookie(
            "course_device", g.device_token,
            max_age=60 * 60 * 24 * 365,
            httponly=True,
            secure=app.config["SESSION_COOKIE_SECURE"],
            samesite="Lax",
            path="/",
        )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.endpoint in {"lesson_view", "lesson_playback", "course_detail", "dashboard"}:
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Vary"] = "Cookie"
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; frame-src https://player.vdocipher.com; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'")
    if app.config["SESSION_COOKIE_SECURE"]:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.context_processor
def inject_helpers():
    support_digits = re.sub(r"\D", "", os.getenv("SUPPORT_WHATSAPP_NUMBER", ""))
    support_enabled = 7 <= len(support_digits) <= 15
    try:
        device_limit = max(1, min(5, int(os.getenv("DEVICE_LIMIT", "1"))))
    except ValueError:
        device_limit = 1
    return {
        "now_utc": utcnow,
        "device_limit": device_limit,
        "support_whatsapp_enabled": support_enabled,
        "support_url_for_user": whatsapp_support_url,
    }


@app.route("/")
def index():
    courses = Course.query.filter_by(is_published=True).order_by(Course.created_at.desc()).all()
    return render_template("index.html", courses=courses)


def recent_event_count(event_type: str, since: datetime, ip: str | None = None, details: str | None = None) -> int:
    try:
        query = SecurityEvent.query.filter(
            SecurityEvent.event_type == event_type,
            SecurityEvent.created_at >= since,
        )
        if ip is not None:
            query = query.filter(SecurityEvent.ip_address == ip)
        if details is not None:
            query = query.filter(SecurityEvent.details == details)
        return query.count()
    except Exception:
        db.session.rollback()
        app.logger.exception("Could not check security event rate limit")
        # Keep sign-in available during an audit-store outage; other controls still apply.
        return 0


@app.post("/api/device/challenge")
def api_device_challenge():
    """Issue a short-lived one-time challenge; request is protected by the site's CSRF token."""
    ip = client_ip()
    since = utcnow() - timedelta(minutes=15)
    if ip and recent_event_count("native_challenge_request", since, ip=ip) >= 30:
        return jsonify({"error": "rate_limited", "message": "محاولات كثيرة. انتظر قليلاً ثم أعد المحاولة."}), 429
    log_event("native_challenge_request", None, "challenge_requested")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_device_identity", "message": "تعذّر قراءة هوية الجهاز."}), 400
    try:
        challenge = create_device_challenge(payload)
    except (ValueError, TypeError, UnsupportedAlgorithm, base64.binascii.Error):
        return jsonify({"error": "invalid_device_identity", "message": "تعذّر قراءة هوية الجهاز."}), 400
    response = jsonify(challenge)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response, 200


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        ip = client_ip()
        cutoff = utcnow() - timedelta(hours=1)
        if recent_event_count("registration_attempt", cutoff, ip=ip) >= 5:
            flash("وصلت إلى حد محاولات التسجيل من اتصالك. حاول بعد ساعة.", "error")
            return render_template("auth.html", mode="register"), 429
        log_event("registration_attempt", details="registration form submitted")

        username = request.form.get("username", "").strip()[:80]
        email = request.form.get("email", "").strip().lower()[:254]
        raw_whatsapp = request.form.get("whatsapp_number", "").strip()[:32]
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        accept_terms = request.form.get("accept_terms") == "yes"
        phone_digits = re.sub(r"\D", "", raw_whatsapp)
        has_country_prefix = raw_whatsapp.startswith("+") or raw_whatsapp.startswith("00")
        phone_format_ok = has_country_prefix and bool(re.fullmatch(r"[+0-9()\s.\-]{7,32}", raw_whatsapp))
        if raw_whatsapp.startswith("00"):
            whatsapp_number = "+" + phone_digits[2:]
        elif raw_whatsapp.startswith("+"):
            whatsapp_number = "+" + phone_digits
        else:
            whatsapp_number = raw_whatsapp
        password_error = validate_password(password)

        if not username:
            flash("أدخل الاسم.", "error")
        elif not valid_email(email):
            flash("أدخل بريداً إلكترونياً صحيحاً.", "error")
        elif not phone_format_ok or not 7 <= len(phone_digits) <= 15:
            flash("أدخل رقم واتساب صحيحاً مع مفتاح الدولة، مثل +... .", "error")
        elif password_error:
            flash(password_error, "error")
        elif password != confirm_password:
            flash("كلمتا المرور غير متطابقتين.", "error")
        elif not accept_terms:
            flash("يجب الموافقة على الشروط والأحكام وسياسة الخصوصية.", "error")
        elif User.query.filter_by(email=email).first():
            flash("تعذّر إنشاء الحساب بهذا البريد. جرّب تسجيل الدخول أو استخدم بريداً آخر.", "error")
        else:
            user = User(
                username=username,
                email=email,
                whatsapp_number=whatsapp_number,
                role="student",
                status="pending",
                email_verified=True,  # legacy compatibility only; never gates registration or activation
            )
            user.set_password(password)
            try:
                db.session.add(user)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("تعذّر إنشاء الحساب بهذا البريد. جرّب تسجيل الدخول أو استخدم بريداً آخر.", "error")
                return render_template("auth.html", mode="register"), 409
            log_event("student_registered", user.id, "Awaiting manual admin activation")
            flash("تم إنشاء حسابك. سيبقى بانتظار تفعيل الإدارة؛ لا يوجد تأكيد بريد أو كود تفعيل أو اشتراك شهري.", "success")
            return redirect(url_for("login"))
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()[:254]
        password = request.form.get("password", "")
        ip = client_ip()
        now = utcnow()
        cutoff = now - timedelta(minutes=15)
        hashed_email_detail = f"email_hash={email_key(email)}"
        ip_failures = recent_event_count("login_failed", cutoff, ip=ip) if ip else 0
        email_failures = recent_event_count("login_failed", cutoff, details=hashed_email_detail)
        if ip_failures >= 10 or email_failures >= 10:
            flash("محاولات كثيرة غير ناجحة. انتظر 15 دقيقة ثم حاول مجدداً.", "error")
            return render_template("auth.html", mode="login"), 429

        user = User.query.filter_by(email=email).first() if valid_email(email) else None
        if not user or len(password) > 128 or not user.check_password(password):
            log_event("login_failed", user.id if user else None, hashed_email_detail)
            flash("البريد الإلكتروني أو كلمة المرور غير صحيحة.", "error")
        else:
            if user.role != "admin" and user.status == "suspended":
                log_event("suspended_login_attempt", user.id)
                flash("حسابك غير مفعّل حالياً. تواصل مع الإدارة عبر واتساب.", "error")
                return render_template("auth.html", mode="login",
                                       support_whatsapp_url=whatsapp_support_url(user, "account"))
            if user.status not in {"active", "pending"}:
                log_event("invalid_account_status_login", user.id)
                flash("تعذّر تسجيل الدخول لهذا الحساب. تواصل مع الإدارة.", "error")
                return render_template("auth.html", mode="login",
                                       support_whatsapp_url=whatsapp_support_url(user, "account"))
            # Native app logins require an ECDSA proof from the installation's Android Keystore key.
            # Browser logins remain supported, but a native-bound cookie alone cannot re-authorize an APK.
            native_proof = None
            raw_installation_id = request.form.get("app_installation_id", "").strip()
            if user.role != "admin" and raw_installation_id:
                native_proof = verify_native_device_proof(user.email)
                if not native_proof:
                    log_event("native_device_proof_failed", user.id)
                    flash("تعذّر إثبات هوية تثبيت التطبيق. حدّث التطبيق أو اطلب من الإدارة مراجعة الجهاز.", "error")
                    return render_template("auth.html", mode="login",
                                           support_whatsapp_url=whatsapp_support_url(user, "device")), 401
            # Admins are not student devices and must never be locked out by device approval.
            if user.role != "admin" and not login_device_for_user(
                user, posted_fingerprint(), native_proof
            ):
                pending_device = None
                if native_proof:
                    pending_device = Device.query.filter_by(
                        user_id=user.id,
                        app_installation_hash=native_proof["installation_hash"],
                    ).first()
                if pending_device is None:
                    pending_device = Device.query.filter_by(
                        user_id=user.id,
                        token_hash=device_token_hash(g.device_token),
                    ).first()
                if pending_device and pending_device.status == "pending":
                    flash("تم تسجيل طلب هذا الجهاز بانتظار المراجعة. راجع الإدارة عبر واتساب المسجّل في حسابك.", "error")
                elif pending_device and pending_device.status == "blocked":
                    flash("هذا الجهاز غير معتمد حالياً. تواصل مع الإدارة لمراجعة طلبك.", "error")
                else:
                    flash("تعذّر اعتماد هذا الجهاز تلقائياً. تواصل مع الإدارة.", "error")
                return render_template("auth.html", mode="login",
                                       support_whatsapp_url=whatsapp_support_url(user, "device"))
            # Rotate the signed session before assigning an authenticated identity.
            session.clear()
            login_user(user, remember=False, fresh=True)
            session["session_version"] = user.session_version
            start_single_session(user)
            user.last_login_at = utcnow()
            db.session.commit()
            log_event("login_success", user.id)
            return redirect(url_for("dashboard"))
    return render_template("auth.html", mode="login")


@app.route("/change-password", methods=["GET", "POST"])
@admin_required
def change_password():
    user = current_user._get_current_object()
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if len(current_password) > 128 or not user.check_password(current_password):
            flash("كلمة المرور الحالية غير صحيحة.", "error")
        else:
            password_error = validate_password(new_password)
            if password_error:
                flash(password_error, "error")
            elif new_password != confirm_password:
                flash("كلمتا المرور غير متطابقتين.", "error")
            elif user.check_password(new_password):
                flash("اختر كلمة مرور مختلفة عن الحالية.", "error")
            else:
                user.set_password(new_password)
                invalidate_user_sessions(user)
                db.session.commit()
                log_event("password_changed", user.id)
                logout_user()
                session.clear()
                flash("تم تغيير كلمة المرور وإغلاق الجلسات السابقة. سجّل الدخول من جديد.", "success")
                return redirect(url_for("login"))
    return render_template("change_password.html")


@app.post("/logout")
@login_required
def logout():
    user = current_user._get_current_object()
    if user.role == "student":
        # Logging out frees the slot immediately: this device stops counting as "active now"
        # and the server stops accepting the old session.
        device = Device.query.filter_by(user_id=user.id, token_hash=device_token_hash(g.device_token)).first()
        stale = utcnow() - timedelta(seconds=DEVICE_ACTIVE_WINDOW_SECONDS + 1)
        if device and device.last_active_at and device.last_active_at > stale:
            device.last_active_at = stale
        user.session_hash = None
        db.session.commit()
    log_event("logout", user.id)
    logout_user()
    flash("تم تسجيل الخروج. تظل بصمة هذا المتصفح محفوظة حتى لا يُحسب كتثبيت جديد عند كل دخول.", "info")
    return redirect(url_for("index"))


@app.route("/dashboard")
@login_required
def dashboard():
    if current_user.role == "admin":
        return redirect(url_for("admin_dashboard"))
    # The ChemX student dashboard currently exposes Chemistry only. Keep all course
    # records intact in the database/admin area and filter only this student's view.
    published_courses = Course.query.filter_by(is_published=True).order_by(Course.created_at.desc()).all()
    chemistry_terms = ("كيمياء", "كيميا", "chemistry", "chemis")
    courses = [
        course for course in published_courses
        if any(term in (course.title or "").casefold() for term in chemistry_terms)
    ]
    devices = Device.query.filter_by(user_id=current_user.id).order_by(Device.last_seen_at.desc()).all()
    active_cutoff = utcnow() - timedelta(seconds=DEVICE_ACTIVE_WINDOW_SECONDS)
    active_device_ids = {
        d.id for d in devices
        if d.status == "approved" and d.last_active_at and d.last_active_at >= active_cutoff
    }
    return render_template("student_dashboard.html", courses=courses, devices=devices,
                           active_device_ids=active_device_ids,
                           active_device_count=len(active_device_ids))


@app.post("/device/heartbeat")
@login_required
def device_heartbeat():
    """Record current activity only for the authenticated user's approved browser device."""
    if current_user.role != "student":
        return jsonify({"status": "inactive"}), 403
    if current_user.status != "active":
        return jsonify({"status": "inactive"}), 200

    token = getattr(g, "device_token", None)
    if not token:
        return jsonify({"status": "unregistered_device"}), 403

    device = Device.query.filter_by(
        user_id=current_user.id,
        token_hash=device_token_hash(token),
        status="approved",
    ).first()
    if not device:
        return jsonify({"status": "unapproved_device"}), 403

    fp = posted_fingerprint()
    if fp and not device.fp_hash:
        device.fp_hash = fp  # older devices get their fingerprint on the first heartbeat

    now = utcnow()
    device.last_seen_at = now
    device.last_active_at = now
    device.last_ip = client_ip()
    device.user_agent = (request.user_agent.string or "")[:512]
    db.session.commit()
    return jsonify({"status": "ok", "active_window_seconds": DEVICE_ACTIVE_WINDOW_SECONDS})


@app.route("/courses/<int:course_id>")
@login_required
def course_detail(course_id):
    course = db.session.get(Course, course_id)
    if not course or not course.is_published:
        abort(404)
    denied = learning_access_denial(
        current_user._get_current_object(), resource="course", resource_id=course.id
    )
    if denied is not None:
        return denied
    lessons = Lesson.query.filter_by(course_id=course.id, is_published=True).order_by(Lesson.sort_order.asc(), Lesson.id.asc()).all()
    return render_template("course.html", course=course, lessons=lessons)


@app.route("/lessons/<int:lesson_id>")
@login_required
def lesson_view(lesson_id):
    lesson = db.session.get(Lesson, lesson_id)
    if not lesson or not lesson.is_published:
        abort(404)
    api_secret = os.getenv("VDOCIPHER_API_SECRET", "").strip()
    denied = learning_access_denial(
        current_user._get_current_object(), resource="lesson", resource_id=lesson.id
    )
    if denied is not None:
        return denied
    return render_template("lesson.html", lesson=lesson,
                           vdocipher_configured=bool(api_secret),
                           playback_url=url_for("lesson_playback", lesson_id=lesson.id))


@app.post("/lessons/<int:lesson_id>/playback")
@login_required
def lesson_playback(lesson_id):
    """Issue VdoCipher credentials only after a fresh server-side access check."""
    lesson = db.session.get(Lesson, lesson_id)
    if not lesson or not lesson.is_published:
        return jsonify({"error": "lesson_not_found", "message": "هذا الدرس غير متاح."}), 404

    denied = learning_access_denial(
        current_user._get_current_object(), resource="lesson_playback", resource_id=lesson.id, api=True
    )
    if denied is not None:
        return denied

    api_secret = os.getenv("VDOCIPHER_API_SECRET", "").strip()
    if not api_secret:
        return jsonify({"error": "video_not_configured", "message": "الفيديو غير مهيأ حالياً."}), 503

    # Copy what is needed, then release pooled DB connections before the slow external call
    # (the pool is small: pool_size=2 per database, with 4 gunicorn threads).
    video_id = lesson.vdocipher_video_id
    lesson_pk = lesson.id
    user_pk = current_user.id
    otp_payload = build_otp_payload(current_user)
    db.session.commit()

    try:
        response = requests.post(
            f"https://dev.vdocipher.com/api/videos/{quote(video_id, safe='')}/otp",
            headers={"Authorization": f"Apisecret {api_secret}", "Content-Type": "application/json"},
            json=otp_payload,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("otp") or not data.get("playbackInfo"):
            raise ValueError("VdoCipher response did not contain playback credentials")
        log_event("vdocipher_otp_issued", user_pk, f"lesson_id={lesson_pk}")
        return jsonify({"otp": data["otp"], "playback_info": data["playbackInfo"]})
    except Exception:
        app.logger.exception("VdoCipher OTP request failed")
        log_event("vdocipher_otp_failed", user_pk, f"lesson_id={lesson_pk}")
        return jsonify({"error": "video_unavailable", "message": "تعذّر تجهيز الفيديو حالياً. حاول لاحقاً."}), 502


@app.route("/admin")
@admin_required
def admin_dashboard():
    users = User.query.filter_by(role="student").order_by(User.created_at.desc()).all()
    devices = Device.query.order_by(Device.last_seen_at.desc()).all()
    active_cutoff = utcnow() - timedelta(seconds=DEVICE_ACTIVE_WINDOW_SECONDS)
    active_device_ids = {
        d.id for d in devices
        if d.status == "approved" and d.last_active_at and d.last_active_at >= active_cutoff
    }
    device_counts = {}
    for u in users:
        user_devices = [d for d in devices if d.user_id == u.id]
        device_counts[u.id] = {
            "total": len(user_devices),
            "approved": sum(d.status == "approved" for d in user_devices),
            "active": sum(d.id in active_device_ids for d in user_devices),
        }
    courses = Course.query.order_by(Course.created_at.desc()).all()
    lessons = Lesson.query.order_by(Lesson.course_id, Lesson.sort_order, Lesson.id).all()
    db_health = {}
    for role, engine in db.engines.items():
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            db_health[role] = "متصلة"
        except Exception:
            db_health[role] = "خطأ اتصال"
    sharing_alerts = open_sharing_alerts()
    return render_template("admin.html", users=users, devices=devices, courses=courses,
                           sharing_alerts=sharing_alerts,
                           sharing_alert_total=sum(a["count"] for a in sharing_alerts),
                           lessons=lessons, db_health=db_health,
                           active_device_ids=active_device_ids,
                           active_device_count=len(active_device_ids),
                           device_counts=device_counts,
                           active_window_seconds=DEVICE_ACTIVE_WINDOW_SECONDS)


@app.get("/admin/device-activity")
@admin_required
def admin_device_activity():
    """Small admin-only polling endpoint so live status updates without a page reload."""
    cutoff = utcnow() - timedelta(seconds=DEVICE_ACTIVE_WINDOW_SECONDS)
    devices = Device.query.all()
    active = [
        d for d in devices
        if d.status == "approved" and d.last_active_at and d.last_active_at >= cutoff
    ]
    active_by_user = {}
    for device in active:
        key = str(device.user_id)
        active_by_user[key] = active_by_user.get(key, 0) + 1
    pending_devices = Device.query.filter_by(status="pending").all()
    return jsonify({
        "active_device_count": len(active),
        "active_device_ids": [d.id for d in active],
        "active_by_user": active_by_user,
        "pending_device_ids": [d.id for d in pending_devices],
        "pending_device_count": len(pending_devices),
        "active_window_seconds": DEVICE_ACTIVE_WINDOW_SECONDS,
        "sharing_alert_total": sum(a["count"] for a in open_sharing_alerts()),
        "checked_at": utcnow().isoformat(timespec="seconds") + "Z",
    })


@app.post("/admin/users/<int:user_id>/activate")
@admin_required
def admin_activate_user(user_id):
    user = db.session.get(User, user_id)
    if not user or user.role == "admin":
        abort(404)
    user.status = "active"
    db.session.commit()
    log_event("admin_activated_user", user.id, f"by_admin={current_user.id}")
    flash(f"تم تفعيل حساب {user.email}. سيبقى مفعّلاً حتى تلغي تفعيله يدوياً.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/users/<int:user_id>/reset-password")
@admin_required
def admin_reset_student_password(user_id):
    """Set a student password manually from the admin dashboard; no email is sent."""
    user = db.session.get(User, user_id)
    if not user or user.role == "admin":
        abort(404)

    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")
    password_error = validate_password(new_password)
    if password_error:
        flash(password_error, "error")
        return redirect(url_for("admin_dashboard"))
    if new_password != confirm_password:
        flash("كلمتا المرور غير متطابقتين؛ لم يتم تغيير كلمة المرور.", "error")
        return redirect(url_for("admin_dashboard"))
    if user.check_password(new_password):
        flash("كلمة المرور الجديدة مطابقة للحالية. أدخل كلمة مختلفة.", "error")
        return redirect(url_for("admin_dashboard"))

    user.set_password(new_password)
    invalidate_user_sessions(user)
    db.session.commit()
    log_event("admin_reset_student_password", user.id, f"by_admin={current_user.id}")
    flash(f"تم تعيين كلمة مرور جديدة لحساب {user.email} وإغلاق جلساته السابقة. أبلغ الطالب بها بطريقة آمنة.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/users/<int:user_id>/suspend")
@admin_required
def admin_suspend_user(user_id):
    user = db.session.get(User, user_id)
    if not user or user.role == "admin":
        abort(404)
    user.status = "suspended"
    invalidate_user_sessions(user)
    db.session.commit()
    log_event("admin_deactivated_user", user.id, f"by_admin={current_user.id}")
    log_event(SHARING_ALERT_DISMISSED_EVENT, user.id, "auto: account deactivated")
    flash(f"تم إلغاء تفعيل حساب {user.email}.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/alerts/<int:user_id>/dismiss")
@admin_required
def admin_dismiss_sharing_alert(user_id):
    user = db.session.get(User, user_id)
    if not user or user.role == "admin":
        abort(404)
    log_event(SHARING_ALERT_DISMISSED_EVENT, user.id, f"by_admin={current_user.id}")
    flash("تم إخفاء التنبيه. إذا تكرر الوضع يظهر تنبيه جديد.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/devices/<int:device_id>/approve")
@admin_required
def admin_approve_device(device_id):
    device = db.session.get(Device, device_id)
    if not device:
        abort(404)
    replaced_count = 0
    owner = db.session.get(User, device.user_id)
    if owner and owner.role == "student":
        # Keep concurrent manual approvals for this student within the configured cap.
        db.session.query(User.id).filter(User.id == owner.id).with_for_update().first()
    # Enforce the configured limit on approval too; keep the requested device and
    # retain older rows as a review/audit trail rather than deleting them.
    device.status = "approved"
    device.relink_count = 0
    device.last_seen_at = utcnow()
    device.last_active_at = None  # approval is not proof the student has logged in on this device yet
    if owner and owner.role == "student":
        try:
            limit = max(1, min(5, int(os.getenv("DEVICE_LIMIT", "1"))))
        except ValueError:
            limit = 1
        previously_approved = Device.query.filter(
            Device.user_id == owner.id,
            Device.status == "approved",
            Device.id != device.id,
        ).order_by(Device.last_seen_at.desc(), Device.id.desc()).all()
        # The newly approved device is retained; retire the least-recently-seen others.
        for previous in previously_approved[max(0, limit - 1):]:
            previous.status = "blocked"
            replaced_count += 1
        # Force every existing session to re-authenticate after a manual device change.
        invalidate_user_sessions(owner)
    db.session.commit()
    log_event("admin_approved_device", device.user_id,
              f"device_id={device.id}; replaced_previous={replaced_count}; by_admin={current_user.id}")
    if replaced_count:
        flash("تم اعتماد الجهاز الجديد وإيقاف اعتماد الجهاز السابق للطالب.", "success")
    else:
        flash("تم اعتماد الجهاز.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/devices/<int:device_id>/block")
@admin_required
def admin_block_device(device_id):
    device = db.session.get(Device, device_id)
    if not device:
        abort(404)
    was_approved = device.status == "approved"
    owner = db.session.get(User, device.user_id)
    device.status = "blocked"
    device.last_active_at = None
    if was_approved and owner and owner.role == "student":
        invalidate_user_sessions(owner)
    db.session.commit()
    log_event("admin_blocked_device", device.user_id, f"device_id={device.id}; by_admin={current_user.id}")
    flash("تم حظر الجهاز.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/devices/<int:device_id>/unlink")
@admin_required
def admin_unlink_device(device_id):
    device = db.session.get(Device, device_id)
    if not device:
        abort(404)
    user_id = device.user_id
    was_approved = device.status == "approved"
    owner = db.session.get(User, user_id)
    # Keep a blocked audit row so deleting browser data cannot make a future device
    # look like the student's first-ever installation and silently auto-approve.
    device.status = "blocked"
    device.last_active_at = None
    if was_approved and owner and owner.role == "student":
        invalidate_user_sessions(owner)
    db.session.commit()
    log_event("admin_unlinked_device", user_id, f"device_id={device_id}; by_admin={current_user.id}")
    flash("تم إيقاف اعتماد الجهاز مع الاحتفاظ بسجله. أي جهاز بديل سيحتاج مراجعة الإدارة.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/courses/create")
@admin_required
def admin_create_course():
    title = request.form.get("title", "").strip()[:180]
    description = request.form.get("description", "").strip()
    if not title:
        flash("اكتب اسم الدورة.", "error")
    else:
        course = Course(title=title, description=description)
        db.session.add(course)
        db.session.commit()
        log_event("admin_created_course", current_user.id, f"course_id={course.id}")
        flash("تم إنشاء الدورة.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/courses/<int:course_id>/delete")
@admin_required
def admin_delete_course(course_id):
    """Remove a course and its lessons so they are no longer visible to students."""
    course = db.session.get(Course, course_id)
    if not course:
        abort(404)

    course_title = course.title
    lesson_count = len(course.lessons)
    db.session.delete(course)
    db.session.commit()
    log_event("admin_deleted_course", current_user.id,
              f"course_id={course_id}; lessons_deleted={lesson_count}; title={course_title}")
    flash(f"تم حذف المادة «{course_title}» وجميع دروسها، ولن تظهر للطلاب.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/lessons/create")
@admin_required
def admin_create_lesson():
    try:
        course_id = int(request.form.get("course_id", ""))
        sort_order = int(request.form.get("sort_order", "1"))
    except ValueError:
        flash("تحقق من الدورة وترتيب الدرس.", "error")
        return redirect(url_for("admin_dashboard"))
    course = db.session.get(Course, course_id)
    title = request.form.get("title", "").strip()[:180]
    video_id = request.form.get("vdocipher_video_id", "").strip()[:128]
    description = request.form.get("description", "").strip()
    if not course or not title or not video_id or not 1 <= sort_order <= 100000:
        flash("املأ اسم الدرس وVideo ID واختر دورة صحيحة.", "error")
    elif not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", video_id):
        flash("Video ID يجب أن يحتوي على أحرف إنجليزية وأرقام وشرطات فقط.", "error")
    else:
        lesson = Lesson(course_id=course_id, title=title, description=description,
                        vdocipher_video_id=video_id, sort_order=sort_order)
        db.session.add(lesson)
        db.session.commit()
        log_event("admin_created_lesson", current_user.id, f"lesson_id={lesson.id}; course_id={course_id}")
        flash("تمت إضافة الدرس. لا يظهر مفتاح VdoCipher في المتصفح.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/lessons/<int:lesson_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_lesson(lesson_id):
    """Edit lesson metadata and its VdoCipher reference from the admin console."""
    lesson = db.session.get(Lesson, lesson_id)
    if not lesson:
        abort(404)

    courses = Course.query.order_by(Course.title.asc()).all()
    if request.method == "POST":
        try:
            course_id = int(request.form.get("course_id", ""))
            sort_order = int(request.form.get("sort_order", "1"))
        except ValueError:
            flash("تحقق من المادة وترتيب الدرس.", "error")
            return redirect(url_for("admin_edit_lesson", lesson_id=lesson_id))

        course = db.session.get(Course, course_id)
        title = request.form.get("title", "").strip()[:180]
        video_id = request.form.get("vdocipher_video_id", "").strip()[:128]
        description = request.form.get("description", "").strip()
        is_published = request.form.get("is_published") == "on"

        if not course or not title or not video_id or not 1 <= sort_order <= 100000:
            flash("املأ عنوان الدرس وVideo ID واختر مادة صحيحة وترتيباً بين 1 و100000.", "error")
        elif not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", video_id):
            flash("Video ID يجب أن يحتوي على أحرف إنجليزية وأرقام وشرطات فقط.", "error")
        else:
            old_course_id = lesson.course_id
            lesson.course_id = course_id
            lesson.title = title
            lesson.description = description
            lesson.vdocipher_video_id = video_id
            lesson.sort_order = sort_order
            lesson.is_published = is_published
            db.session.commit()
            log_event(
                "admin_updated_lesson", current_user.id,
                f"lesson_id={lesson.id}; old_course_id={old_course_id}; course_id={course_id}; published={is_published}",
            )
            flash("تم حفظ تعديلات الدرس.", "success")
            return redirect(url_for("admin_dashboard"))

    return render_template("edit_lesson.html", lesson=lesson, courses=courses)


@app.post("/admin/lessons/<int:lesson_id>/delete")
@admin_required
def admin_delete_lesson(lesson_id):
    lesson = db.session.get(Lesson, lesson_id)
    if not lesson:
        abort(404)
    lesson_title = lesson.title
    db.session.delete(lesson)
    db.session.commit()
    log_event("admin_deleted_lesson", current_user.id, f"lesson_id={lesson_id}; title={lesson_title}")
    flash("تم حذف الدرس من فهرس الموقع. لم يُحذف ملف الفيديو الأصلي من VdoCipher.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/health")
def health():
    result = {"status": "ok", "databases": {}}
    failed = False
    for role, engine in db.engines.items():
        # The default bind key is None; JSON keys must all be strings or Flask's sorted dump raises TypeError.
        role_name = "main" if role is None else str(role)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            result["databases"][role_name] = "ok"
        except Exception:
            result["databases"][role_name] = "error"
            failed = True
    result["status"] = "degraded" if failed else "ok"
    return result, (503 if failed else 200)


@app.errorhandler(CSRFError)
def csrf_failed(_error):
    message = "انتهت صلاحية الصفحة أو النموذج. حدّث الصفحة ثم أعد المحاولة."
    if request.is_json or request.accept_mimetypes.best == "application/json":
        return jsonify({"error": "csrf_failed", "message": message}), 400
    return render_template("error.html", code=400, message=message), 400


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, message="ما عندك صلاحية للوصول لهذه الصفحة."), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="الصفحة المطلوبة غير موجودة."), 404


@app.errorhandler(500)
def internal_error(_error):
    db.session.rollback()
    app.logger.exception("Unhandled server error")
    return render_template("error.html", code=500, message="حدث خطأ داخلي. راجع سجلات الاستضافة للتفاصيل."), 500


def initialize_database():
    with app.app_context():
        db.create_all()
        # Safe, additive migration for existing databases created by earlier builds.
        # Old accounts keep their data and may leave WhatsApp blank until edited manually.
        user_columns = {column["name"] for column in inspect(db.engine).get_columns("users")}
        if "whatsapp_number" not in user_columns:
            try:
                with db.engine.begin() as connection:
                    connection.execute(text("ALTER TABLE users ADD COLUMN whatsapp_number VARCHAR(32)"))
            except Exception:
                # Avoid masking unrelated failures; ignore only a concurrent successful migration.
                refreshed = {column["name"] for column in inspect(db.engine).get_columns("users")}
                if "whatsapp_number" not in refreshed:
                    raise
        device_columns = {column["name"] for column in inspect(db.engine).get_columns("devices")}
        if "last_active_at" not in device_columns:
            # Backward-compatible migration: preserves all existing device records.
            with db.engine.begin() as connection:
                connection.execute(text("ALTER TABLE devices ADD COLUMN last_active_at TIMESTAMP"))
        with db.engine.begin() as connection:
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_devices_last_active_at ON devices (last_active_at)"
            ))
        # Anti-sharing columns (additive; existing rows are kept).
        user_columns = {column["name"] for column in inspect(db.engine).get_columns("users")}
        if "session_hash" not in user_columns:
            with db.engine.begin() as connection:
                connection.execute(text("ALTER TABLE users ADD COLUMN session_hash VARCHAR(64)"))
        user_columns = {column["name"] for column in inspect(db.engine).get_columns("users")}
        if "email_verified" not in user_columns:
            # Legacy compatibility only; the field never gates registration, login, or activation.
            with db.engine.begin() as connection:
                connection.execute(text("ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT TRUE"))
        user_columns = {column["name"] for column in inspect(db.engine).get_columns("users")}
        if "session_version" not in user_columns:
            with db.engine.begin() as connection:
                connection.execute(text("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0"))
        device_columns = {column["name"] for column in inspect(db.engine).get_columns("devices")}
        for column_name, ddl in (
            ("fp_hash", "ALTER TABLE devices ADD COLUMN fp_hash VARCHAR(64)"),
            ("app_installation_hash", "ALTER TABLE devices ADD COLUMN app_installation_hash VARCHAR(64)"),
            ("app_public_key", "ALTER TABLE devices ADD COLUMN app_public_key VARCHAR(2048)"),
            ("app_public_key_hash", "ALTER TABLE devices ADD COLUMN app_public_key_hash VARCHAR(64)"),
            ("relink_count", "ALTER TABLE devices ADD COLUMN relink_count INTEGER NOT NULL DEFAULT 0"),
        ):
            if column_name not in device_columns:
                with db.engine.begin() as connection:
                    connection.execute(text(ddl))
        with db.engine.begin() as connection:
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_devices_fp_hash ON devices (fp_hash)"))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_devices_app_installation_hash "
                "ON devices (user_id, app_installation_hash)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_devices_app_public_key_hash "
                "ON devices (app_public_key_hash)"
            ))
        admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
        admin_password = os.getenv("ADMIN_PASSWORD", "")
        admin_values_look_configured = (
            admin_email and admin_password
            and admin_email != "admin@example.com"
            and "replace-with" not in admin_password.lower()
            and 12 <= len(admin_password) <= 128
        )
        if admin_values_look_configured:
            existing = User.query.filter_by(email=admin_email).first()
            if not existing:
                admin = User(username="Administrator", email=admin_email, role="admin", status="active")
                admin.set_password(admin_password)
                db.session.add(admin)
                try:
                    db.session.commit()
                    app.logger.info("Initial administrator account created")
                except IntegrityError:
                    # Another worker may have seeded the same email at the same time.
                    db.session.rollback()
                    app.logger.info("Administrator seed already created by another worker")
            elif existing.role != "admin":
                app.logger.warning("ADMIN_EMAIL already belongs to a non-admin user; not changing role automatically")


# The starter uses create_all to make first-run testing simple. For a live production
# system, use versioned migrations (e.g. Flask-Migrate) for schema changes.
initialize_database()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
