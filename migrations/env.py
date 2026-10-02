"""Alembic environment: migrations run on the app's own connection (core/db/engine.upgrade),
or from the alembic CLI against data/auto_app.db (for generating new migrations)."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from auto_app.core.db.tables import metadata

config = context.config
target_metadata = metadata


def run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


conn = config.attributes.get("connection")
if conn is not None:
    run(conn)
else:
    engine = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.",
                                poolclass=pool.NullPool)
    with engine.connect() as c:
        run(c)
