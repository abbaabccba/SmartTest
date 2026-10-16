from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import asyncio
from threading import get_ident

from mysql.connector.pooling import MySQLConnectionPool
from sqlalchemy import MetaData, create_engine
from sqlalchemy.dialects.mysql import insert as mysql_insert

from .config import ApplicationDatabaseSettings, DatabaseSettings
from .queries import ensure_readonly_sql

_TRANSACTIONS = ContextVar("web-database-transactions", default={})
APPLICATION_METADATA = MetaData()
_DEFAULT_ISOLATION_LEVEL = 'REPEATABLE READ'


def _execution_owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return get_ident(), task

class WebDatabase:
    @classmethod
    def from_settings(cls, settings: ApplicationDatabaseSettings):
        database = cls.__new__(cls)
        database._key = object()
        database._engine = create_engine(settings.url, pool_size=settings.pool_size,
            max_overflow=settings.max_overflow, pool_timeout=settings.pool_timeout_seconds,
            pool_pre_ping=True, hide_parameters=True, isolation_level=_DEFAULT_ISOLATION_LEVEL,
            connect_args={'connection_timeout': settings.connect_timeout_seconds,
                          'time_zone': '+00:00', 'sql_mode': 'STRICT_ALL_TABLES,NO_ENGINE_SUBSTITUTION'})
        return database

    @property
    def dialect(self):
        return self._engine.dialect.name

    @property
    def location(self):
        return self._engine.url.database

    def _active_transaction(self):
        state = _TRANSACTIONS.get().get(self._key)
        return state if state is not None and state['owner'] == _execution_owner() else None

    @contextmanager
    def connect(self):
        active = self._active_transaction()
        if active is not None:
            yield active['connection']
        else:
            with self._engine.connect() as connection:
                yield connection

    @contextmanager
    def transaction(self, *, isolation_level=None):
        active = self._active_transaction()
        if active is not None:
            try:
                if isolation_level is not None and isolation_level != active['isolation_level']:
                    raise ValueError('Nested transaction isolation level does not match the active transaction')
                yield active['connection']
            except BaseException as error:
                active['error'] = error
                raise
            return
        with self._engine.connect() as connection:
            if isolation_level is not None:
                connection = connection.execution_options(isolation_level=isolation_level)
            with connection.begin():
                state = {'connection': connection, 'owner': _execution_owner(), 'error': None,
                         'isolation_level': isolation_level or _DEFAULT_ISOLATION_LEVEL}
                token = _TRANSACTIONS.set({**_TRANSACTIONS.get(), self._key: state})
                try:
                    yield connection
                    if state['error'] is not None:
                        raise RuntimeError('Cannot commit after a failed nested transaction') from state['error']
                finally:
                    _TRANSACTIONS.reset(token)

    def close(self):
        self._engine.dispose()


def upsert(connection, table, values, *, conflict_columns, updates=None):
    """Compile explicit conflict expressions through the supported SQLAlchemy dialect."""
    statement = mysql_insert(table)
    if not isinstance(values, list):
        statement = statement.values(values)
    incoming = statement.inserted
    changes = updates(incoming) if callable(updates) else {name: getattr(incoming, name) for name in updates or ()}
    # No INSERT IGNORE: strict length/type errors must remain visible.
    changes = changes or {conflict_columns[0]: table.c[conflict_columns[0]]}
    statement = statement.on_duplicate_key_update(**changes)
    return (
        connection.execute(statement, values)
        if isinstance(values, list)
        else connection.execute(statement)
    )


class ReadonlyDatabase:
    def __init__(self, settings: DatabaseSettings):
        self._pool = MySQLConnectionPool(
            pool_name="smarttest_wifi_readonly", pool_size=settings.pool_size,
            pool_reset_session=True, host=settings.host, port=settings.port,
            user=settings.user, password=settings.password, database=settings.database,
        )

    def select(self, sql: str, params=()) -> list[dict]:
        ensure_readonly_sql(sql)
        connection = self._pool.get_connection()
        try:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(sql, tuple(params))
                return cursor.fetchall()
            finally:
                cursor.close()
        finally:
            connection.close()


def is_unique_violation(error):
    original = error.orig
    return getattr(original, 'errno', None) == 1062
