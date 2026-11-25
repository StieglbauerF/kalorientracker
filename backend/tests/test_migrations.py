from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

import main

BACKEND = Path(__file__).resolve().parent.parent
ERWARTETE_TABELLEN = {"users", "sessions", "user_settings", "foods", "meal_entries"}


def alembic_config():
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return cfg


def reset_database():
    # Tabellen und die Versionsverwaltung von Alembic komplett entfernen
    main.Base.metadata.drop_all(bind=main.engine)
    with main.engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))


def test_upgrade_auf_leerer_datenbank_legt_alle_tabellen_an():
    reset_database()
    command.upgrade(alembic_config(), "head")
    tabellen = set(inspect(main.engine).get_table_names())
    assert ERWARTETE_TABELLEN <= tabellen


def test_modelle_und_migrationen_stimmen_ueberein():
    reset_database()
    command.upgrade(alembic_config(), "head")
    # Wirft einen Fehler, wenn main.py und die Migrationen auseinanderlaufen
    command.check(alembic_config())
