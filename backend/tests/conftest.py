"""
Gemeinsame Testeinrichtung.

Die Tests laufen gegen eine temporäre SQLite-Datei und NIE gegen die Datenbank aus DATABASE_URL.
Die Umgebungsvariablen werden gesetzt, bevor main.py importiert wird, weil main.py beim Import
die Datenbank-Verbindung aufbaut.
"""
import os
import tempfile
from pathlib import Path

TEST_DB = Path(tempfile.gettempdir()) / "kalorientracker_test.db"
if TEST_DB.exists():
    TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["INVITE_CODE"] = "test-invite-code"
os.environ.pop("GEMINI_API_KEY", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

assert main.engine.url.drivername.startswith("sqlite"), "Tests dürfen nur auf SQLite laufen"


@pytest.fixture(autouse=True)
def fresh_database():
    # Vor jedem Test eine leere Datenbank und ein leerer Login-Limiter
    main.Base.metadata.drop_all(bind=main.engine)
    main.Base.metadata.create_all(bind=main.engine)
    main._failed_logins.clear()
    yield


@pytest.fixture
def client():
    return TestClient(main.app)
