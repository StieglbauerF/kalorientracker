from datetime import date

from helpers import auth, entry, token_for

TODAY = date.today().isoformat()


def test_fremde_eintraege_sind_nicht_sichtbar(client):
    a = token_for(client, "anna@beispiel.at")
    b = token_for(client, "berta@beispiel.at")
    client.post("/api/entries", headers=auth(a), json=entry())

    day = client.get(f"/api/days/{TODAY}", headers=auth(b)).json()
    assert day["totals"]["kcal"] == 0
    assert day["meals"]["lunch"] == []


def test_fremder_eintrag_kann_nicht_geaendert_oder_geloescht_werden(client):
    a = token_for(client, "anna@beispiel.at")
    b = token_for(client, "berta@beispiel.at")
    entry_id = client.post("/api/entries", headers=auth(a), json=entry()).json()["id"]

    assert client.patch(f"/api/entries/{entry_id}", headers=auth(b), json={"grams": 1}).status_code == 404
    assert client.delete(f"/api/entries/{entry_id}", headers=auth(b)).status_code == 404

    # Der Eintrag ist bei Anna unverändert
    day = client.get(f"/api/days/{TODAY}", headers=auth(a)).json()
    assert day["totals"]["kcal"] == 260


def test_fremde_lebensmittel_sind_nicht_sichtbar_und_nicht_aenderbar(client):
    a = token_for(client, "anna@beispiel.at")
    b = token_for(client, "berta@beispiel.at")
    food = client.post(
        "/api/foods", headers=auth(a), json={"name": "Quark", "kcal": 70, "p": 12, "c": 4, "f": 0.2}
    ).json()

    assert client.get("/api/foods", headers=auth(b)).json() == []
    payload = {"name": "Manipuliert", "kcal": 1, "p": 1, "c": 1, "f": 1}
    assert client.put(f"/api/foods/{food['id']}", headers=auth(b), json=payload).status_code == 404
    assert client.delete(f"/api/foods/{food['id']}", headers=auth(b)).status_code == 404


def test_historie_ist_getrennt(client):
    a = token_for(client, "anna@beispiel.at")
    b = token_for(client, "berta@beispiel.at")
    client.post("/api/entries", headers=auth(a), json=entry())

    assert client.get("/api/history", headers=auth(b)).json() == []
    assert len(client.get("/api/history", headers=auth(a)).json()) == 1
