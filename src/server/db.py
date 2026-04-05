"""Database engine, session factory, and table creation for async PostgreSQL."""
import asyncio
import logging

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession, AsyncEngine
from sqlalchemy.orm import DeclarativeBase
from src.config import DATABASE_URL

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def create_engine(db_url: str = DATABASE_URL) -> AsyncEngine:
    """Create a single async engine with appropriate pool configuration.

    For PostgreSQL, uses a sized asyncpg connection pool. For SQLite (used
    in tests), pool_size and related params are omitted since StaticPool
    does not support them.

    Args:
        db_url: SQLAlchemy database URL string.

    Returns:
        Configured AsyncEngine instance.
    """
    is_sqlite = db_url.startswith("sqlite")
    if is_sqlite:
        return create_async_engine(db_url, echo=False)
    return create_async_engine(
        db_url,
        echo=False,
        pool_size=20,
        max_overflow=60,
        pool_pre_ping=True,
        pool_timeout=60,
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

    Retries the initial DB connection up to 5 times with a 2-second delay
    between attempts. This handles the Docker startup race where Postgres
    accepts TCP connections but rejects queries with CannotConnectNowError.

    Args:
        db_url: SQLAlchemy database URL string. Defaults to DATABASE_URL from config.

    Returns:
        Tuple of (AsyncEngine, async_sessionmaker).
    """
    engine = create_engine(db_url)
    max_retries = 5
    delay = 2

    for attempt in range(1, max_retries + 1):
        try:
            async with engine.begin() as conn:
                from src.server.models_db import PlayerRecord, PlayerScore, MarketSnapshot, ListingObservation, DailyListingSummary, TradeAction, TradeRecord, PortfolioSlot, ScannerStatus, DailyTransactionCount  # noqa: F401
                await conn.run_sync(Base.metadata.create_all)
            break
        except Exception as e:
            if attempt < max_retries:
                logger.warning(
                    f"DB connection attempt {attempt}/{max_retries} failed: {e}. Retrying in {delay}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"DB connection attempt {attempt}/{max_retries} failed: {e}. All retries exhausted."
                )
                raise

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
