from datetime import date

from helpers import auth, entry, token_for

TODAY = date.today().isoformat()


def day(client, token):
    return client.get(f"/api/days/{TODAY}", headers=auth(token)).json()


def bier():
    # 5 Bier à 500 ml = 2500 ml, zusammen 1000 kcal
    return entry(name="Bier", category="alcohol", meal_type="dinner",
                 grams=2500, kcal=40, p=0.3, c=3.0, f=0.0)


def limo():
    # 500 ml Cola, 210 kcal
    return entry(name="Cola", category="soft_drink", meal_type="snacks",
                 grams=500, kcal=42, p=0.0, c=10.6, f=0.0)


def test_drei_ansichten_bei_gemischtem_tag(client):
    t = token_for(client)
    for e in (bier(), entry(), limo()):
        client.post("/api/entries", headers=auth(t), json=e)
    modes = day(client, t)["modes"]
    assert modes["alles"]["kcal"] == 1470       # Bier 1000 + Reis 260 + Cola 210
    assert modes["ohne_alkohol"]["kcal"] == 470  # ohne Bier
    assert modes["nur_essen"]["kcal"] == 260     # ohne alle Getränke


def test_totals_ist_die_ansicht_alles(client):
    t = token_for(client)
    client.post("/api/entries", headers=auth(t), json=bier())
    result = day(client, t)
    assert result["totals"] == result["modes"]["alles"]


def test_einmalige_portion_ohne_gewicht(client):
    t = token_for(client)
    client.post("/api/entries", headers=auth(t),
                json=entry(name="Restaurant", grams=100, kcal=850, p=40, c=90, f=30))
    assert day(client, t)["totals"]["kcal"] == 850


def test_einmalige_portion_mit_gewicht(client):
    # 400 g mit insgesamt 850 kcal, umgerechnet auf 100 g
    t = token_for(client)
    client.post("/api/entries", headers=auth(t),
                json=entry(name="Restaurant", grams=400, kcal=212.5, p=10, c=22.5, f=7.5))
    assert day(client, t)["totals"]["kcal"] == 850


def test_stueck_wird_als_gramm_gerechnet(client):
    # 2 Eier à 60 g, 155 kcal pro 100 g
    t = token_for(client)
    res = client.post("/api/entries", headers=auth(t),
                      json=entry(name="Ei", grams=120, kcal=155, p=12.6, c=1.1, f=10.6, pieces=2))
    assert res.json()["pieces"] == 2
    assert day(client, t)["totals"]["kcal"] == 186


def test_lebensmittel_mit_stueckgewicht(client):
    t = token_for(client)
    body = client.post("/api/foods", headers=auth(t),
                       json={"name": "Ei", "kcal": 155, "p": 12.6, "c": 1.1, "f": 10.6,
                             "piece_grams": 60}).json()
    assert body["piece_grams"] == 60
    assert body["category"] == "food"


def test_ungueltige_kategorie_wird_abgelehnt(client):
    t = token_for(client)
    food = {"name": "Wein", "kcal": 80, "p": 0, "c": 3, "f": 0, "category": "wein"}
    assert client.post("/api/foods", headers=auth(t), json=food).status_code == 422
    assert client.post("/api/entries", headers=auth(t), json=entry(category="wein")).status_code == 422


def test_eintrag_ohne_kategorie_ist_essen(client):
    t = token_for(client)
    res = client.post("/api/entries", headers=auth(t), json=entry())
    assert res.json()["category"] == "food"


def test_historie_hat_alle_ansichten(client):
    t = token_for(client)
    client.post("/api/entries", headers=auth(t), json=bier())
    client.post("/api/entries", headers=auth(t), json=entry())
    modes = client.get("/api/history", headers=auth(t)).json()[0]["modes"]
    assert modes["alles"]["kcal"] == 1260
    assert modes["ohne_alkohol"]["kcal"] == 260
    assert modes["nur_essen"]["kcal"] == 260
