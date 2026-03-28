"""Database engine, session factory, and table creation for async PostgreSQL."""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession, AsyncEngine
from sqlalchemy.orm import DeclarativeBase
from src.config import DATABASE_URL


class Base(DeclarativeBase):
    pass


def create_engine(db_url: str = DATABASE_URL) -> AsyncEngine:
    """Create a single async Postgres engine with asyncpg connection pool.

    Postgres MVCC eliminates the need for separate read/write engines.
    The connection pool handles concurrent scanner + API access.

    Args:
        db_url: SQLAlchemy database URL string.

    Returns:
        Configured AsyncEngine instance.
    """
    return create_async_engine(
        db_url,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine.

    Args:
        engine: The AsyncEngine to bind sessions to.

    Returns:
        Configured async_sessionmaker instance.
    """
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def create_engine_and_tables(db_url: str = DATABASE_URL) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create engine, create all ORM tables, return engine and session factory.

    Args:
        db_url: SQLAlchemy database URL string. Defaults to DATABASE_URL from config.

    Returns:
        Tuple of (AsyncEngine, async_sessionmaker).
    """
    engine = create_engine(db_url)
    async with engine.begin() as conn:
        from src.server.models_db import PlayerRecord, PlayerScore, MarketSnapshot, SnapshotSale, SnapshotPricePoint, ListingObservation, DailyListingSummary, TradeAction, TradeRecord, PortfolioSlot  # noqa: F401
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
