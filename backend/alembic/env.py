from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.auth import models as _auth_models  # noqa: F401
from app.creators import models as _creator_models  # noqa: F401
from app.outbox import models as _outbox_models  # noqa: F401
from app.audit import models as _audit_models  # noqa: F401
from app.catalog import models as _catalog_models  # noqa: F401
from app.content import models as _content_models  # noqa: F401
from app.youtube import models as _youtube_models  # noqa: F401
from app.readings import models as _reading_models  # noqa: F401
from app.billing import models as _billing_models  # noqa: F401
from app.payouts import models as _payout_models  # noqa: F401
from app.exports import models as _export_models  # noqa: F401
from app.notifications import models as _notification_models  # noqa: F401
from app.support import models as _support_models  # noqa: F401
from app.lifecycle import models as _lifecycle_models  # noqa: F401
from app.account_deletion import models as _account_deletion_models  # noqa: F401
from app.legal import models as _legal_models  # noqa: F401
from app.database.base import Base
from app.database.config import DatabaseSettings

config = context.config
config.set_main_option("sqlalchemy.url", DatabaseSettings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
