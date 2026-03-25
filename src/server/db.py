"""Database engine, session factory, and table creation for async SQLite."""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession, AsyncEngine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event
from src.config import DATABASE_URL


class Base(DeclarativeBase):
    pass


def create_engine(db_url: str = DATABASE_URL) -> AsyncEngine:
    """Create an async SQLAlchemy engine with WAL mode enabled.

    Args:
        db_url: SQLAlchemy database URL string.

    Returns:
        Configured AsyncEngine instance.
    """
    engine = create_async_engine(db_url, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def enable_wal(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine.

    Args:
        engine: The AsyncEngine to bind sessions to.

    Returns:
        Configured async_sessionmaker instance.
    """
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def create_engine_and_tables(db_url: str = DATABASE_URL) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create engine, enable WAL, create all ORM tables, return engine and session factory.

    Args:
        db_url: SQLAlchemy database URL string. Defaults to DATABASE_URL from config.

    Returns:
        Tuple of (AsyncEngine, async_sessionmaker).
    """
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        from src.server.models_db import PlayerRecord, PlayerScore  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    return engine, session_factory


async def get_session(session_factory: async_sessionmaker[AsyncSession]):
    """Yield an AsyncSession from the given session factory.

    Args:
        session_factory: The async_sessionmaker to create a session from.

    Yields:
        An active AsyncSession.
    """
    async with session_factory() as session:
        yield session
