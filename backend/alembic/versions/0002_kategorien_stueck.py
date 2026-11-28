"""Kategorien für Lebensmittel und Einträge, Stückangaben.

Bestehende Zeilen bekommen die Kategorie 'food', damit ihre Summen unverändert bleiben.
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_kategorien_stueck"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("foods") as batch:
        batch.add_column(sa.Column("category", sa.String(), nullable=False, server_default="food"))
        batch.add_column(sa.Column("piece_grams", sa.Float(), nullable=True))
    with op.batch_alter_table("meal_entries") as batch:
        batch.add_column(sa.Column("category", sa.String(), nullable=False, server_default="food"))
        batch.add_column(sa.Column("pieces", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("meal_entries") as batch:
        batch.drop_column("pieces")
        batch.drop_column("category")
    with op.batch_alter_table("foods") as batch:
        batch.drop_column("piece_grams")
        batch.drop_column("category")
