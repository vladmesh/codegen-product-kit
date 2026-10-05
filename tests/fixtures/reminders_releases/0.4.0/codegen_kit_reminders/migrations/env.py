"""Alembic environment for package-owned reminder revisions."""

from alembic import context
from sqlalchemy import Connection

config = context.config
connection = config.attributes.get("connection")
schema = config.attributes.get("version_table_schema")
if not isinstance(connection, Connection) or not isinstance(schema, str):
    raise RuntimeError("package migrations require a connection and version-table schema")

context.configure(
    connection=connection,
    version_table="alembic_version",
    version_table_schema=schema,
)
with context.begin_transaction():
    context.run_migrations()
