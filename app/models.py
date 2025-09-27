from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, BigInteger, Boolean
from datetime import datetime
import os

Base = declarative_base()
DB_PATH = os.path.join("fxmonitor.sqlite")
engine = create_engine(f"sqlite:///{DB_PATH}", future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    chat_id = Column(String, unique=True, index=True, nullable=False)
    api_key = Column(String, unique=True, index=True, nullable=False)
    short_id = Column(String, unique=True, index=True, nullable=True)
    min_equity = Column(Float, nullable=True)
    min_ml = Column(Float, nullable=True)
    max_daily_loss = Column(Float, nullable=True)
    dd_percent = Column(Float, nullable=True)
    heartbeat_min = Column(Integer, nullable=True)
    last_alert_at = Column(DateTime, nullable=True)
    lost_conn_alerted = Column(Boolean, default=False)

class LastSnapshot(Base):
    __tablename__ = "last_snapshots"
    id = Column(Integer, primary_key=True)
    api_key = Column(String, unique=True, index=True, nullable=False)
    account_id = Column(BigInteger, nullable=False)
    equity = Column(Float)
    margin_level = Column(Float)
    pnl_daily = Column(Float)
    balance = Column(Float)
    max_equity = Column(Float)
    last_seen = Column(DateTime)
    ts = Column(DateTime, default=datetime.utcnow)

from sqlalchemy import UniqueConstraint

class SymbolSnapshot(Base):
    __tablename__ = "symbol_snapshots"
    id = Column(Integer, primary_key=True)
    api_key = Column(String, index=True, nullable=False)
    account_id = Column(BigInteger, nullable=False)
    symbol = Column(String, nullable=False)
    price = Column(Float)
    dd_percent = Column(Float)
    buy_lots = Column(Float)
    buy_count = Column(Integer)
    sell_lots = Column(Float)
    sell_count = Column(Integer)
    ts = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("api_key", "account_id", "symbol", name="uix_symbol_unique"),
    )

class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    api_key = Column(String, nullable=False)
    account_id = Column(String, nullable=False)
    name = Column(String, nullable=True)
    is_cent = Column(Boolean, default=False)

    __table_args__ = (UniqueConstraint("api_key", "account_id"),)