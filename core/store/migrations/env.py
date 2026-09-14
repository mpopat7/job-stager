"""Alembic environment.

`init_db` runs migrations inside a connection it already holds and passes it in through
`config.attributes`. Run from the command line there is no such connection, so this opens
one against the same URL the app would use.
"""

from alembic import context

from core.store.db import get_engine, metadata


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        # SQLite cannot ALTER most column changes in place; batch mode rebuilds the table.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:
    with get_engine().begin() as conn:
        _run(conn)
