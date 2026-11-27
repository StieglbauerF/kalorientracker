from helpers import register


def login(client, email, password="falsch-falsch", xff=None):
    headers = {"X-Forwarded-For": xff} if xff else {}
    return client.post("/api/auth/login", json={"email": email, "password": password}, headers=headers)


def test_registrierung_pro_ip_begrenzt(client):
    for i in range(10):
        assert register(client, email=f"user{i}@beispiel.at").status_code == 200
    assert register(client, email="zu-viel@beispiel.at").status_code == 429


def test_login_pro_ip_begrenzt(client):
    # Unterschiedliche E-Mail-Adressen, damit nur das IP-Limit greift
    for i in range(30):
        assert login(client, f"x{i}@beispiel.at").status_code == 401
    assert login(client, "x30@beispiel.at").status_code == 429


def test_forwarded_for_zaehlt_nur_den_rechten_wert(client):
    # Der linke Teil ist vom Client gefälscht und ändert nichts an der Zählung
    for i in range(30):
        assert login(client, f"y{i}@beispiel.at", xff=f"gefaelscht-{i}, 203.0.113.7").status_code == 401
    assert login(client, "y30@beispiel.at", xff="anderer-fake, 203.0.113.7").status_code == 429
    # Eine andere echte Adresse ist davon nicht betroffen
    assert login(client, "y31@beispiel.at", xff="gefaelscht, 198.51.100.9").status_code == 401
