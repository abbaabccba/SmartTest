"""Explicit migration entry point; never executed by application startup."""
from alembic import context

from smarttest_web.config import ApplicationDatabaseSettings, storage_environment
from smarttest_web.database import WebDatabase
from smarttest_web.schema import application_metadata


target_metadata = application_metadata()

if context.is_offline_mode():
    context.configure(url=ApplicationDatabaseSettings.from_environment(storage_environment()).url,
                      target_metadata=target_metadata, literal_binds=True, dialect_opts={'paramstyle': 'named'})
    with context.begin_transaction():
        context.run_migrations()
else:
    supplied = context.config.attributes.get('connection')
    def run(connection):
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    if supplied is not None:
        run(supplied)
    else:
        database = WebDatabase.from_settings(ApplicationDatabaseSettings.from_environment(storage_environment()))
        try:
            with database.connect() as connection:
                run(connection)
        finally:
            database.close()
