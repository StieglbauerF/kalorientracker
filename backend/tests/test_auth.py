import json

from sqlalchemy.orm import Session

import main
from helpers import (
    PASSWORD, auth, count, entry, register, token_for, user_without_consent_token,
)


def test_registrierung_ohne_zustimmung_wird_abgelehnt(client):
    res = register(client, privacy=False)
    assert res.status_code == 400
    assert count(main.UserDB) == 0


def test_falscher_einladungscode_wird_abgelehnt(client):
    res = register(client, invite="falsch")
    assert res.status_code == 403
    assert count(main.UserDB) == 0


def test_registrierung_mit_zustimmung(client):
    token = token_for(client)
    res = client.get("/api/auth/me", headers=auth(token))
    assert res.status_code == 200
    assert res.json()["privacy_accepted"] is True


def test_doppelte_registrierung_wird_abgelehnt(client):
    token_for(client)
    assert register(client).status_code == 409


def test_falsches_passwort(client):
    token_for(client)
    res = client.post("/api/auth/login", json={"email": "anna@beispiel.at", "password": "falsch-falsch"})
    assert res.status_code == 401


def test_richtiges_passwort_liefert_token(client):
    token_for(client)
    res = client.post("/api/auth/login", json={"email": "anna@beispiel.at", "password": PASSWORD})
    assert res.status_code == 200
    assert res.json()["token"]


def test_login_sperre_nach_zehn_fehlversuchen(client):
    token_for(client)
    codes = [
        client.post("/api/auth/login", json={"email": "anna@beispiel.at", "password": "falsch-falsch"}).status_code
        for _ in range(11)
    ]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429


def test_token_wird_nur_als_hash_gespeichert(client):
    token = token_for(client)
    with Session(main.engine) as db:
        stored = [s.token_hash for s in db.query(main.SessionDB).all()]
    assert stored == [main.hash_token(token)]
    assert token not in stored


def test_ohne_anmeldung_ist_alles_gesperrt(client):
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/days/2026-01-01").status_code == 401


def test_logout_macht_token_ungueltig(client):
    token = token_for(client)
    assert client.post("/api/auth/logout", headers=auth(token)).status_code == 200
    assert client.get("/api/settings", headers=auth(token)).status_code == 401


def test_ohne_zustimmung_sind_daten_gesperrt(client):
    token = user_without_consent_token()
    assert client.get("/api/settings", headers=auth(token)).status_code == 403
    assert client.get("/api/auth/me", headers=auth(token)).json()["privacy_accepted"] is False

    assert client.post("/api/auth/accept-privacy", headers=auth(token)).status_code == 200
    assert client.get("/api/settings", headers=auth(token)).status_code == 200


def test_export_enthaelt_daten_ohne_passwort_und_ohne_user_id(client):
    token = token_for(client)
    client.post("/api/foods", headers=auth(token), json={"name": "Quark", "kcal": 70, "p": 12, "c": 4, "f": 0.2})
    client.post("/api/entries", headers=auth(token), json=entry())

    res = client.get("/api/auth/export", headers=auth(token))
    assert res.status_code == 200
    data = res.json()
    assert len(data["lebensmittel"]) == 1
    assert len(data["eintraege"]) == 1
    text = json.dumps(data)
    assert "password_hash" not in text
    assert "user_id" not in text


def test_falsches_passwort_loescht_nichts(client):
    token = token_for(client)
    res = client.request("DELETE", "/api/auth/account", headers=auth(token), json={"password": "falsch-falsch"})
    assert res.status_code == 401
    assert count(main.UserDB) == 1


def test_kontoloeschung_entfernt_alle_daten(client):
    token = token_for(client)
    client.get("/api/settings", headers=auth(token))
    client.post("/api/foods", headers=auth(token), json={"name": "Quark", "kcal": 70, "p": 12, "c": 4, "f": 0.2})
    client.post("/api/entries", headers=auth(token), json=entry())

    res = client.request("DELETE", "/api/auth/account", headers=auth(token), json={"password": PASSWORD})
    assert res.status_code == 200

    assert count(main.UserDB) == 0
    assert count(main.SessionDB) == 0
    assert count(main.UserSettingsDB) == 0
    assert count(main.FoodDB) == 0
    assert count(main.MealEntryDB) == 0
    login = client.post("/api/auth/login", json={"email": "anna@beispiel.at", "password": PASSWORD})
    assert login.status_code == 401
