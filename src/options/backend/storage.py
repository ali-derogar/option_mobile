"""SQLite storage."""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    cast,
    create_engine,
    delete,
    event,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from options.backend.config import DATABASE_PATH
from options.backend.services.expiry_selection import (
    ExpiryFilters,
    ExpirySnapshot,
    select_expiries,
)

logger = logging.getLogger(__name__)
MARKET_TZ = ZoneInfo("Asia/Tehran")
HISTORY_RETENTION_DAYS = 20
HISTORY_LOCK_TIMEOUT_SECONDS = 5
_HISTORY_RETENTION_KEY = "history_retention"
_HISTORY_MAINTENANCE_LOCK = threading.Lock()


def _reset_sqlite_connection(dbapi_connection, _connection_record, reset_state) -> None:
    # A busy COMMIT can leave SQLite's transaction open after SQLAlchemy has
    # marked it inactive. Never return that pending write and its locks to the
    # pool, where a later request could reuse or accidentally commit them.
    if not reset_state.terminate_only and dbapi_connection.in_transaction:
        dbapi_connection.rollback()


class Base(DeclarativeBase):
    pass


class Contract(Base):
    __tablename__ = "contracts"

    ins_code = Column(Integer, primary_key=True)
    instrument_id = Column(String(64), nullable=True)
    option_type = Column(String(16), nullable=True)
    symbol = Column(String(128), nullable=True)
    short_name = Column(String(256), nullable=True)
    long_name = Column(String(512), nullable=True)
    isin = Column(String(32), nullable=True)
    buy_open_positions = Column(Float, nullable=True)
    sell_open_positions = Column(Float, nullable=True)
    yesterday_open_positions = Column(Float, nullable=True)
    contract_size = Column(Float, nullable=True)
    strike_price = Column(Float, nullable=True)
    underlying_ins_code = Column(Integer, nullable=True)
    underlying_symbol = Column(String(128), nullable=True)
    underlying_short_name = Column(String(256), nullable=True)
    underlying_last_price = Column(Float, nullable=True)
    underlying_closing_price = Column(Float, nullable=True)
    moneyness = Column(String(16), nullable=True)
    intrinsic_value = Column(Float, nullable=True)
    begin_date = Column(Integer, nullable=True)
    end_date = Column(Integer, nullable=True)
    a_factor = Column(Float, nullable=True)
    b_factor = Column(Float, nullable=True)
    c_factor = Column(Float, nullable=True)
    market_name = Column(String(256), nullable=True)
    sector = Column(String(256), nullable=True)
    last_price = Column(Float, nullable=True)
    closing_price = Column(Float, nullable=True)
    price_change = Column(String(32), nullable=True)
    trade_volume = Column(Float, nullable=True)
    trade_value = Column(Float, nullable=True)
    trade_count = Column(Integer, nullable=True)
    price_min = Column(Float, nullable=True)
    price_max = Column(Float, nullable=True)
    instrument_json = Column(Text, nullable=True)
    fetched_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)


class ContractSnapshot(Base):
    __tablename__ = "contract_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String(10), nullable=False, index=True)
    ins_code = Column(Integer, nullable=False, index=True)
    instrument_id = Column(String(64), nullable=True)
    option_type = Column(String(16), nullable=True)
    symbol = Column(String(128), nullable=True)
    short_name = Column(String(256), nullable=True)
    long_name = Column(String(512), nullable=True)
    isin = Column(String(32), nullable=True)
    buy_open_positions = Column(Float, nullable=True)
    sell_open_positions = Column(Float, nullable=True)
    yesterday_open_positions = Column(Float, nullable=True)
    contract_size = Column(Float, nullable=True)
    strike_price = Column(Float, nullable=True)
    underlying_ins_code = Column(Integer, nullable=True)
    underlying_symbol = Column(String(128), nullable=True)
    underlying_short_name = Column(String(256), nullable=True)
    underlying_last_price = Column(Float, nullable=True)
    underlying_closing_price = Column(Float, nullable=True)
    moneyness = Column(String(16), nullable=True)
    intrinsic_value = Column(Float, nullable=True)
    begin_date = Column(Integer, nullable=True)
    end_date = Column(Integer, nullable=True)
    a_factor = Column(Float, nullable=True)
    b_factor = Column(Float, nullable=True)
    c_factor = Column(Float, nullable=True)
    market_name = Column(String(256), nullable=True)
    sector = Column(String(256), nullable=True)
    last_price = Column(Float, nullable=True)
    closing_price = Column(Float, nullable=True)
    price_change = Column(String(32), nullable=True)
    trade_volume = Column(Float, nullable=True)
    trade_value = Column(Float, nullable=True)
    trade_count = Column(Integer, nullable=True)
    price_min = Column(Float, nullable=True)
    price_max = Column(Float, nullable=True)
    instrument_json = Column(Text, nullable=True)
    fetched_at = Column(DateTime, nullable=False, index=True)
    updated_at = Column(DateTime, nullable=False)


class OpenInterestSnapshot(Base):
    __tablename__ = "open_interest"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ins_code = Column(Integer, nullable=False, index=True)
    buy_open_positions = Column(Float, nullable=True)
    sell_open_positions = Column(Float, nullable=True)
    yesterday_open_positions = Column(Float, nullable=True)
    fetched_at = Column(DateTime, nullable=False, index=True)


class MoneyFlowSnapshot(Base):
    __tablename__ = "money_flow"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ins_code = Column(Integer, nullable=False, index=True)
    rec_date = Column(Integer, nullable=True)
    natural_money_flow = Column(Float, nullable=True)
    legal_money_flow = Column(Float, nullable=True)
    fetched_at = Column(DateTime, nullable=False, index=True)


class ClientTypeStats(Base):
    __tablename__ = "client_type_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ins_code = Column(Integer, nullable=False, index=True)
    rec_date = Column(Integer, nullable=True)
    natural_buy_volume = Column(Float, nullable=True)
    natural_buy_value = Column(Float, nullable=True)
    natural_buy_count = Column(Integer, nullable=True)
    natural_sell_volume = Column(Float, nullable=True)
    natural_sell_value = Column(Float, nullable=True)
    natural_sell_count = Column(Integer, nullable=True)
    legal_buy_volume = Column(Float, nullable=True)
    legal_buy_value = Column(Float, nullable=True)
    legal_buy_count = Column(Integer, nullable=True)
    legal_sell_volume = Column(Float, nullable=True)
    legal_sell_value = Column(Float, nullable=True)
    legal_sell_count = Column(Integer, nullable=True)
    natural_money_flow = Column(Float, nullable=True)
    legal_money_flow = Column(Float, nullable=True)
    fetched_at = Column(DateTime, nullable=False, index=True)


class AppState(Base):
    __tablename__ = "app_state"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=False)


class UnderlyingCatalog(Base):
    __tablename__ = "underlying_catalog"

    ins_code = Column(Integer, primary_key=True)
    symbol = Column(String(128), nullable=False)
    name = Column(String(256), nullable=True)
    underlying_trade_volume = Column(Float, nullable=True)
    options_trade_volume = Column(Float, nullable=True)
    market_date = Column(String(10), nullable=True)
    available = Column(Boolean, nullable=False, default=True)
    fetched_at = Column(DateTime, nullable=False)


class WatchlistEntry(Base):
    __tablename__ = "watchlist"

    ins_code = Column(Integer, primary_key=True)
    created_at = Column(DateTime, nullable=False)


class ExpiryContract(Base):
    __tablename__ = "expiry_contracts"

    ins_code = Column(Integer, primary_key=True)
    symbol = Column(String(128), nullable=False)
    underlying_ins_code = Column(Integer, nullable=True)
    underlying_symbol = Column(String(128), nullable=True)
    option_type = Column(String(16), nullable=False)
    strike_price = Column(Float, nullable=True)
    end_date = Column(Integer, nullable=False, index=True)
    ask_price = Column(Float, nullable=True)
    ask_volume = Column(Float, nullable=True)
    underlying_last_price = Column(Float, nullable=True)
    underlying_closing_price = Column(Float, nullable=True)
    trade_volume = Column(Float, nullable=True)
    price_min = Column(Float, nullable=True)
    price_max = Column(Float, nullable=True)
    price_range_date = Column(Integer, nullable=True)


class Storage:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DATABASE_PATH
        self.engine = create_engine(
            f"sqlite:///{self.db_path}", connect_args={"timeout": HISTORY_LOCK_TIMEOUT_SECONDS},
        )
        event.listen(self.engine, "reset", _reset_sqlite_connection)
        Base.metadata.create_all(self.engine)
        self._ensure_schema()
        self.SessionLocal = sessionmaker(bind=self.engine)
        self._backfill_current_contract_snapshot()

    def session(self) -> Session:
        return self.SessionLocal()

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def cleanup_old_history(self) -> dict[str, int] | None:
        """Prune after a successful refresh; maintenance must not fail that refresh.

        The marker prevents legacy backfill from resurrecting deleted history.
        Pending compaction survives failures and process restarts.
        """
        with _HISTORY_MAINTENANCE_LOCK:
            try:
                today = date.fromisoformat(self.snapshot_date_for(self.now()))
                keep_from = today - timedelta(days=HISTORY_RETENTION_DAYS - 1)
                counts, vacuum_token = self._prune_history(keep_from)
            except Exception:
                logger.exception("History cleanup failed; retrying after the next successful refresh")
                return None
            logger.info("History cleanup before %s: %s", keep_from, counts)
            if vacuum_token:
                try:
                    self._vacuum_history()
                    with self.session() as session:
                        # Another process may have pruned more rows after our
                        # VACUUM. Do not acknowledge that newer pending work.
                        session.execute(update(AppState).where(
                            AppState.key == _HISTORY_RETENTION_KEY,
                            AppState.value == vacuum_token,
                        ).values(value="clean", updated_at=self.now()))
                        session.commit()
                    logger.info("History database compaction completed")
                except Exception:
                    logger.exception("History compaction failed; retrying after the next successful refresh")
            return counts

    def _prune_history(self, keep_from: date) -> tuple[dict[str, int], str | None]:
        cutoff_utc = (
            datetime.combine(keep_from, time.min, tzinfo=MARKET_TZ)
            .astimezone(timezone.utc).replace(tzinfo=None)
        )
        with self.session() as session:
            counts = {
                ContractSnapshot.__tablename__: session.execute(
                    delete(ContractSnapshot).where(ContractSnapshot.snapshot_date < keep_from.isoformat())
                ).rowcount,
                OpenInterestSnapshot.__tablename__: session.execute(
                    delete(OpenInterestSnapshot).where(OpenInterestSnapshot.fetched_at < cutoff_utc)
                ).rowcount,
            }
            for model in (MoneyFlowSnapshot, ClientTypeStats):
                counts[model.__tablename__] = self._prune_recorded_history(session, model, keep_from)
            state = session.get(AppState, _HISTORY_RETENTION_KEY)
            previous_value = (state.value or "") if state is not None else ""
            vacuum_token = previous_value if previous_value.startswith("pending_vacuum:") else None
            if any(counts.values()):
                vacuum_token = f"pending_vacuum:{uuid4().hex}"
            if state is None:
                state = AppState(key=_HISTORY_RETENTION_KEY)
                session.add(state)
            state.value = vacuum_token or "clean"
            state.updated_at = self.now()
            session.commit()
        return counts, vacuum_token

    def _prune_recorded_history(
        self, session: Session, model: type[MoneyFlowSnapshot] | type[ClientTypeStats], keep_from: date,
    ) -> int:
        # Read only dates and IDs in bounded batches. Validate Gregorian record
        # dates in Python instead of treating malformed integers as old dates.
        deleted = 0
        last_id = 0
        while rows := session.execute(
            select(model.id, model.rec_date, model.fetched_at)
            .where(model.id > last_id).order_by(model.id).limit(500)
        ).all():
            expired = []
            for row in rows:
                try:
                    recorded_date = date.fromisoformat(str(self._coerce_rec_date(row.rec_date)))
                except ValueError:
                    recorded_date = date.fromisoformat(self.snapshot_date_for(row.fetched_at))
                if recorded_date < keep_from:
                    expired.append(row.id)
            if expired:
                deleted += session.execute(delete(model).where(model.id.in_(expired))).rowcount
            last_id = rows[-1].id
        return deleted

    def _vacuum_history(self) -> None:
        # SQLite cannot VACUUM inside a transaction. All pruning sessions have
        # committed and closed before acquiring this autocommit connection.
        with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql("VACUUM")

    def replace_expiry_contracts(
        self, rows: list[dict[str, Any]], warning: str | None = None, *, market_received_at: str | None = None,
    ) -> None:
        """Replace the independent market snapshot and its metadata atomically."""
        now = self.now()
        with self.session() as session:
            session.execute(delete(ExpiryContract))
            session.add_all(ExpiryContract(**row) for row in rows)
            metadata = session.get(AppState, "expiry_snapshot")
            if metadata is None:
                metadata = AppState(key="expiry_snapshot")
                session.add(metadata)
            metadata.value = json.dumps({"warning": warning, "market_received_at": market_received_at}, ensure_ascii=False)
            metadata.updated_at = now
            session.commit()

    def get_expiry_snapshot(
        self, today: date, limit: int = 50, *, filters: ExpiryFilters | None = None,
    ) -> dict[str, Any]:
        """Read rows and metadata from the same committed SQLite snapshot."""
        cutoff = int(today.strftime("%Y%m%d"))
        with self.session() as session:
            # SQLite's legacy driver does not begin a read transaction on SELECT.
            session.execute(text("BEGIN"))
            metadata = session.get(AppState, "expiry_snapshot")
            rows = session.scalars(select(ExpiryContract).where(
                ExpiryContract.end_date >= cutoff,
            )).all()
            info = json.loads(metadata.value) if metadata else {}
            snapshot = ExpirySnapshot(
                [{col.name: getattr(row, col.name) for col in ExpiryContract.__table__.columns} for row in rows],
                info.get("warning"), info.get("market_received_at"),
            )
            return {
                **select_expiries(snapshot, today, filters, limit),
                "fetched_at": metadata.updated_at.replace(tzinfo=timezone.utc).isoformat() if metadata else None,
            }

    def get_watchlist_codes(self) -> List[int]:
        with self.session() as session:
            return list(session.scalars(select(WatchlistEntry.ins_code).order_by(WatchlistEntry.ins_code)))

    def replace_watchlist(self, ins_codes: List[int]) -> None:
        with self.session() as session:
            session.execute(delete(WatchlistEntry))
            session.add_all(WatchlistEntry(ins_code=code, created_at=self.now()) for code in sorted(set(ins_codes)))
            session.commit()

    def get_underlying_catalog(self) -> List[Dict[str, Any]]:
        with self.session() as session:
            rows = session.scalars(select(UnderlyingCatalog).order_by(UnderlyingCatalog.ins_code)).all()
            return [
                {
                    "ins_code": str(row.ins_code),
                    "symbol": row.symbol,
                    "name": row.name,
                    "underlying_trade_volume": row.underlying_trade_volume,
                    "options_trade_volume": row.options_trade_volume,
                    "market_date": row.market_date,
                    "available": row.available,
                    "fetched_at": row.fetched_at.replace(tzinfo=timezone.utc).isoformat(),
                }
                for row in rows
            ]

    def replace_underlying_catalog(self, rows: List[Dict[str, Any]]) -> None:
        now = self.now()
        with self.session() as session:
            for existing in session.scalars(select(UnderlyingCatalog)):
                existing.available = False
            for item in rows:
                code = int(item["ins_code"])
                row = session.get(UnderlyingCatalog, code)
                if row is None:
                    row = UnderlyingCatalog(ins_code=code)
                    session.add(row)
                for key in ("symbol", "name", "underlying_trade_volume", "options_trade_volume", "market_date"):
                    setattr(row, key, item.get(key))
                row.available = True
                row.fetched_at = now
            session.commit()

    def is_activated(self) -> bool:
        with self.session() as session:
            row = session.get(AppState, "activated")
            return row is not None and row.value == "1"

    def set_activated(self, activated: bool = True) -> None:
        now = self.now()
        with self.session() as session:
            row = session.get(AppState, "activated")
            if row is None:
                session.add(AppState(key="activated", value="1" if activated else "0", updated_at=now))
            else:
                row.value = "1" if activated else "0"
                row.updated_at = now
            session.commit()

    @staticmethod
    def snapshot_date_for(dt: Optional[datetime] = None) -> str:
        dt = dt or datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(MARKET_TZ).date().isoformat()

    def _ensure_schema(self) -> None:
        """Add lightweight columns for existing SQLite databases."""
        inspector = inspect(self.engine)
        tables = set(inspector.get_table_names())
        columns = {
            "option_type": "VARCHAR(16)",
            "underlying_symbol": "VARCHAR(128)",
            "underlying_short_name": "VARCHAR(256)",
            "underlying_last_price": "FLOAT",
            "underlying_closing_price": "FLOAT",
            "moneyness": "VARCHAR(16)",
            "intrinsic_value": "FLOAT",
        }
        with self.engine.begin() as conn:
            migrations = {
                "contracts": columns,
                "contract_snapshots": columns,
                "expiry_contracts": {
                    "ask_price": "FLOAT", "ask_volume": "FLOAT",
                    "underlying_last_price": "FLOAT", "trade_volume": "FLOAT",
                    "underlying_closing_price": "FLOAT",
                    "price_min": "FLOAT", "price_max": "FLOAT", "price_range_date": "INTEGER",
                },
            }
            for table_name, additions in migrations.items():
                if table_name not in tables:
                    continue
                existing = {col["name"] for col in inspector.get_columns(table_name)}
                missing = [(name, sql_type) for name, sql_type in additions.items() if name not in existing]
                for name, sql_type in missing:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {sql_type}"))

    def upsert_contracts(self, contracts: List[Dict[str, Any]]) -> int:
        now = self.now()
        count = 0
        with self.session() as session:
            for c in contracts:
                if not isinstance(c, dict):
                    continue
                ins_code = self._coerce_ins_code(c.get("ins_code"))
                if not ins_code:
                    continue
                existing = session.get(Contract, ins_code)
                instrument_meta = c.get("instrument_meta")
                fields = {
                    "instrument_id": c.get("instrument_id"),
                    "option_type": c.get("option_type"),
                    "symbol": c.get("symbol"),
                    "short_name": c.get("short_name"),
                    "long_name": c.get("long_name"),
                    "isin": c.get("isin"),
                    "buy_open_positions": c.get("buy_open_positions"),
                    "sell_open_positions": c.get("sell_open_positions"),
                    "yesterday_open_positions": c.get("yesterday_open_positions"),
                    "contract_size": c.get("contract_size"),
                    "strike_price": c.get("strike_price"),
                    "underlying_ins_code": self._coerce_ins_code(c.get("underlying_ins_code")),
                    "underlying_symbol": c.get("underlying_symbol"),
                    "underlying_short_name": c.get("underlying_short_name"),
                    "underlying_last_price": c.get("underlying_last_price"),
                    "underlying_closing_price": c.get("underlying_closing_price"),
                    "moneyness": c.get("moneyness"),
                    "intrinsic_value": c.get("intrinsic_value"),
                    "begin_date": c.get("begin_date"),
                    "end_date": c.get("end_date"),
                    "a_factor": c.get("a_factor"),
                    "b_factor": c.get("b_factor"),
                    "c_factor": c.get("c_factor"),
                    "market_name": c.get("market_name"),
                    "sector": c.get("sector"),
                    "last_price": c.get("last_price"),
                    "closing_price": c.get("closing_price"),
                    "price_change": c.get("price_change"),
                    "trade_volume": c.get("trade_volume"),
                    "trade_value": c.get("trade_value"),
                    "trade_count": c.get("trade_count"),
                    "price_min": c.get("price_min"),
                    "price_max": c.get("price_max"),
                    "instrument_json": json.dumps(instrument_meta, ensure_ascii=False)
                    if instrument_meta
                    else None,
                    "fetched_at": now,
                    "updated_at": now,
                }
                if existing:
                    for key, val in fields.items():
                        setattr(existing, key, val)
                else:
                    session.add(Contract(ins_code=ins_code, **fields))
                count += 1
            session.commit()
        return count

    def insert_contract_snapshot(
        self,
        contracts: List[Dict[str, Any]],
        snapshot_date: Optional[str] = None,
    ) -> int:
        now = self.now()
        snapshot_date = _normalize_date_text(snapshot_date) if snapshot_date else self.snapshot_date_for(now)
        count = 0
        with self.session() as session:
            for c in contracts:
                if not isinstance(c, dict):
                    continue
                ins_code = self._coerce_ins_code(c.get("ins_code"))
                if not ins_code:
                    continue
                instrument_meta = c.get("instrument_meta")
                session.add(
                    ContractSnapshot(
                        snapshot_date=snapshot_date,
                        ins_code=ins_code,
                        instrument_id=c.get("instrument_id"),
                        option_type=c.get("option_type"),
                        symbol=c.get("symbol"),
                        short_name=c.get("short_name"),
                        long_name=c.get("long_name"),
                        isin=c.get("isin"),
                        buy_open_positions=c.get("buy_open_positions"),
                        sell_open_positions=c.get("sell_open_positions"),
                        yesterday_open_positions=c.get("yesterday_open_positions"),
                        contract_size=c.get("contract_size"),
                        strike_price=c.get("strike_price"),
                        underlying_ins_code=self._coerce_ins_code(c.get("underlying_ins_code")),
                        underlying_symbol=c.get("underlying_symbol"),
                        underlying_short_name=c.get("underlying_short_name"),
                        underlying_last_price=c.get("underlying_last_price"),
                        underlying_closing_price=c.get("underlying_closing_price"),
                        moneyness=c.get("moneyness"),
                        intrinsic_value=c.get("intrinsic_value"),
                        begin_date=c.get("begin_date"),
                        end_date=c.get("end_date"),
                        a_factor=c.get("a_factor"),
                        b_factor=c.get("b_factor"),
                        c_factor=c.get("c_factor"),
                        market_name=c.get("market_name"),
                        sector=c.get("sector"),
                        last_price=c.get("last_price"),
                        closing_price=c.get("closing_price"),
                        price_change=c.get("price_change"),
                        trade_volume=c.get("trade_volume"),
                        trade_value=c.get("trade_value"),
                        trade_count=c.get("trade_count"),
                        price_min=c.get("price_min"),
                        price_max=c.get("price_max"),
                        instrument_json=json.dumps(instrument_meta, ensure_ascii=False)
                        if instrument_meta
                        else c.get("instrument_json"),
                        fetched_at=now,
                        updated_at=now,
                    )
                )
                count += 1
            session.commit()
        return count

    def insert_open_interest(self, rows: List[Dict[str, Any]]) -> int:
        now = self.now()
        market_date = datetime.strptime(self.snapshot_date_for(now), "%Y-%m-%d").date()
        day_start = (
            datetime.combine(market_date, time.min, tzinfo=MARKET_TZ)
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )
        day_end = (
            datetime.combine(market_date + timedelta(days=1), time.min, tzinfo=MARKET_TZ)
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )
        snapshots: List[OpenInterestSnapshot] = []
        with self.session() as session:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ins_code = self._coerce_ins_code(row.get("ins_code"))
                if not ins_code:
                    continue
                snapshots.append(
                    OpenInterestSnapshot(
                        ins_code=ins_code,
                        buy_open_positions=row.get("buy_open_positions"),
                        sell_open_positions=row.get("sell_open_positions"),
                        yesterday_open_positions=row.get("yesterday_open_positions"),
                        fetched_at=now,
                    )
                )
            if not snapshots:
                return 0
            ins_codes = {snapshot.ins_code for snapshot in snapshots}
            session.execute(
                delete(OpenInterestSnapshot).where(
                    OpenInterestSnapshot.ins_code.in_(ins_codes),
                    OpenInterestSnapshot.fetched_at >= day_start,
                    OpenInterestSnapshot.fetched_at < day_end,
                )
            )
            session.add_all(snapshots)
            session.commit()
        return len(snapshots)

    def insert_money_flow(self, rows: List[Dict[str, Any]]) -> int:
        now = self.now()
        count = 0
        with self.session() as session:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ins_code = self._coerce_ins_code(row.get("ins_code"))
                if not ins_code:
                    continue
                session.add(
                    MoneyFlowSnapshot(
                        ins_code=ins_code,
                        rec_date=self._coerce_rec_date(row.get("rec_date")),
                        natural_money_flow=row.get("natural_money_flow"),
                        legal_money_flow=row.get("legal_money_flow"),
                        fetched_at=now,
                    )
                )
                count += 1
            session.commit()
        return count

    def insert_client_type_stats(self, rows: List[Dict[str, Any]]) -> int:
        now = self.now()
        count = 0
        with self.session() as session:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ins_code = self._coerce_ins_code(row.get("ins_code"))
                if not ins_code:
                    continue
                session.add(
                    ClientTypeStats(
                        ins_code=ins_code,
                        rec_date=self._coerce_rec_date(row.get("rec_date")),
                        natural_buy_volume=row.get("natural_buy_volume"),
                        natural_buy_value=row.get("natural_buy_value"),
                        natural_buy_count=row.get("natural_buy_count"),
                        natural_sell_volume=row.get("natural_sell_volume"),
                        natural_sell_value=row.get("natural_sell_value"),
                        natural_sell_count=row.get("natural_sell_count"),
                        legal_buy_volume=row.get("legal_buy_volume"),
                        legal_buy_value=row.get("legal_buy_value"),
                        legal_buy_count=row.get("legal_buy_count"),
                        legal_sell_volume=row.get("legal_sell_volume"),
                        legal_sell_value=row.get("legal_sell_value"),
                        legal_sell_count=row.get("legal_sell_count"),
                        natural_money_flow=row.get("natural_money_flow"),
                        legal_money_flow=row.get("legal_money_flow"),
                        fetched_at=now,
                    )
                )
                count += 1
            session.commit()
        return count

    def get_contracts_df(self, snapshot_date: Optional[str] = None) -> pd.DataFrame:
        if snapshot_date:
            return self.get_contract_snapshot_df(_normalize_date_text(snapshot_date))
        with self.session() as session:
            rows = session.scalars(select(Contract)).all()
            if not rows:
                return pd.DataFrame()
            return self._contract_frame(rows)

    def get_contract_snapshot_df(self, snapshot_date: Optional[str] = None) -> pd.DataFrame:
        with self.session() as session:
            if snapshot_date is None:
                snapshot_date = self.get_latest_snapshot_date()
            else:
                snapshot_date = _normalize_date_text(snapshot_date)
            if snapshot_date is None:
                return pd.DataFrame()
            # Refreshes may cover different watchlists on the same date. Keep
            # the latest complete batch for each underlying, rather than
            # discarding other assets whenever one asset is refreshed.
            asset_key = func.coalesce(
                cast(ContractSnapshot.underlying_ins_code, String),
                ContractSnapshot.underlying_symbol,
                ContractSnapshot.underlying_short_name,
                cast(ContractSnapshot.ins_code, String),
            )
            latest_batches = (
                select(asset_key.label("asset_key"), func.max(ContractSnapshot.fetched_at).label("fetched_at"))
                .where(ContractSnapshot.snapshot_date == snapshot_date)
                .group_by(asset_key)
                .subquery()
            )
            rows = session.scalars(
                select(ContractSnapshot)
                .join(
                    latest_batches,
                    (asset_key == latest_batches.c.asset_key)
                    & (ContractSnapshot.fetched_at == latest_batches.c.fetched_at),
                )
                .where(ContractSnapshot.snapshot_date == snapshot_date)
                .order_by(ContractSnapshot.ins_code, ContractSnapshot.id)
            ).all()
            if not rows:
                return pd.DataFrame()
            df = self._contract_frame(rows)
            if "ins_code" in df.columns:
                df = df.drop_duplicates("ins_code", keep="last").reset_index(drop=True)
            return df

    def get_available_snapshot_dates(self) -> List[str]:
        with self.session() as session:
            rows = session.scalars(
                select(ContractSnapshot.snapshot_date)
                .distinct()
                .order_by(ContractSnapshot.snapshot_date.desc())
            ).all()
            return [row for row in rows if row]

    def get_latest_snapshot_date(self) -> Optional[str]:
        dates = self.get_available_snapshot_dates()
        return dates[0] if dates else None

    def has_contract_snapshot_date(self, snapshot_date: str) -> bool:
        snapshot_date = _normalize_date_text(snapshot_date)
        with self.session() as session:
            count = session.scalar(
                select(func.count(ContractSnapshot.id)).where(
                    ContractSnapshot.snapshot_date == snapshot_date
                )
            )
            return bool(count)

    def has_underlying_snapshot_key(self, underlying_key: str) -> bool:
        key = str(underlying_key).strip()
        if not key:
            return False
        numeric_key = _clean_numeric_text(key)
        with self.session() as session:
            if numeric_key.isdigit():
                count = session.scalar(
                    select(func.count(ContractSnapshot.id)).where(
                        ContractSnapshot.underlying_ins_code == int(numeric_key)
                    )
                )
                if count:
                    return True
                rows = session.scalars(select(ContractSnapshot.underlying_ins_code)).all()
                return any(self._coerce_ins_code(row) == int(numeric_key) for row in rows)
            count = session.scalar(
                select(func.count(ContractSnapshot.id)).where(
                    (ContractSnapshot.underlying_symbol == key)
                    | (ContractSnapshot.underlying_short_name == key)
                )
            )
            if count:
                return True
            normalized_key = _normalize_lookup_text(key)
            if not normalized_key:
                return False
            rows = session.execute(
                select(
                    ContractSnapshot.underlying_symbol,
                    ContractSnapshot.underlying_short_name,
                )
            ).all()
            return any(
                normalized_key
                in {
                    _normalize_lookup_text(symbol),
                    _normalize_lookup_text(short_name),
                }
                for symbol, short_name in rows
            )

    def get_contract_metadata_by_ins_code(self) -> Dict[int, Dict[str, Any]]:
        df = self.get_contracts_df()
        if df.empty or "ins_code" not in df.columns:
            return {}
        metadata: Dict[int, Dict[str, Any]] = {}
        for row in df.to_dict(orient="records"):
            ins_code = self._coerce_ins_code(row.get("ins_code"))
            if ins_code:
                metadata[ins_code] = row
        return metadata

    def get_latest_client_type_df(self, snapshot_date: Optional[str] = None) -> pd.DataFrame:
        with self.session() as session:
            stmt = select(ClientTypeStats)
            if snapshot_date:
                snapshot_date = _normalize_date_text(snapshot_date)
                api_date = int(snapshot_date.replace("-", ""))
                start = datetime.combine(
                    datetime.strptime(snapshot_date, "%Y-%m-%d").date(),
                    time.min,
                    tzinfo=MARKET_TZ,
                ).astimezone(timezone.utc)
                start = start.replace(tzinfo=None)
                end = start + timedelta(days=1)
                is_current_market_day = snapshot_date == self.snapshot_date_for(self.now())
                fetched_count = 0
                if is_current_market_day:
                    fetched_count = session.scalar(
                        select(func.count(ClientTypeStats.id)).where(
                            ClientTypeStats.fetched_at >= start,
                            ClientTypeStats.fetched_at < end,
                        )
                    ) or 0
                rec_date_count = session.scalar(
                    select(func.count(ClientTypeStats.id)).where(
                        ClientTypeStats.rec_date == api_date
                    )
                )
                if fetched_count:
                    stmt = stmt.where(
                        ClientTypeStats.fetched_at >= start,
                        ClientTypeStats.fetched_at < end,
                    )
                    prefer_fetched_at = True
                elif rec_date_count:
                    stmt = stmt.where(ClientTypeStats.rec_date == api_date)
                    prefer_fetched_at = False
                else:
                    legacy_rows = session.scalars(
                        select(ClientTypeStats).order_by(ClientTypeStats.ins_code)
                    ).all()
                    legacy_df = pd.DataFrame([self._client_type_to_dict(r) for r in legacy_rows])
                    if not legacy_df.empty and "rec_date" in legacy_df.columns:
                        legacy_df = legacy_df[
                            legacy_df["rec_date"].map(self._coerce_rec_date) == api_date
                        ]
                        if not legacy_df.empty:
                            return self._latest_client_type_rows(legacy_df)
                    stmt = stmt.where(
                        ClientTypeStats.fetched_at >= start,
                        ClientTypeStats.fetched_at < end,
                    )
                    prefer_fetched_at = True
            else:
                prefer_fetched_at = False
            rows = session.scalars(stmt.order_by(ClientTypeStats.ins_code)).all()
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame([self._client_type_to_dict(r) for r in rows])
            return self._latest_client_type_rows(df, prefer_fetched_at=prefer_fetched_at)

    def get_open_interest_history_df(
        self,
        ins_code: Optional[int] = None,
        through_date: Optional[str] = None,
    ) -> pd.DataFrame:
        with self.session() as session:
            stmt = select(OpenInterestSnapshot)
            if ins_code is not None:
                stmt = stmt.where(OpenInterestSnapshot.ins_code == ins_code)
            if through_date:
                through_date = _normalize_date_text(through_date)
                through_end = (
                    datetime.combine(
                        datetime.strptime(through_date, "%Y-%m-%d").date(),
                        time.max,
                        tzinfo=MARKET_TZ,
                    )
                    .astimezone(timezone.utc)
                    .replace(tzinfo=None)
                )
                stmt = stmt.where(OpenInterestSnapshot.fetched_at <= through_end)
            rows = session.scalars(
                stmt.order_by(OpenInterestSnapshot.ins_code, OpenInterestSnapshot.fetched_at)
            ).all()
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(
                [
                    {
                        "ins_code": r.ins_code,
                        "buy_open_positions": r.buy_open_positions,
                        "sell_open_positions": r.sell_open_positions,
                        "yesterday_open_positions": r.yesterday_open_positions,
                        "fetched_at": r.fetched_at,
                    }
                    for r in rows
                ]
            )
            # Multiple refreshes can happen during the same market day. History
            # consumers should see the latest market-day snapshot, not treat an
            # earlier refresh from the same day as the previous day.
            df["_market_date"] = df["fetched_at"].map(self.snapshot_date_for)
            df = df.drop_duplicates(subset=["ins_code", "_market_date"], keep="last")
            return df.drop(columns=["_market_date"]).reset_index(drop=True)

    def _backfill_current_contract_snapshot(self) -> None:
        with self.session() as session:
            # Serialize the migration check with cleanup, including another
            # Storage instance opening the same database during a refresh.
            session.execute(text("BEGIN IMMEDIATE"))
            if session.get(AppState, _HISTORY_RETENTION_KEY) is not None:
                return
            snapshot_exists = session.scalar(select(func.count(ContractSnapshot.id))) or 0
            if snapshot_exists:
                return
            contracts = session.scalars(select(Contract)).all()
            if not contracts:
                return
            snapshot_date = self.snapshot_date_for(max((c.updated_at for c in contracts if c.updated_at), default=self.now()))
            for contract in contracts:
                payload = self._contract_to_dict(contract)
                session.add(
                    ContractSnapshot(
                        snapshot_date=snapshot_date,
                        ins_code=contract.ins_code,
                        instrument_id=payload.get("instrument_id"),
                        option_type=payload.get("option_type"),
                        symbol=payload.get("symbol"),
                        short_name=payload.get("short_name"),
                        long_name=payload.get("long_name"),
                        isin=payload.get("isin"),
                        buy_open_positions=payload.get("buy_open_positions"),
                        sell_open_positions=payload.get("sell_open_positions"),
                        yesterday_open_positions=payload.get("yesterday_open_positions"),
                        contract_size=payload.get("contract_size"),
                        strike_price=payload.get("strike_price"),
                        underlying_ins_code=payload.get("underlying_ins_code"),
                        underlying_symbol=payload.get("underlying_symbol"),
                        underlying_short_name=payload.get("underlying_short_name"),
                        underlying_last_price=payload.get("underlying_last_price"),
                        underlying_closing_price=payload.get("underlying_closing_price"),
                        moneyness=payload.get("moneyness"),
                        intrinsic_value=payload.get("intrinsic_value"),
                        begin_date=payload.get("begin_date"),
                        end_date=payload.get("end_date"),
                        a_factor=payload.get("a_factor"),
                        b_factor=payload.get("b_factor"),
                        c_factor=payload.get("c_factor"),
                        market_name=payload.get("market_name"),
                        sector=payload.get("sector"),
                        last_price=payload.get("last_price"),
                        closing_price=payload.get("closing_price"),
                        price_change=payload.get("price_change"),
                        trade_volume=payload.get("trade_volume"),
                        trade_value=payload.get("trade_value"),
                        trade_count=payload.get("trade_count"),
                        price_min=payload.get("price_min"),
                        price_max=payload.get("price_max"),
                        fetched_at=payload.get("fetched_at") or self.now(),
                        updated_at=payload.get("updated_at") or self.now(),
                    )
                )
            session.commit()

    @staticmethod
    def _coerce_ins_code(value: Any) -> Optional[int]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            ins_code = value
            return ins_code if ins_code > 0 else None
        if isinstance(value, float):
            if not pd.notna(value) or not value.is_integer():
                return None
            ins_code = int(value)
            return ins_code if ins_code > 0 else None
        if isinstance(value, str):
            value = _clean_numeric_text(value)
            if not value:
                return None
        try:
            ins_code = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return ins_code if ins_code > 0 else None

    @staticmethod
    def _coerce_rec_date(value: Any) -> Optional[int]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value > 0 else None
        if isinstance(value, float):
            if not pd.notna(value) or not value.is_integer():
                return None
            rec_date = int(value)
            return rec_date if rec_date > 0 else None
        if isinstance(value, str):
            value = _clean_date_number_text(value)
            if not value:
                return None
        try:
            rec_date = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return rec_date if rec_date > 0 else None

    @classmethod
    def _latest_client_type_rows(cls, df: pd.DataFrame, prefer_fetched_at: bool = False) -> pd.DataFrame:
        if df.empty or "ins_code" not in df.columns:
            return df
        df = df.copy()
        df["_rec_date_key"] = df.get("rec_date", pd.Series(index=df.index, dtype=object)).map(cls._coerce_rec_date)
        if prefer_fetched_at and "fetched_at" in df.columns:
            return (
                df.sort_values(["ins_code", "fetched_at"], na_position="first")
                .drop_duplicates("ins_code", keep="last")
                .drop(columns=["_rec_date_key"], errors="ignore")
            )
        if "fetched_at" not in df.columns:
            return (
                df.sort_values(["ins_code", "_rec_date_key"], na_position="first")
                .drop_duplicates("ins_code", keep="last")
                .drop(columns=["_rec_date_key"], errors="ignore")
            )
        return (
            df.sort_values(["ins_code", "_rec_date_key", "fetched_at"], na_position="first")
            .drop_duplicates("ins_code", keep="last")
            .drop(columns=["_rec_date_key"], errors="ignore")
        )

    @classmethod
    def _contract_frame(cls, rows: list[Contract | ContractSnapshot]) -> pd.DataFrame:
        records = [cls._contract_to_dict(row) for row in rows]
        frame = pd.DataFrame(records)
        # Nullable 17-digit IDs must never pass through float64 inference.
        for key in ("ins_code", "underlying_ins_code"):
            frame[key] = pd.Series([record[key] for record in records], dtype=object)
        return frame

    @staticmethod
    def _contract_to_dict(r: Contract | ContractSnapshot) -> Dict[str, Any]:
        return {
            "ins_code": r.ins_code,
            "instrument_id": r.instrument_id,
            "option_type": r.option_type,
            "symbol": r.symbol,
            "short_name": r.short_name,
            "long_name": r.long_name,
            "isin": r.isin,
            "buy_open_positions": r.buy_open_positions,
            "sell_open_positions": r.sell_open_positions,
            "yesterday_open_positions": r.yesterday_open_positions,
            "contract_size": r.contract_size,
            "strike_price": r.strike_price,
            "underlying_ins_code": r.underlying_ins_code,
            "underlying_symbol": r.underlying_symbol,
            "underlying_short_name": r.underlying_short_name,
            "underlying_last_price": r.underlying_last_price,
            "underlying_closing_price": r.underlying_closing_price,
            "moneyness": r.moneyness,
            "intrinsic_value": r.intrinsic_value,
            "begin_date": r.begin_date,
            "end_date": r.end_date,
            "a_factor": r.a_factor,
            "b_factor": r.b_factor,
            "c_factor": r.c_factor,
            "market_name": r.market_name,
            "sector": r.sector,
            "last_price": r.last_price,
            "closing_price": r.closing_price,
            "price_change": r.price_change,
            "trade_volume": r.trade_volume,
            "trade_value": r.trade_value,
            "trade_count": r.trade_count,
            "price_min": r.price_min,
            "price_max": r.price_max,
            "fetched_at": r.fetched_at,
            "updated_at": r.updated_at,
        }

    @staticmethod
    def _client_type_to_dict(r: ClientTypeStats) -> Dict[str, Any]:
        return {
            "ins_code": r.ins_code,
            "rec_date": r.rec_date,
            "natural_buy_volume": r.natural_buy_volume,
            "natural_buy_value": r.natural_buy_value,
            "natural_buy_count": r.natural_buy_count,
            "natural_sell_volume": r.natural_sell_volume,
            "natural_sell_value": r.natural_sell_value,
            "natural_sell_count": r.natural_sell_count,
            "legal_buy_volume": r.legal_buy_volume,
            "legal_buy_value": r.legal_buy_value,
            "legal_buy_count": r.legal_buy_count,
            "legal_sell_volume": r.legal_sell_volume,
            "legal_sell_value": r.legal_sell_value,
            "legal_sell_count": r.legal_sell_count,
            "natural_money_flow": r.natural_money_flow,
            "legal_money_flow": r.legal_money_flow,
            "fetched_at": r.fetched_at,
        }


def _normalize_lookup_text(value: Any) -> str:
    if value is None:
        return ""
    return (
        str(value)
        .strip()
        .lower()
        .replace("ي", "ی")
        .replace("ك", "ک")
        .replace("\u200c", "")
    )


def _clean_numeric_text(value: str) -> str:
    return (
        value.strip()
        .replace(",", "")
        .replace("٬", "")
        .replace("،", "")
        .replace(" ", "")
    )


def _clean_date_number_text(value: str) -> str:
    return _clean_numeric_text(value).replace("-", "").replace("/", "")


def _normalize_date_text(value: str) -> str:
    return value.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
