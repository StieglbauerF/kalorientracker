"""Ausgangsstand des Schemas vor Alembic.

Für bestehende Datenbanken (Produktion, Branches) ist diese Revision nur ein Marker.
Sie wird einmalig mit 'alembic stamp head' gesetzt und ändert dort nichts.
Für leere Datenbanken legt sie die Tabellen an, mit dieser festen Definition und
nicht mit den aktuellen Modellen. So bleiben spätere Revisionen korrekt.
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()

users = sa.Table(
    "users", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("email", sa.String, nullable=False, unique=True, index=True),
    sa.Column("password_hash", sa.String, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
    sa.Column("ai_calls_date", sa.Date, nullable=True),
    sa.Column("ai_calls_today", sa.Integer, nullable=False),
    sa.Column("privacy_accepted_at", sa.DateTime, nullable=True),
    sa.Column("privacy_version", sa.String, nullable=True),
)

sessions = sa.Table(
    "sessions", metadata,
    sa.Column("token_hash", sa.String, primary_key=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"),
              nullable=False, index=True),
    sa.Column("expires_at", sa.DateTime, nullable=False, index=True),
)

user_settings = sa.Table(
    "user_settings", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
    sa.Column("goal_mode", sa.String),
    sa.Column("gender", sa.String),
    sa.Column("weight", sa.Float),
    sa.Column("height", sa.Integer),
    sa.Column("age", sa.Integer),
    sa.Column("activity", sa.Float),
    sa.Column("objective", sa.Integer),
    sa.Column("protein_factor", sa.Float),
    sa.Column("target_kcal", sa.Integer),
    sa.Column("target_p", sa.Integer),
    sa.Column("target_c", sa.Integer),
    sa.Column("target_f", sa.Integer),
    sa.Column("target_s", sa.Float),
    sa.Index("ix_user_settings_user_id", "user_id", unique=True),
)

foods = sa.Table(
    "foods", metadata,
    sa.Column("id", sa.Integer, primary_key=True, index=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"),
              nullable=True, index=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("kcal", sa.Float, nullable=False),
    sa.Column("p", sa.Float, nullable=False),
    sa.Column("c", sa.Float, nullable=False),
    sa.Column("f", sa.Float, nullable=False),
    sa.Column("s", sa.Float),
    sa.Column("default_g", sa.Float),
)

meal_entries = sa.Table(
    "meal_entries", metadata,
    sa.Column("id", sa.Integer, primary_key=True, index=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"),
              nullable=True, index=True),
    sa.Column("entry_date", sa.Date, nullable=False, index=True),
    sa.Column("meal_type", sa.String, nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("grams", sa.Float, nullable=False),
    sa.Column("kcal", sa.Float, nullable=False),
    sa.Column("p", sa.Float, nullable=False),
    sa.Column("c", sa.Float, nullable=False),
    sa.Column("f", sa.Float, nullable=False),
    sa.Column("s", sa.Float),
)


def upgrade() -> None:
    bind = op.get_bind()
    vorhanden = set(sa.inspect(bind).get_table_names())
    # Vorhandene Tabellen bleiben unberührt, nur fehlende werden angelegt
    for tabelle in (users, sessions, user_settings, foods, meal_entries):
        if tabelle.name not in vorhanden:
            tabelle.create(bind)


def downgrade() -> None:
    raise NotImplementedError("Der Ausgangsstand kann nicht zurückgerollt werden.")
