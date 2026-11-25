import main
from helpers import auth, count, token_for

NEUE_ZIELE = {
    "goal_mode": "manual",
    "gender": "female",
    "weight": 80,
    "height": 170,
    "age": 30,
    "activity": 1.55,
    "objective": 0,
    "protein_factor": 2.0,
    "target_kcal": 2200,
    "target_p": 150,
    "target_c": 250,
    "target_f": 70,
    "target_s": 6.0,
}


def test_einstellungen_entstehen_bei_registrierung(client):
    token_for(client)
    assert count(main.UserSettingsDB) == 1


def test_einstellungen_werden_nicht_doppelt_angelegt(client):
    token = token_for(client)
    ids = {client.get("/api/settings", headers=auth(token)).json()["id"] for _ in range(3)}
    assert len(ids) == 1
    assert count(main.UserSettingsDB) == 1


def test_einstellungen_gehoeren_zum_konto(client):
    a = token_for(client, "anna@beispiel.at")
    b = token_for(client, "berta@beispiel.at")
    assert client.put("/api/settings", headers=auth(a), json=NEUE_ZIELE).status_code == 200

    assert client.get("/api/settings", headers=auth(a)).json()["target_kcal"] == 2200
    assert client.get("/api/settings", headers=auth(b)).json()["target_kcal"] == 2500
