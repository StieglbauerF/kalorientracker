import asyncio
import json

from sqlalchemy.orm import Session

import main
from helpers import auth, token_for

BILD = "A" * 120  # gültiges Base64 in passender Länge für das Schema


class AntwortOk:
    status_code = 200

    def json(self):
        meal = {
            "name": "Reis", "portionGrams": 200, "kcal100": 130,
            "p100": 2.7, "c100": 28, "f100": 0.3, "s100": 0, "note": "",
        }
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(meal)}]}}]}


class AntwortFehler:
    def __init__(self, code):
        self.status_code = code
        self.text = f"code {code}"


def analyse(client, token):
    return client.post("/api/ai/analyze-meal", headers=auth(token),
                       json={"image_base64": BILD, "mime_type": "image/jpeg"})


def ai_count(email="anna@beispiel.at"):
    with Session(main.engine) as db:
        user = db.query(main.UserDB).filter(main.UserDB.email == email).first()
        return user.ai_calls_today or 0


def test_ohne_schluessel_500(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    token = token_for(client)
    assert analyse(client, token).status_code == 500


def test_ueberlastung_verbraucht_kein_tageslimit(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    async def ueberlastet(body, api_key):
        return None, 503

    monkeypatch.setattr(main, "request_gemini", ueberlastet)
    token = token_for(client)
    assert analyse(client, token).status_code == 503
    assert ai_count() == 0


def test_erfolgreiche_analyse_zaehlt(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    async def ok(body, api_key):
        return AntwortOk(), 200

    monkeypatch.setattr(main, "request_gemini", ok)
    token = token_for(client)
    res = analyse(client, token)
    assert res.status_code == 200
    assert res.json()["name"] == "Reis"
    assert ai_count() == 1


def test_tageslimit_blockiert_weitere_aufrufe(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(main, "AI_DAILY_LIMIT", 1)

    async def ok(body, api_key):
        return AntwortOk(), 200

    monkeypatch.setattr(main, "request_gemini", ok)
    token = token_for(client)
    assert analyse(client, token).status_code == 200

    async def nicht_aufrufen(body, api_key):
        raise AssertionError("Gemini darf nach dem Limit nicht mehr aufgerufen werden")

    monkeypatch.setattr(main, "request_gemini", nicht_aufrufen)
    assert analyse(client, token).status_code == 429


def fake_httpx(monkeypatch, skript):
    # skript: Modellname -> Liste der Statuscodes, die nacheinander zurückkommen
    aufrufe = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            modell = url.split("/models/")[1].split(":")[0]
            aufrufe.append(modell)
            return AntwortFehler(skript[modell].pop(0))

    async def kein_warten(sekunden):
        return None

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(main.asyncio, "sleep", kein_warten)
    return aufrufe


def test_wiederholung_und_ersatzmodell(monkeypatch):
    skript = {main.GEMINI_MODEL: [503, 503], main.GEMINI_FALLBACK_MODEL: [200]}
    aufrufe = fake_httpx(monkeypatch, skript)
    res, status = asyncio.run(main.request_gemini({}, "key"))
    assert status == 200
    assert aufrufe == [main.GEMINI_MODEL, main.GEMINI_MODEL, main.GEMINI_FALLBACK_MODEL]


def test_nicht_verfuegbares_modell_wechselt_sofort(monkeypatch):
    skript = {main.GEMINI_MODEL: [404], main.GEMINI_FALLBACK_MODEL: [200]}
    aufrufe = fake_httpx(monkeypatch, skript)
    res, status = asyncio.run(main.request_gemini({}, "key"))
    assert status == 200
    assert aufrufe == [main.GEMINI_MODEL, main.GEMINI_FALLBACK_MODEL]


def test_alles_ueberlastet_gibt_503_zurueck(monkeypatch):
    skript = {main.GEMINI_MODEL: [503, 503], main.GEMINI_FALLBACK_MODEL: [503, 503]}
    aufrufe = fake_httpx(monkeypatch, skript)
    res, status = asyncio.run(main.request_gemini({}, "key"))
    assert res is None
    assert status == 503
    assert len(aufrufe) == 4
