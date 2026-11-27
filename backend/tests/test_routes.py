"""
Prüft alle API-Routen automatisch: Jede Route außer den bewusst öffentlichen
verlangt eine Anmeldung. Kommt eine neue Route ohne Prüfung dazu, schlägt dieser
Test fehl, bevor die Änderung in die Produktion gelangt.
"""
import re

import pytest

import main

# Bewusst öffentliche Routen. Neue Ausnahmen nur mit guter Begründung hier eintragen.
OEFFENTLICH = {
    ("GET", "/api/health"),           # Aufwecken des Servers, gibt nur den Status zurück
    ("POST", "/api/auth/register"),   # Neue Konten, geschützt durch Einladungscode und Limit
    ("POST", "/api/auth/login"),      # Anmeldung, geschützt durch Login-Sperren und Limit
    ("POST", "/api/auth/logout"),     # Ohne Token nur ein harmloser No-Op
}

ERSATZWERTE = {"target_date": "2026-01-01"}


def fuelle_pfad(pfad):
    return re.sub(r"{(\w+)}", lambda m: ERSATZWERTE.get(m.group(1), "1"), pfad)


def ist_oeffentlich(methode, pfad):
    # HEAD ist ein Sonderfall von GET und folgt dessen Regel
    if methode == "HEAD":
        methode = "GET"
    return (methode, pfad) in OEFFENTLICH


def api_routen():
    result = []
    for route in main.app.routes:
        if not route.path.startswith("/api/"):
            continue
        for methode in sorted(getattr(route, "methods", set()) or set()):
            if not ist_oeffentlich(methode, route.path):
                result.append((methode, route.path))
    return result


ROUTEN = api_routen()


def test_routenliste_ist_vollstaendig():
    # Schutz gegen einen leeren Test: es müssen tatsächlich Routen geprüft werden
    assert len(ROUTEN) >= 15


def test_oeffentliche_ausnahmen_existieren():
    # Umbenannte oder entfernte Routen dürfen nicht als Ausnahme stehen bleiben
    vorhanden = {
        (methode, route.path)
        for route in main.app.routes
        for methode in (getattr(route, "methods", set()) or set())
        if methode != "HEAD"
    }
    assert OEFFENTLICH <= vorhanden, f"Nicht existierende Ausnahmen: {OEFFENTLICH - vorhanden}"


def test_keine_admin_routen():
    pfade = [route.path for route in main.app.routes]
    assert not [p for p in pfade if "admin" in p.lower()]


@pytest.mark.parametrize("methode,pfad", ROUTEN, ids=lambda x: str(x))
def test_jede_route_verlangt_anmeldung(client, methode, pfad):
    res = client.request(methode, fuelle_pfad(pfad), json={})
    assert res.status_code in (401, 403), (
        f"{methode} {pfad} ist ohne Anmeldung erreichbar (Status {res.status_code})"
    )


@pytest.mark.parametrize("methode,pfad", ROUTEN, ids=lambda x: str(x))
def test_ungueltiges_token_wird_abgelehnt(client, methode, pfad):
    res = client.request(
        methode, fuelle_pfad(pfad), json={},
        headers={"Authorization": "Bearer ungueltig"},
    )
    assert res.status_code in (401, 403), (
        f"{methode} {pfad} akzeptiert ein ungültiges Token (Status {res.status_code})"
    )
