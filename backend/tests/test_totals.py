from datetime import date

from helpers import auth, entry, token_for

TODAY = date.today().isoformat()


def day(client, token):
    return client.get(f"/api/days/{TODAY}", headers=auth(token)).json()


def test_summe_eines_eintrags(client):
    token = token_for(client)
    client.post("/api/entries", headers=auth(token), json=entry())  # 200 g Reis
    assert day(client, token)["totals"] == {"kcal": 260, "p": 5.4, "c": 56.0, "f": 0.6, "s": 0.0}


def test_summe_mehrerer_mahlzeiten(client):
    token = token_for(client)
    client.post("/api/entries", headers=auth(token), json=entry())  # Mittag: 260 kcal
    client.post("/api/entries", headers=auth(token), json=entry(
        name="Haferflocken", meal_type="breakfast", grams=100, kcal=370, p=13, c=60, f=7,
    ))
    result = day(client, token)
    assert result["totals"] == {"kcal": 630, "p": 18.4, "c": 116.0, "f": 7.6, "s": 0.0}
    assert len(result["meals"]["breakfast"]) == 1
    assert len(result["meals"]["lunch"]) == 1


def test_grammaenderung_wirkt_auf_summe(client):
    token = token_for(client)
    entry_id = client.post("/api/entries", headers=auth(token), json=entry()).json()["id"]
    client.patch(f"/api/entries/{entry_id}", headers=auth(token), json={"grams": 100})
    totals = day(client, token)["totals"]
    assert totals["kcal"] == 130
    assert totals["p"] == 2.7


def test_historie_stimmt_mit_tagessumme_ueberein(client):
    token = token_for(client)
    client.post("/api/entries", headers=auth(token), json=entry())
    client.post("/api/entries", headers=auth(token), json=entry(
        name="Haferflocken", meal_type="breakfast", grams=100, kcal=370, p=13, c=60, f=7,
    ))
    history = client.get("/api/history", headers=auth(token)).json()
    totals = day(client, token)["totals"]
    assert history[0]["date"] == TODAY
    assert history[0]["kcal"] == totals["kcal"]
    assert history[0]["p"] == totals["p"]
    assert history[0]["c"] == totals["c"]
    assert history[0]["f"] == totals["f"]
