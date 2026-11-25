import os
os.environ["DISABLE_SQLALCHEMY_CEXT"] = "1"
import json
from datetime import date
from typing import Literal, Optional

#test

import httpx
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, func
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ==============================================================================
# 1. DATENBANK-KONFIGURATION (Lokal SQLite, auf Render/Neon PostgreSQL)
# ==============================================================================
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./kalorientracker.db")

# Falls ein Hoster "postgres://" statt "postgresql://" liefert, für SQLAlchemy korrigieren
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


# ==============================================================================
# 2. DATENBANK-TABELLEN (SQLAlchemy Modelle)
# ==============================================================================
class UserSettingsDB(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, default=1)
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
    name = Column(String, nullable=False)
    kcal = Column(Float, nullable=False)      # pro 100g
    p = Column(Float, nullable=False)         # pro 100g
    c = Column(Float, nullable=False)         # pro 100g
    f = Column(Float, nullable=False)         # pro 100g
    s = Column(Float, default=0.0)            # Salz pro 100g
    default_g = Column(Float, default=100.0)


class MealEntryDB(Base):
    __tablename__ = "meal_entries"

    id = Column(Integer, primary_key=True, index=True)
    entry_date = Column(Date, index=True, nullable=False)
    meal_type = Column(String, nullable=False)  # breakfast, lunch, dinner, snacks
    name = Column(String, nullable=False)
    grams = Column(Float, nullable=False)
    kcal = Column(Float, nullable=False)        # pro 100g
    p = Column(Float, nullable=False)           # pro 100g
    c = Column(Float, nullable=False)           # pro 100g
    f = Column(Float, nullable=False)           # pro 100g
    s = Column(Float, default=0.0)              # Salz pro 100g


# Tabellen automatisch erzeugen, falls sie noch nicht existieren
Base.metadata.create_all(bind=engine)


# ==============================================================================
# 3. VALIDIERUNGSSCHEMATA (Pydantic)
# ==============================================================================
class SettingsSchema(BaseModel):
    goal_mode: Literal["auto", "manual"] = "auto"
    gender: Literal["male", "female"] = "male"
    weight: float = Field(gt=20, le=350)
    height: int = Field(gt=100, le=250)
    age: int = Field(gt=10, le=120)
    activity: float = Field(gt=1.0, le=2.5)
    objective: int
    protein_factor: float = Field(gt=0.5, le=4.0)
    target_kcal: int = Field(ge=500)
    target_p: int = Field(ge=10)
    target_c: int = Field(ge=0)
    target_f: int = Field(ge=10)
    target_s: float = Field(ge=0)


class FoodCreate(BaseModel):
    name: str = Field(min_length=1)
    kcal: float = Field(ge=0)
    p: float = Field(ge=0)
    c: float = Field(ge=0)
    f: float = Field(ge=0)
    s: float = Field(default=0.0, ge=0)
    default_g: float = Field(default=100.0, gt=0)


class EntryCreate(BaseModel):
    entry_date: date = Field(default_factory=date.today)
    meal_type: Literal["breakfast", "lunch", "dinner", "snacks"]
    name: str = Field(min_length=1)
    grams: float = Field(gt=0)
    kcal: float = Field(ge=0)
    p: float = Field(ge=0)
    c: float = Field(ge=0)
    f: float = Field(ge=0)
    s: float = Field(default=0.0, ge=0)


class EntryUpdateGrams(BaseModel):
    grams: float = Field(gt=0)


class PhotoAnalyzeRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"
    hint: Optional[str] = None


# ==============================================================================
# 4. FASTAPI APP & SICHERHEIT
# ==============================================================================
app = FastAPI(title="Kalorienrechner API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Kann nach Frontend-Deployment auf deine Domain eingeschränkt werden
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_secret(x_app_secret: Optional[str] = Header(default=None)):
    """Prüft das Passwort im Header X-App-Secret, sofern APP_SECRET gesetzt ist."""
    expected_secret = os.getenv("APP_SECRET")
    if expected_secret and x_app_secret != expected_secret:
        raise HTTPException(status_code=401, detail="Ungültiges oder fehlendes App-Passwort")


def get_or_create_settings(db: Session) -> UserSettingsDB:
    settings = db.query(UserSettingsDB).filter(UserSettingsDB.id == 1).first()
    if not settings:
        settings = UserSettingsDB(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


# ==============================================================================
# 5. API-ENDPUNKTE
# ==============================================================================
@app.get("/api/health")
def health_check():
    """Leichter Endpunkt zum Aufwecken des Servers (Cold Start)."""
    return {"status": "ok"}


@app.get("/api/settings", dependencies=[Depends(verify_secret)])
def read_settings(db: Session = Depends(get_db)):
    return get_or_create_settings(db)


@app.put("/api/settings", dependencies=[Depends(verify_secret)])
def update_settings(payload: SettingsSchema, db: Session = Depends(get_db)):
    settings = get_or_create_settings(db)
    for key, value in payload.model_dump().items():
        setattr(settings, key, value)
    db.commit()
    db.refresh(settings)
    return settings


@app.get("/api/days/{target_date}", dependencies=[Depends(verify_secret)])
def get_day(target_date: date, db: Session = Depends(get_db)):
    entries = (
        db.query(MealEntryDB)
        .filter(MealEntryDB.entry_date == target_date)
        .order_by(MealEntryDB.id.asc())
        .all()
    )
    settings = get_or_create_settings(db)

    grouped_meals = {"breakfast": [], "lunch": [], "dinner": [], "snacks": []}
    totals = {"kcal": 0.0, "p": 0.0, "c": 0.0, "f": 0.0, "s": 0.0}

    for e in entries:
        if e.meal_type in grouped_meals:
            grouped_meals[e.meal_type].append(e)
        factor = e.grams / 100.0
        totals["kcal"] += e.kcal * factor
        totals["p"] += e.p * factor
        totals["c"] += e.c * factor
        totals["f"] += e.f * factor
        totals["s"] += (e.s or 0.0) * factor

    return {
        "date": str(target_date),
        "totals": {
            "kcal": round(totals["kcal"]),
            "p": round(totals["p"], 1),
            "c": round(totals["c"], 1),
            "f": round(totals["f"], 1),
            "s": round(totals["s"], 1),
        },
        "targets": {
            "kcal": settings.target_kcal,
            "p": settings.target_p,
            "c": settings.target_c,
            "f": settings.target_f,
            "s": settings.target_s,
        },
        "meals": grouped_meals,
    }


@app.post("/api/entries", dependencies=[Depends(verify_secret)])
def add_entry(payload: EntryCreate, db: Session = Depends(get_db)):
    entry = MealEntryDB(**payload.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@app.patch("/api/entries/{entry_id}", dependencies=[Depends(verify_secret)])
def update_entry_grams(entry_id: int, payload: EntryUpdateGrams, db: Session = Depends(get_db)):
    entry = db.query(MealEntryDB).filter(MealEntryDB.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    entry.grams = payload.grams
    db.commit()
    db.refresh(entry)
    return entry


@app.delete("/api/entries/{entry_id}", dependencies=[Depends(verify_secret)])
def delete_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(MealEntryDB).filter(MealEntryDB.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    db.delete(entry)
    db.commit()
    return {"deleted": True}


@app.get("/api/history", dependencies=[Depends(verify_secret)])
def get_history(limit: int = 60, db: Session = Depends(get_db)):
    """Aggregiert automatisch alle getrackten Tage aus meal_entries."""
    rows = (
        db.query(
            MealEntryDB.entry_date,
            func.count(MealEntryDB.id).label("meals_count"),
            func.sum(MealEntryDB.kcal * MealEntryDB.grams / 100.0).label("kcal"),
            func.sum(MealEntryDB.p * MealEntryDB.grams / 100.0).label("p"),
            func.sum(MealEntryDB.c * MealEntryDB.grams / 100.0).label("c"),
            func.sum(MealEntryDB.f * MealEntryDB.grams / 100.0).label("f"),
            func.sum(MealEntryDB.s * MealEntryDB.grams / 100.0).label("s"),
        )
        .group_by(MealEntryDB.entry_date)
        .order_by(MealEntryDB.entry_date.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "date": str(r.entry_date),
            "meals_count": r.meals_count,
            "kcal": round(r.kcal or 0),
            "p": round(r.p or 0, 1),
            "c": round(r.c or 0, 1),
            "f": round(r.f or 0, 1),
            "s": round(r.s or 0, 1),
        }
        for r in rows
    ]


@app.get("/api/foods", dependencies=[Depends(verify_secret)])
def list_foods(db: Session = Depends(get_db)):
    return db.query(FoodDB).order_by(FoodDB.name.asc()).all()


@app.post("/api/foods", dependencies=[Depends(verify_secret)])
def create_food(payload: FoodCreate, db: Session = Depends(get_db)):
    food = FoodDB(**payload.model_dump())
    db.add(food)
    db.commit()
    db.refresh(food)
    return food


@app.put("/api/foods/{food_id}", dependencies=[Depends(verify_secret)])
def update_food(food_id: int, payload: FoodCreate, db: Session = Depends(get_db)):
    food = db.query(FoodDB).filter(FoodDB.id == food_id).first()
    if not food:
        raise HTTPException(status_code=404, detail="Lebensmittel nicht gefunden")
    for key, value in payload.model_dump().items():
        setattr(food, key, value)
    db.commit()
    db.refresh(food)
    return food


@app.delete("/api/foods/{food_id}", dependencies=[Depends(verify_secret)])
def delete_food(food_id: int, db: Session = Depends(get_db)):
    food = db.query(FoodDB).filter(FoodDB.id == food_id).first()
    if not food:
        raise HTTPException(status_code=404, detail="Lebensmittel nicht gefunden")
    db.delete(food)
    db.commit()
    return {"deleted": True}


@app.post("/api/ai/analyze-meal", dependencies=[Depends(verify_secret)])
async def analyze_meal(req: PhotoAnalyzeRequest):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY auf dem Server nicht konfiguriert")

    prompt = (
        "Du bist ein präziser Ernährungswissenschaftler. Analysiere dieses Essensfoto (oder die Nährwerttabelle). "
        f"{f'Zusatzhinweis des Nutzers: {req.hint}. ' if req.hint else ''}"
        "Schätze den Namen des Gerichts auf Deutsch, das gesamte Portionsgewicht in Gramm (portionGrams) "
        "sowie die Nährwerte pro 100g (kcal100, p100, c100, f100, s100 für Salz) und eine kurze Aufschlüsselung "
        "der geschätzten Komponenten (note). Antworte AUSSCHLIESSLICH im folgenden JSON-Format: "
        '{"name": "String", "portionGrams": 370, "kcal100": 186, "p100": 10.8, '
        '"c100": 20.3, "f100": 6.4, "s100": 1.2, "note": "Kurze Erklärung"}'
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    body = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": req.mime_type, "data": req.image_base64}},
                ]
            }
        ],
        "generationConfig": {"response_mime_type": "application/json"},
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(url, json=body)
        if res.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Gemini API Fehler: {res.text}")
        data = res.json()
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(raw_text)