from datetime import date

from sqlalchemy.orm import Session

import main

PASSWORD = "passwort-sicher-123"
INVITE = "test-invite-code"


def register(client, email="anna@beispiel.at", password=PASSWORD, privacy=True, invite=INVITE):
    return client.post("/api/auth/register", json={
        "email": email,
        "password": password,
        "invite_code": invite,
        "privacy_accepted": privacy,
    })


def token_for(client, email="anna@beispiel.at"):
    res = register(client, email)
    assert res.status_code == 200, res.text
    return res.json()["token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def entry(**overrides):
    # Standard: 200 g Reis, 100-g-Werte 130 kcal, 2.7 P, 28 C, 0.3 F
    data = {
        "entry_date": date.today().isoformat(),
        "meal_type": "lunch",
        "name": "Reis",
        "grams": 200,
        "kcal": 130,
        "p": 2.7,
        "c": 28,
        "f": 0.3,
        "s": 0,
    }
    data.update(overrides)
    return data


def user_without_consent_token(email="ohne@beispiel.at"):
    # Konto ohne Datenschutz-Zustimmung, so wie es bei bestehenden Konten vor dem Update war
    with Session(main.engine) as db:
        user = main.UserDB(email=email, password_hash=main.hash_password(PASSWORD))
        db.add(user)
        db.commit()
        db.refresh(user)
        return main.issue_session(db, user)["token"]


def count(model):
    with Session(main.engine) as db:
        return db.query(model).count()
