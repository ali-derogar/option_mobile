"""SQLite storage and CSV export."""

from __future__ import annotations

import json
import logging
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from options.backend.config import DATA_DIR, DATABASE_PATH

logger = logging.getLogger(__name__)
MARKET_TZ = ZoneInfo("Asia/Tehran")


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


class Storage:
    def __init__(self, db_path: Optional[Path] = None, export_dir: Optional[Path] = None):
        self.db_path = db_path or DATABASE_PATH
        self.export_dir = export_dir or DATA_DIR
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        Base.metadata.create_all(self.engine)
        self._ensure_schema()
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self._backfill_current_contract_snapshot()

    def session(self) -> Session:
        return self.SessionLocal()

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

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
            for table_name in ("contracts", "contract_snapshots"):
                if table_name not in tables:
                    continue
                existing = {col["name"] for col in inspector.get_columns(table_name)}
                missing = [(name, sql_type) for name, sql_type in columns.items() if name not in existing]
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
        count = 0
        with self.session() as session:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ins_code = self._coerce_ins_code(row.get("ins_code"))
                if not ins_code:
                    continue
                session.add(
                    OpenInterestSnapshot(
                        ins_code=ins_code,
                        buy_open_positions=row.get("buy_open_positions"),
                        sell_open_positions=row.get("sell_open_positions"),
                        yesterday_open_positions=row.get("yesterday_open_positions"),
                        fetched_at=now,
                    )
                )
                count += 1
            session.commit()
        return count

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
            return pd.DataFrame([self._contract_to_dict(r) for r in rows])

    def get_contract_snapshot_df(self, snapshot_date: Optional[str] = None) -> pd.DataFrame:
        with self.session() as session:
            if snapshot_date is None:
                snapshot_date = self.get_latest_snapshot_date()
            else:
                snapshot_date = _normalize_date_text(snapshot_date)
            if snapshot_date is None:
                return pd.DataFrame()
            latest_fetched_at = session.scalar(
                select(func.max(ContractSnapshot.fetched_at)).where(
                    ContractSnapshot.snapshot_date == snapshot_date
                )
            )
            if latest_fetched_at is None:
                return pd.DataFrame()
            rows = session.scalars(
                select(ContractSnapshot)
                .where(
                    ContractSnapshot.snapshot_date == snapshot_date,
                    ContractSnapshot.fetched_at == latest_fetched_at,
                )
                .order_by(ContractSnapshot.ins_code, ContractSnapshot.id)
            ).all()
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame([self._contract_to_dict(r) for r in rows])
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
                rec_date_count = session.scalar(
                    select(func.count(ClientTypeStats.id)).where(
                        ClientTypeStats.rec_date == api_date
                    )
                )
                if rec_date_count:
                    stmt = stmt.where(ClientTypeStats.rec_date == api_date)
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
                    start = datetime.combine(
                        datetime.strptime(snapshot_date, "%Y-%m-%d").date(),
                        time.min,
                        tzinfo=MARKET_TZ,
                    ).astimezone(timezone.utc)
                    start = start.replace(tzinfo=None)
                    end = start + timedelta(days=1)
                    stmt = stmt.where(
                        ClientTypeStats.fetched_at >= start,
                        ClientTypeStats.fetched_at < end,
                    )
            rows = session.scalars(stmt.order_by(ClientTypeStats.ins_code)).all()
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame([self._client_type_to_dict(r) for r in rows])
            return self._latest_client_type_rows(df)

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
            return pd.DataFrame(
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

    def _backfill_current_contract_snapshot(self) -> None:
        with self.session() as session:
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
    def _latest_client_type_rows(cls, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or "ins_code" not in df.columns:
            return df
        df = df.copy()
        df["_rec_date_key"] = df.get("rec_date", pd.Series(index=df.index, dtype=object)).map(cls._coerce_rec_date)
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

    def export_csv(self, prefix: str = "") -> Dict[str, Path]:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = f"{prefix}_" if prefix else ""
        paths: Dict[str, Path] = {}

        contracts_df = self.get_contracts_df()
        if not contracts_df.empty:
            p = self.export_dir / f"{prefix}contracts_{timestamp}.csv"
            contracts_df.to_csv(p, index=False, encoding="utf-8-sig")
            paths["contracts"] = p

        ct_df = self.get_latest_client_type_df()
        if not ct_df.empty:
            p = self.export_dir / f"{prefix}client_type_stats_{timestamp}.csv"
            ct_df.to_csv(p, index=False, encoding="utf-8-sig")
            paths["client_type_stats"] = p

            mf = ct_df[
                ["ins_code", "rec_date", "natural_money_flow", "legal_money_flow", "fetched_at"]
            ].copy()
            p = self.export_dir / f"{prefix}money_flow_{timestamp}.csv"
            mf.to_csv(p, index=False, encoding="utf-8-sig")
            paths["money_flow"] = p

        oi_df = self.get_open_interest_history_df()
        if not oi_df.empty:
            latest_oi = oi_df.sort_values("fetched_at").groupby("ins_code").tail(1)
            p = self.export_dir / f"{prefix}open_interest_{timestamp}.csv"
            latest_oi.to_csv(p, index=False, encoding="utf-8-sig")
            paths["open_interest"] = p

        logger.info("Exported CSV files: %s", list(paths.keys()))
        return paths

    @staticmethod
    def _contract_to_dict(r: Contract) -> Dict[str, Any]:
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
