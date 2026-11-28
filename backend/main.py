import os
os.environ["DISABLE_SQLALCHEMY_CEXT"] = "1"
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import (
    Column, Date, DateTime, Float, ForeignKey, Index, Integer, String,
    create_engine, func,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, declarative_base, sessionmaker

logger = logging.getLogger("kalorientracker")

# ==============================================================================
# 0. KONFIGURATION (Umgebungsvariablen auf Render)
# ==============================================================================
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./kalorientracker.db")
# psycopg (Version 3) ist in requirements.txt. SQLAlchemy braucht dafür den Treiber-Zusatz,
# sonst sucht es psycopg2, das auf dem Server nicht installiert ist.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# Komma-getrennte Liste der erlaubten Frontend-Adressen, z. B. "https://kalorien.example.com"
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

# Registrierung funktioniert nur mit diesem Einladungscode. Leer = Registrierung aus.
INVITE_CODE = os.getenv("INVITE_CODE", "").strip()

# Maximale KI-Fotoanalysen pro Nutzer und Tag (Kostenschutz)
AI_DAILY_LIMIT = int(os.getenv("AI_DAILY_LIMIT", "20"))

SESSION_TTL_DAYS = 30
PRIVACY_VERSION = "2026-11-25"  # bei Änderung des Hinweises erhöhen, dann stimmen alle neu zu
LOGIN_MAX_FAILS = 10
LOGIN_WINDOW_SECONDS = 15 * 60
# Modell über Umgebungsvariable steuerbar, damit ein Modellwechsel keine Code-Änderung braucht
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.7-flash")

if not ALLOWED_ORIGINS:
    logger.warning("ALLOWED_ORIGINS ist leer: Browser-Zugriffe vom Frontend werden blockiert.")
if not INVITE_CODE:
    logger.info("INVITE_CODE ist leer: Registrierung ist deaktiviert.")


# ==============================================================================
# 1. DATENBANK-KONFIGURATION (Lokal SQLite, auf Render/Neon PostgreSQL)
# ==============================================================================
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def utc_now() -> datetime:
    """Naive UTC-Zeit. Damit verhalten sich SQLite und Postgres gleich."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ==============================================================================
# 2. DATENBANK-TABELLEN (SQLAlchemy Modelle)
# ==============================================================================
class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    ai_calls_date = Column(Date, nullable=True)
    ai_calls_today = Column(Integer, default=0, nullable=False)
    privacy_accepted_at = Column(DateTime, nullable=True)
    privacy_version = Column(String, nullable=True)


class SessionDB(Base):
    """Sitzungstokens. Gespeichert wird nur der SHA-256-Hash, nie das Token selbst."""
    __tablename__ = "sessions"

    token_hash = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)


class UserSettingsDB(Base):
    __tablename__ = "user_settings"
    __table_args__ = (Index("ix_user_settings_user_id", "user_id", unique=True),)

    id = Column(Integer, primary_key=True)
    # nullable nur für Altdaten vor der Migration; Abfragen filtern immer nach user_id
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    goal_mode = Column(String, default="auto")
    gender = Column(String, default="male")
    weight = Column(Float, default=80.0)
    height = Column(Integer, default=180)
    age = Column(Integer, default=23)
    activity = Column(Float, default=1.55)
    objective = Column(Integer, default=0)
    protein_factor = Column(Float, default=2.0)
    target_kcal = Column(Integer, default=2500)
    target_p = Column(Integer, default=160)
    target_c = Column(Integer, default=280)
    target_f = Column(Integer, default=75)
    target_s = Column(Float, default=6.0)


class FoodDB(Base):
    __tablename__ = "foods"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True)
    name = Column(String, nullable=False)
    kcal = Column(Float, nullable=False)      # pro 100g
    p = Column(Float, nullable=False)         # pro 100g
    c = Column(Float, nullable=False)         # pro 100g
    f = Column(Float, nullable=False)         # pro 100g
    s = Column(Float, default=0.0)            # Salz pro 100g
    default_g = Column(Float, default=100.0)
    category = Column(String, nullable=False, default="food", server_default="food")
    piece_grams = Column(Float, nullable=True)


class MealEntryDB(Base):
    __tablename__ = "meal_entries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True)
    entry_date = Column(Date, index=True, nullable=False)
    meal_type = Column(String, nullable=False)  # breakfast, lunch, dinner, snacks, drinks
    category = Column(String, nullable=False, default="food", server_default="food")
    pieces = Column(Float, nullable=True)
    name = Column(String, nullable=False)
    grams = Column(Float, nullable=False)
    kcal = Column(Float, nullable=False)        # pro 100g
    p = Column(Float, nullable=False)           # pro 100g
    c = Column(Float, nullable=False)           # pro 100g
    f = Column(Float, nullable=False)           # pro 100g
    s = Column(Float, default=0.0)              # Salz pro 100g


# Das Schema wird ausschließlich über Alembic verwaltet (alembic upgrade head), nicht hier.


# ==============================================================================
# 3. VALIDIERUNGSSCHEMATA (Pydantic)
# ==============================================================================
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
ImageMime = Literal["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"]


class RegisterSchema(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=10, max_length=128)
    invite_code: str = Field(min_length=1, max_length=200)
    privacy_accepted: bool = False


class LoginSchema(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class DeleteAccountSchema(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    token: str
    email: str


class SettingsSchema(BaseModel):
    goal_mode: Literal["auto", "manual"] = "auto"
    gender: Literal["male", "female"] = "male"
    weight: float = Field(gt=20, le=350)
    height: int = Field(gt=100, le=250)
    age: int = Field(gt=10, le=120)
    activity: float = Field(gt=1.0, le=2.5)
    objective: int
    protein_factor: float = Field(gt=0.5, le=4.0)
    target_kcal: int = Field(ge=500, le=10000)
    target_p: int = Field(ge=10, le=1000)
    target_c: int = Field(ge=0, le=1500)
    target_f: int = Field(ge=10, le=600)
    target_s: float = Field(ge=0, le=100)


class FoodCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kcal: float = Field(ge=0, le=900)      # pro 100g, Fett hat maximal 900 kcal/100g
    p: float = Field(ge=0, le=100)
    c: float = Field(ge=0, le=100)
    f: float = Field(ge=0, le=100)
    s: float = Field(default=0.0, ge=0, le=100)
    default_g: float = Field(default=100.0, gt=0, le=5000)
    category: Literal["food", "alcohol", "soft_drink"] = "food"
    piece_grams: Optional[float] = Field(default=None, gt=0, le=5000)


class EntryCreate(BaseModel):
    entry_date: date = Field(default_factory=date.today)
    meal_type: Literal["breakfast", "lunch", "dinner", "snacks", "drinks"]
    name: str = Field(min_length=1, max_length=200)
    grams: float = Field(gt=0, le=5000)
    kcal: float = Field(ge=0, le=900)
    p: float = Field(ge=0, le=100)
    c: float = Field(ge=0, le=100)
    f: float = Field(ge=0, le=100)
    s: float = Field(default=0.0, ge=0, le=100)
    category: Literal["food", "alcohol", "soft_drink"] = "food"
    pieces: Optional[float] = Field(default=None, gt=0, le=1000)


class EntryUpdateGrams(BaseModel):
    grams: float = Field(gt=0, le=5000)


class PhotoAnalyzeRequest(BaseModel):
    image_base64: str = Field(min_length=100, max_length=7_000_000)  # ca. 5 MB Bilddaten
    mime_type: ImageMime = "image/jpeg"
    hint: Optional[str] = Field(default=None, max_length=300)

    @field_validator("image_base64")
    @classmethod
    def must_be_base64(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except (ValueError, TypeError):
            raise ValueError("image_base64 ist kein gültiges Base64")
        return value


class MealAnalysis(BaseModel):
    """Form der Gemini-Antwort. Alles, was nicht passt, wird abgelehnt."""
    name: str = Field(max_length=200)
    portionGrams: float = Field(gt=0, le=5000)
    kcal100: float = Field(ge=0, le=900)
    p100: float = Field(ge=0, le=100)
    c100: float = Field(ge=0, le=100)
    f100: float = Field(ge=0, le=100)
    s100: float = Field(ge=0, le=100)
    note: str = Field(default="", max_length=1000)


MEAL_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "name": {"type": "STRING"},
        "portionGrams": {"type": "NUMBER"},
        "kcal100": {"type": "NUMBER"},
        "p100": {"type": "NUMBER"},
        "c100": {"type": "NUMBER"},
        "f100": {"type": "NUMBER"},
        "s100": {"type": "NUMBER"},
        "note": {"type": "STRING"},
    },
    "required": ["name", "portionGrams", "kcal100", "p100", "c100", "f100", "s100", "note"],
}


# ==============================================================================
# 4. PASSWÖRTER, SITZUNGEN, LOGIN-SCHUTZ
# ==============================================================================
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p),
            dklen=len(digest_hex) // 2,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


# Wird geprüft, wenn die E-Mail unbekannt ist, damit die Antwortzeit nicht verrät, ob ein Konto existiert
DUMMY_HASH = hash_password("kein-konto-vorhanden")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return authorization[len("Bearer "):].strip() or None


# Einfacher In-Memory-Limiter pro E-Mail. Greift nur innerhalb eines Prozesses,
# bei mehreren Workern bräuchte man Redis o. Ä.
_failed_logins: dict[str, deque] = defaultdict(deque)
_login_lock = threading.Lock()


def check_login_allowed(email: str) -> None:
    now = time.monotonic()
    with _login_lock:
        attempts = _failed_logins[email]
        while attempts and now - attempts[0] > LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= LOGIN_MAX_FAILS:
            raise HTTPException(status_code=429, detail="Zu viele Fehlversuche. Bitte warte 15 Minuten.")


def record_failed_login(email: str) -> None:
    with _login_lock:
        _failed_logins[email].append(time.monotonic())


def clear_failed_logins(email: str) -> None:
    with _login_lock:
        _failed_logins.pop(email, None)


class RateLimiter:
    # Zählt Anfragen je Schlüssel (z. B. IP-Adresse) im gleitenden Zeitfenster.
    # Blockierte Versuche zählen nicht mit, daher entspannt sich die Sperre von selbst.
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, message: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window_seconds:
                hits.popleft()
            if len(hits) >= self.limit:
                raise HTTPException(status_code=429, detail=message)
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


RATE_LOGIN_IP = RateLimiter(limit=30, window_seconds=15 * 60)
RATE_REGISTER_IP = RateLimiter(limit=10, window_seconds=60 * 60)


def client_ip(request: Request) -> str:
    # Hinter dem Render-Proxy steht die echte Adresse ganz rechts in X-Forwarded-For.
    # Den linken Teil kann der Client selbst setzen, deshalb wird er nicht verwendet.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unbekannt"


def issue_session(db: Session, user: UserDB) -> dict:
    token = new_token()
    db.add(SessionDB(
        token_hash=hash_token(token),
        user_id=user.id,
        expires_at=utc_now() + timedelta(days=SESSION_TTL_DAYS),
    ))
    db.commit()
    return {"token": token, "email": user.email}


# Tageslimit: erst prüfen, gezählt wird nach einer erfolgreichen Antwort von Gemini.
def check_ai_quota(user: UserDB) -> None:
    today = utc_now().date()
    used = user.ai_calls_today if user.ai_calls_date == today else 0
    if used >= AI_DAILY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Tageslimit für Fotoanalysen ({AI_DAILY_LIMIT}) erreicht. Morgen geht es weiter.",
        )


def count_ai_call(db: Session, user: UserDB) -> None:
    today = utc_now().date()
    if user.ai_calls_date != today:
        user.ai_calls_date = today
        user.ai_calls_today = 0
    user.ai_calls_today += 1
    db.commit()


# ==============================================================================
# 5. FASTAPI APP, DEPENDENCIES
# ==============================================================================
app = FastAPI(title="Kalorienrechner API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,  # Auth läuft über den Authorization-Header, keine Cookies
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session_user(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> UserDB:
    # Prüft nur die Anmeldung, nicht die Datenschutz-Zustimmung
    token = extract_bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    session = (
        db.query(SessionDB)
        .filter(SessionDB.token_hash == hash_token(token), SessionDB.expires_at > utc_now())
        .first()
    )
    if not session:
        raise HTTPException(status_code=401, detail="Sitzung abgelaufen oder ungültig")
    user = db.get(UserDB, session.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    return user


def has_privacy_consent(user: UserDB) -> bool:
    return user.privacy_accepted_at is not None and user.privacy_version == PRIVACY_VERSION


def get_current_user(user: UserDB = Depends(get_session_user)) -> UserDB:
    # Für alle Datenendpunkte: Anmeldung UND aktuelle Datenschutz-Zustimmung nötig
    if not has_privacy_consent(user):
        raise HTTPException(status_code=403, detail="Datenschutz-Zustimmung fehlt")
    return user


def get_or_create_settings(db: Session, user: UserDB) -> UserSettingsDB:
    """
    Legt die Einstellungen beim ersten Zugriff an. Das Frontend lädt Einstellungen und Tage
    parallel, deshalb können zwei Anfragen gleichzeitig versuchen, den Datensatz anzulegen.
    Die Unique-Constraint auf user_id verhindert den Doppelten. Die zweite Anfrage
    liest dann den bereits vorhandenen Datensatz.
    """
    settings = db.query(UserSettingsDB).filter(UserSettingsDB.user_id == user.id).first()
    if settings:
        return settings
    try:
        settings = UserSettingsDB(user_id=user.id)
        db.add(settings)
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.query(UserSettingsDB).filter(UserSettingsDB.user_id == user.id).first()
    db.refresh(settings)
    return settings


# ==============================================================================
# 6. API-ENDPUNKTE
# ==============================================================================
@app.get("/api/health")
def health_check():
    """Leichter Endpunkt zum Aufwecken des Servers (Cold Start)."""
    return {"status": "ok"}


# --- Konten & Anmeldung -------------------------------------------------------
@app.post("/api/auth/register", response_model=TokenResponse)
def register(payload: RegisterSchema, request: Request, db: Session = Depends(get_db)):
    RATE_REGISTER_IP.hit(client_ip(request), "Zu viele Registrierungen von dieser Verbindung. Bitte später erneut versuchen.")
    if not INVITE_CODE:
        raise HTTPException(status_code=403, detail="Registrierung ist deaktiviert")
    if not hmac.compare_digest(payload.invite_code.encode("utf-8"), INVITE_CODE.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Einladungscode ungültig")

    email = payload.email.strip().lower()
    if db.query(UserDB).filter(UserDB.email == email).first():
        raise HTTPException(status_code=409, detail="Diese E-Mail ist bereits registriert")

    if not payload.privacy_accepted:
        raise HTTPException(status_code=400, detail="Bitte stimme dem Datenschutzhinweis zu")
    user = UserDB(
        email=email,
        password_hash=hash_password(payload.password),
        privacy_accepted_at=utc_now(),
        privacy_version=PRIVACY_VERSION,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Diese E-Mail ist bereits registriert")
    db.refresh(user)
    get_or_create_settings(db, user)  # Einstellungen gleich anlegen, damit das erste Laden keinen Wettlauf auslöst
    return issue_session(db, user)


@app.post("/api/auth/login", response_model=TokenResponse)
def login(payload: LoginSchema, request: Request, db: Session = Depends(get_db)):
    RATE_LOGIN_IP.hit(client_ip(request), "Zu viele Anmeldeversuche von dieser Verbindung. Bitte warte 15 Minuten.")
    email = payload.email.strip().lower()
    check_login_allowed(email)

    user = db.query(UserDB).filter(UserDB.email == email).first()
    password_ok = verify_password(payload.password, user.password_hash if user else DUMMY_HASH)
    if not user or not password_ok:
        record_failed_login(email)
        raise HTTPException(status_code=401, detail="E-Mail oder Passwort falsch")

    clear_failed_logins(email)
    db.query(SessionDB).filter(SessionDB.expires_at <= utc_now()).delete(synchronize_session=False)
    db.commit()
    return issue_session(db, user)


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    token = extract_bearer(authorization)
    if token:
        db.query(SessionDB).filter(SessionDB.token_hash == hash_token(token)).delete(synchronize_session=False)
        db.commit()
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: UserDB = Depends(get_session_user)):
    return {
        "email": user.email,
        "privacy_accepted": has_privacy_consent(user),
        "privacy_version": PRIVACY_VERSION,
    }


@app.post("/api/auth/accept-privacy")
def accept_privacy(user: UserDB = Depends(get_session_user), db: Session = Depends(get_db)):
    user.privacy_accepted_at = utc_now()
    user.privacy_version = PRIVACY_VERSION
    db.commit()
    return {"ok": True}


def rows_to_dicts(rows, exclude=()):
    return [
        {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name not in exclude}
        for row in rows
    ]


@app.get("/api/auth/export")
def export_data(user: UserDB = Depends(get_session_user), db: Session = Depends(get_db)):
    # Auskunft und Datenübertragbarkeit: alle eigenen Daten als JSON
    return {
        "konto": {
            "email": user.email,
            "erstellt_am": user.created_at,
            "datenschutz_zustimmung_am": user.privacy_accepted_at,
        },
        "einstellungen": rows_to_dicts(
            db.query(UserSettingsDB).filter(UserSettingsDB.user_id == user.id).all(), exclude=("user_id",)
        ),
        "lebensmittel": rows_to_dicts(
            db.query(FoodDB).filter(FoodDB.user_id == user.id).all(), exclude=("user_id",)
        ),
        "eintraege": rows_to_dicts(
            db.query(MealEntryDB).filter(MealEntryDB.user_id == user.id).all(), exclude=("user_id",)
        ),
    }


@app.delete("/api/auth/account")
def delete_account(
    payload: DeleteAccountSchema,
    user: UserDB = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    check_login_allowed(user.email)  # gleicher Schutz wie beim Login gegen Passwort-Raten
    if not verify_password(payload.password, user.password_hash):
        record_failed_login(user.email)
        raise HTTPException(status_code=401, detail="Passwort falsch")

    # Alle Daten ausdrücklich löschen, unabhängig davon, ob die Datenbank Fremdschlüssel erzwingt
    db.query(MealEntryDB).filter(MealEntryDB.user_id == user.id).delete(synchronize_session=False)
    db.query(FoodDB).filter(FoodDB.user_id == user.id).delete(synchronize_session=False)
    db.query(UserSettingsDB).filter(UserSettingsDB.user_id == user.id).delete(synchronize_session=False)
    db.query(SessionDB).filter(SessionDB.user_id == user.id).delete(synchronize_session=False)
    db.delete(user)
    db.commit()
    clear_failed_logins(user.email)
    return {"deleted": True}


# --- Einstellungen ------------------------------------------------------------
@app.get("/api/settings")
def read_settings(user: UserDB = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_or_create_settings(db, user)


@app.put("/api/settings")
def update_settings(
    payload: SettingsSchema,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settings = get_or_create_settings(db, user)
    for key, value in payload.model_dump().items():
        setattr(settings, key, value)
    db.commit()
    db.refresh(settings)
    return settings


# --- Tage, Einträge, Historie -------------------------------------------------
# Summen für die drei Ansichten. Getränke werden nur über ihre Kategorie ausgeschlossen.
ANSICHTEN = {
    "alles": lambda kategorie: True,
    "ohne_alkohol": lambda kategorie: kategorie != "alcohol",
    "nur_essen": lambda kategorie: kategorie == "food",
}


def berechne_summen(eintraege) -> dict:
    ergebnis = {}
    for name, passt in ANSICHTEN.items():
        summe = {"kcal": 0.0, "p": 0.0, "c": 0.0, "f": 0.0, "s": 0.0}
        for e in eintraege:
            if not passt(e.category or "food"):
                continue
            faktor = e.grams / 100.0
            summe["kcal"] += e.kcal * faktor
            summe["p"] += e.p * faktor
            summe["c"] += e.c * faktor
            summe["f"] += e.f * faktor
            summe["s"] += (e.s or 0.0) * faktor
        ergebnis[name] = {
            "kcal": round(summe["kcal"]),
            "p": round(summe["p"], 1),
            "c": round(summe["c"], 1),
            "f": round(summe["f"], 1),
            "s": round(summe["s"], 1),
        }
    return ergebnis


@app.get("/api/days/{target_date}")
def get_day(
    target_date: date,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entries = (
        db.query(MealEntryDB)
        .filter(MealEntryDB.user_id == user.id, MealEntryDB.entry_date == target_date)
        .order_by(MealEntryDB.id.asc())
        .all()
    )
    settings = get_or_create_settings(db, user)

    grouped_meals = {"breakfast": [], "lunch": [], "dinner": [], "snacks": [], "drinks": []}
    for e in entries:
        if e.meal_type in grouped_meals:
            grouped_meals[e.meal_type].append(e)

    modes = berechne_summen(entries)
    return {
        "date": str(target_date),
        "totals": modes["alles"],  # bleibt für bestehende Aufrufer erhalten
        "modes": modes,
        "targets": {
            "kcal": settings.target_kcal,
            "p": settings.target_p,
            "c": settings.target_c,
            "f": settings.target_f,
            "s": settings.target_s,
        },
        "meals": grouped_meals,
    }


@app.post("/api/entries")
def add_entry(
    payload: EntryCreate,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = MealEntryDB(**payload.model_dump(), user_id=user.id)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@app.patch("/api/entries/{entry_id}")
def update_entry_grams(
    entry_id: int,
    payload: EntryUpdateGrams,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = (
        db.query(MealEntryDB)
        .filter(MealEntryDB.id == entry_id, MealEntryDB.user_id == user.id)
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    entry.grams = payload.grams
    db.commit()
    db.refresh(entry)
    return entry


@app.delete("/api/entries/{entry_id}")
def delete_entry(
    entry_id: int,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = (
        db.query(MealEntryDB)
        .filter(MealEntryDB.id == entry_id, MealEntryDB.user_id == user.id)
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    db.delete(entry)
    db.commit()
    return {"deleted": True}


@app.get("/api/history")
def get_history(
    limit: int = 60,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aggregiert alle getrackten Tage in allen drei Ansichten."""
    limit = max(1, min(limit, 365))
    tage = [
        zeile[0]
        for zeile in db.query(MealEntryDB.entry_date)
        .filter(MealEntryDB.user_id == user.id)
        .distinct()
        .order_by(MealEntryDB.entry_date.desc())
        .limit(limit)
        .all()
    ]
    if not tage:
        return []

    eintraege = (
        db.query(MealEntryDB)
        .filter(MealEntryDB.user_id == user.id, MealEntryDB.entry_date.in_(tage))
        .all()
    )
    nach_tag: dict = {}
    for e in eintraege:
        nach_tag.setdefault(e.entry_date, []).append(e)

    ergebnis = []
    for tag in tage:
        liste = nach_tag.get(tag, [])
        modes = berechne_summen(liste)
        alles = modes["alles"]
        ergebnis.append({
            "date": str(tag),
            "meals_count": len(liste),
            "kcal": alles["kcal"],
            "p": alles["p"],
            "c": alles["c"],
            "f": alles["f"],
            "s": alles["s"],
            "modes": modes,
        })
    return ergebnis


# --- Lebensmittel-Datenbank ---------------------------------------------------
@app.get("/api/foods")
def list_foods(user: UserDB = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(FoodDB)
        .filter(FoodDB.user_id == user.id)
        .order_by(FoodDB.name.asc())
        .all()
    )


@app.post("/api/foods")
def create_food(
    payload: FoodCreate,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    food = FoodDB(**payload.model_dump(), user_id=user.id)
    db.add(food)
    db.commit()
    db.refresh(food)
    return food


@app.put("/api/foods/{food_id}")
def update_food(
    food_id: int,
    payload: FoodCreate,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    food = db.query(FoodDB).filter(FoodDB.id == food_id, FoodDB.user_id == user.id).first()
    if not food:
        raise HTTPException(status_code=404, detail="Lebensmittel nicht gefunden")
    for key, value in payload.model_dump().items():
        setattr(food, key, value)
    db.commit()
    db.refresh(food)
    return food


@app.delete("/api/foods/{food_id}")
def delete_food(
    food_id: int,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    food = db.query(FoodDB).filter(FoodDB.id == food_id, FoodDB.user_id == user.id).first()
    if not food:
        raise HTTPException(status_code=404, detail="Lebensmittel nicht gefunden")
    db.delete(food)
    db.commit()
    return {"deleted": True}


# --- KI-Fotoanalyse -----------------------------------------------------------
# Bei Überlastung (429, 500, 503) wird je Modell einmal wiederholt, danach das Ersatzmodell probiert.
RETRYABLE_STATUS = {429, 500, 503}


def gemini_url(model: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


async def request_gemini(body: dict, api_key: str):
    # Gibt (antwort, letzter_status) zurück. Die Antwort ist None, wenn alle Versuche scheitern.
    models = [GEMINI_MODEL]
    if GEMINI_FALLBACK_MODEL and GEMINI_FALLBACK_MODEL != GEMINI_MODEL:
        models.append(GEMINI_FALLBACK_MODEL)
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    last_status = None

    async with httpx.AsyncClient(timeout=20.0) as client:
        for model in models:
            for attempt in range(2):
                try:
                    res = await client.post(gemini_url(model), json=body, headers=headers)
                except httpx.HTTPError:
                    logger.exception("Gemini (%s) nicht erreichbar", model)
                    break  # zum nächsten Modell
                if res.status_code == 200:
                    return res, 200
                last_status = res.status_code
                logger.error("Gemini (%s) HTTP %s: %s", model, res.status_code, res.text[:300])
                if res.status_code not in RETRYABLE_STATUS:
                    break  # z. B. Modell nicht verfügbar: Wiederholen bringt nichts
                if attempt == 0:
                    await asyncio.sleep(2)
    return None, last_status


@app.post("/api/ai/analyze-meal")
async def analyze_meal(
    req: PhotoAnalyzeRequest,
    user: UserDB = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="KI-Analyse ist auf dem Server nicht konfiguriert")

    check_ai_quota(user)

    prompt = (
        "Du bist ein präziser Ernährungswissenschaftler. Analysiere dieses Essensfoto (oder die Nährwerttabelle). "
        f"{f'Zusatzhinweis des Nutzers: {req.hint}. ' if req.hint else ''}"
        "Schätze den Namen des Gerichts auf Deutsch, das gesamte Portionsgewicht in Gramm (portionGrams) "
        "sowie die Nährwerte pro 100g (kcal100, p100, c100, f100, s100 für Salz) und eine kurze Aufschlüsselung "
        "der geschätzten Komponenten (note). Antworte AUSSCHLIESSLICH im vorgegebenen JSON-Format."
    )

    body = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": req.mime_type, "data": req.image_base64}},
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": MEAL_RESPONSE_SCHEMA,
        },
    }

    res, last_status = await request_gemini(body, api_key)
    if res is None:
        if last_status in RETRYABLE_STATUS:
            raise HTTPException(
                status_code=503,
                detail="Der KI-Dienst ist gerade stark ausgelastet. Bitte in einigen Minuten erneut versuchen.",
            )
        raise HTTPException(status_code=502, detail="KI-Analyse fehlgeschlagen")

    count_ai_call(db, user)  # erst jetzt zählen: fehlgeschlagene Versuche kosten kein Tageslimit

    try:
        raw_text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
        analysis = MealAnalysis.model_validate(json.loads(raw_text))
    except (KeyError, IndexError, TypeError, ValueError, ValidationError):
        logger.exception("Gemini-Antwort hat nicht das erwartete Format")
        raise HTTPException(status_code=502, detail="KI-Antwort konnte nicht gelesen werden")

    return analysis.model_dump()
