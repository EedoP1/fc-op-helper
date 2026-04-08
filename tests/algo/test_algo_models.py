"""Tests for algo trading DB models."""
import pytest
from datetime import datetime
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from src.server.db import Base
from src.server.models_db import AlgoConfig, AlgoSignal, AlgoPosition


@pytest.fixture
def db():
    """In-memory SQLite for model tests."""
    from sqlalchemy import create_engine as sync_create
    engine = sync_create("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_algo_config_create(db):
    config = AlgoConfig(
        budget=5_000_000,
        is_active=True,
        strategy_params=None,
        created_at=datetime(2026, 4, 8),
        updated_at=datetime(2026, 4, 8),
    )
    db.add(config)
    db.commit()
    result = db.execute(select(AlgoConfig)).scalar_one()
    assert result.budget == 5_000_000
    assert result.is_active is True


def test_algo_signal_create(db):
    signal = AlgoSignal(
        ea_id=12345,
        action="BUY",
        quantity=3,
        reference_price=15000,
        status="PENDING",
        created_at=datetime(2026, 4, 8),
    )
    db.add(signal)
    db.commit()
    result = db.execute(select(AlgoSignal)).scalar_one()
    assert result.ea_id == 12345
    assert result.action == "BUY"
    assert result.quantity == 3
    assert result.status == "PENDING"


def test_algo_position_create(db):
    pos = AlgoPosition(
        ea_id=12345,
        quantity=3,
        buy_price=15000,
        buy_time=datetime(2026, 4, 8, 12, 0),
        peak_price=15000,
    )
    db.add(pos)
    db.commit()
    result = db.execute(select(AlgoPosition)).scalar_one()
    assert result.ea_id == 12345
    assert result.quantity == 3
    assert result.buy_price == 15000
