from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from aap.config import get_settings
from aap.db.models import Base
from aap.db.session import sqlalchemy_url

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.attributes.get("url") or sqlalchemy_url(get_settings().aap_owner_dsn)


def _include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001
    # Tables owned by libraries (Procrastinate, LangGraph checkpointer) are not ours.
    if type_ == "table" and reflected and compare_to is None:
        return not (name.startswith("procrastinate_") or name.startswith("checkpoint"))
    return True


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      include_object=_include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is None:
        connectable = create_engine(_url(), poolclass=pool.NullPool)
        with connectable.connect() as connection:
            _run(connection)
    else:
        _run(connectable)


def _run(connection) -> None:  # noqa: ANN001
    context.configure(connection=connection, target_metadata=target_metadata, include_object=_include_object,
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
