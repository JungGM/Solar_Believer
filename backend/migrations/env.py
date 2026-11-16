from alembic import context
from sqlalchemy import create_engine

from backend.app.config import get_settings
from backend.app.db import Base
from backend.app import models  # noqa: F401 -- register tables


def run():
    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run()
