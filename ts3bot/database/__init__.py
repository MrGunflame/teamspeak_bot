from pathlib import Path

from alembic import command, script
from alembic.config import Config
from alembic.runtime import migration
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from . import models


def create_session(database_uri: str, is_test=False) -> Session:
    if database_uri.startswith("sqlite"):
        engine = create_engine(
            database_uri, echo=False, connect_args={"check_same_thread": False}
        )
    else:
        engine = create_engine(
            database_uri,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=600,
            pool_size=10,
            pool_use_lifo=True,
            poolclass=QueuePool,
        )

    alembic_cfg = Config(
        str((Path(__file__).parent.parent.parent / "alembic.ini").absolute())
    )

    # Hot-patch migration location if in test environment
    if is_test:
        alembic_cfg.set_section_option("alembic", "script_location", str(Path(__file__).parent / "migrations"))

    # Create tables if accounts does not exist
    if not engine.dialect.has_table(engine, "accounts"):
        models.Base.metadata.create_all(engine)
        command.stamp(alembic_cfg, "head")

    if not is_test:
        # Check if there are any pending migrations
        with engine.begin() as con:
            script_dir = script.ScriptDirectory.from_config(alembic_cfg)
            ctx = migration.MigrationContext.configure(con)

            assert set(ctx.get_current_heads()) == set(
                script_dir.get_heads()
            ), 'There are pending migrations, run them via "alembic upgrade heads"'

    # Bind engine and create session
    models.Base.metadata.bind = engine
    session: Session = sessionmaker(bind=engine)()

    return session
