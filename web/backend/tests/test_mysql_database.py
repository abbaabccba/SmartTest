"""Run only against an explicitly supplied, isolated MySQL schema."""
import asyncio
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, insert, select

from smarttest_web.config import ApplicationDatabaseSettings
from smarttest_web.database import WebDatabase


@pytest.fixture
def mysql_database():
    if not os.environ.get('SMARTTEST_TEST_DB_NAME'):
        pytest.skip('Set SMARTTEST_TEST_DB_* for an isolated MySQL schema')
    env = {name.replace('SMARTTEST_TEST_DB_', 'SMARTTEST_DB_'): value for name, value in os.environ.items()
           if name.startswith('SMARTTEST_TEST_DB_')}
    database = WebDatabase.from_settings(ApplicationDatabaseSettings.from_environment(env))
    metadata = MetaData()
    table = Table('foundation_' + uuid4().hex, metadata, Column('id', Integer, primary_key=True))
    with database.transaction() as connection:
        table.create(connection)
    try:
        yield database, table
    finally:
        with database.transaction() as connection:
            table.drop(connection)
        database.close()


def test_mysql_commit_rollback_and_nested_failure(mysql_database):
    database, table = mysql_database
    with database.transaction() as outer:
        outer.execute(insert(table).values(id=1))
        with database.transaction() as nested, database.connect() as reader:
            assert nested is outer and reader is outer
    with pytest.raises(ValueError):
        with database.transaction() as connection:
            connection.execute(insert(table).values(id=2))
            raise ValueError('rollback')
    with pytest.raises(RuntimeError, match='nested transaction'):
        with database.transaction() as outer:
            outer.execute(insert(table).values(id=3))
            try:
                with database.transaction():
                    raise ValueError('caught by business')
            except ValueError:
                pass
    with database.connect() as connection:
        assert connection.execute(select(table.c.id)).scalars().all() == [1]


def test_explicit_write_isolation_is_scoped_and_nested_mismatch_rolls_back(mysql_database):
    database, table = mysql_database
    with database.transaction() as connection:
        assert connection.get_isolation_level() == 'REPEATABLE READ'
    with database.transaction(isolation_level='READ COMMITTED') as outer:
        assert outer.get_isolation_level() == 'READ COMMITTED'
        with database.transaction() as inherited:
            assert inherited is outer
        with database.transaction(isolation_level='READ COMMITTED') as matching:
            assert matching is outer
    with database.transaction() as connection:
        assert connection.get_isolation_level() == 'REPEATABLE READ'
    with pytest.raises(RuntimeError, match='nested transaction'):
        with database.transaction() as outer:
            outer.execute(insert(table).values(id=5))
            with pytest.raises(ValueError, match='isolation'):
                with database.transaction(isolation_level='READ COMMITTED'):
                    pytest.fail('isolation cannot change inside an active transaction')
    with database.connect() as connection:
        assert connection.execute(select(table.c.id)).scalars().all() == []


def test_mysql_inherited_context_does_not_share_connection_across_threads_or_tasks(mysql_database):
    database, _table = mysql_database
    with database.transaction() as outer:
        def read():
            with database.connect() as connection:
                return connection is outer
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(copy_context().run, read).result() is False

    async def scenario():
        with database.transaction() as outer:
            async def child():
                with database.connect() as connection:
                    return connection is outer
            assert await asyncio.create_task(child()) is False
            with database.connect() as current:
                assert current is outer
    asyncio.run(scenario())
