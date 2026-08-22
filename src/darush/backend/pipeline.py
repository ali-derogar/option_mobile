"""Main data pipeline: fetch, store, and export TSETMC options data."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Set

from darush.backend.client import TsetmcClient, TsetmcAPIError
from darush.backend.config import TSETMC_FLOW, validate_credentials
from darush.backend.services.client_type import (
    fetch_client_type_by_ins,
    normalize_client_type,
)
from darush.backend.services.instruments import fetch_instruments, index_by_ins_code
from darush.backend.services.options import (
    enrich_with_underlying,
    enrich_with_instrument,
    fetch_all_options,
    normalize_option,
)
from darush.backend.services.public_options import (
    fetch_public_client_type_latest_many,
    fetch_public_option_market_watch,
    normalize_public_option_pairs,
)
from darush.backend.services.trades import fetch_trade_last_day, filter_for_ins_codes, normalize_trade
from darush.backend.storage import Storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _merge_trade(enriched: Dict[str, Any], trade: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not trade:
        return enriched
    enriched.update(
        {
            "last_price": trade.get("last_price"),
            "closing_price": trade.get("closing_price"),
            "price_change": trade.get("price_change"),
            "trade_volume": trade.get("volume"),
            "trade_value": trade.get("value"),
            "trade_count": trade.get("trade_count"),
            "price_min": trade.get("price_min"),
            "price_max": trade.get("price_max"),
        }
    )
    return enriched


def _store_public_option_fallback(
    storage: Storage,
    skip_client_type: bool = False,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    def progress(**payload: Any) -> None:
        if progress_callback:
            progress_callback(payload)

    logger.info("Fetching public option market watch from TSETMC CDN...")
    progress(stage="public_options", message="در حال دریافت آپشن‌ها از CDN رسمی TSETMC")
    rows = fetch_public_option_market_watch()
    contracts = normalize_public_option_pairs(rows)
    open_interest_rows = [
        {
            "ins_code": c["ins_code"],
            "buy_open_positions": c.get("buy_open_positions"),
            "sell_open_positions": c.get("sell_open_positions"),
            "yesterday_open_positions": c.get("yesterday_open_positions"),
        }
        for c in contracts
    ]

    storage.upsert_contracts(contracts)
    storage.insert_contract_snapshot(contracts)
    storage.insert_open_interest(open_interest_rows)
    progress(
        stage="contracts_stored",
        message=f"{len(contracts)} قرارداد از CDN رسمی ذخیره شد",
        options_stored=len(contracts),
    )

    client_type_rows: List[Dict[str, Any]] = []
    if not skip_client_type:
        ins_codes = [c["ins_code"] for c in contracts if c.get("ins_code")]
        progress(
            stage="public_client_type",
            message=f"در حال دریافت حقیقی/حقوقی عمومی برای {len(ins_codes)} قرارداد",
            client_type_total=len(ins_codes),
        )
        client_type_rows = fetch_public_client_type_latest_many(ins_codes)
        if client_type_rows:
            storage.insert_client_type_stats(client_type_rows)
            storage.insert_money_flow(client_type_rows)
        progress(
            stage="client_type_stored",
            message=f"{len(client_type_rows)} رکورد حقیقی/حقوقی عمومی ذخیره شد",
            client_type_records=len(client_type_rows),
        )

    export_paths = storage.export_csv(prefix="public")
    return {
        "options": len(contracts),
        "client_type": len(client_type_rows),
        "source": "public_cdn",
        "warning": None if client_type_rows else "داده حقیقی/حقوقی عمومی برای قراردادها دریافت نشد.",
        "exports": {k: str(v) for k, v in export_paths.items()},
    }


def run_pipeline(
    limit: Optional[int] = None,
    skip_client_type: bool = False,
    delay_between_calls: float = 0.1,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """
    Execute full pipeline:
    1. Open positions per option
    2. Money flow (natural/legal) per contract
    3. Full client type buy/sell numbers
    4. All contract information
    """
    validate_credentials()
    client = TsetmcClient()
    storage = Storage()

    def progress(**payload: Any) -> None:
        if progress_callback:
            progress_callback(payload)

    logger.info("Logging in to TSETMC API...")
    progress(stage="login", message="در حال ورود به TSETMC")
    try:
        client.login()
    except TsetmcAPIError as exc:
        logger.warning("Authenticated API login failed, falling back to public CDN: %s", exc)
        progress(
            stage="login_failed",
            message=f"ورود رسمی ناموفق بود؛ استفاده از CDN رسمی TSETMC ({exc})",
        )
        return _store_public_option_fallback(
            storage,
            skip_client_type=skip_client_type,
            progress_callback=progress_callback,
        )

    logger.info("Fetching all option contracts...")
    progress(stage="options", message="در حال دریافت قراردادهای اختیار معامله")
    raw_options = fetch_all_options(client)
    logger.info("Received %d option contracts", len(raw_options))
    progress(stage="options", message=f"{len(raw_options)} قرارداد از TSETMC دریافت شد", options_received=len(raw_options))

    if not raw_options:
        logger.warning("No option data returned. Check credentials and API access.")
        return {"options": 0, "client_type": 0, "exports": {}}

    option_ins_codes: Set[int] = set()
    normalized_options: List[Dict[str, Any]] = []
    for row in raw_options:
        opt = normalize_option(row)
        if opt["ins_code"]:
            option_ins_codes.add(opt["ins_code"])
            normalized_options.append(opt)

    if limit:
        option_ins_codes = set(list(option_ins_codes)[:limit])
        normalized_options = [o for o in normalized_options if o["ins_code"] in option_ins_codes]

    logger.info("Fetching instrument metadata (Flow=%s)...", TSETMC_FLOW)
    progress(stage="instruments", message="در حال دریافت مشخصات نمادها")
    instruments = fetch_instruments(client, flow=TSETMC_FLOW)
    inst_index = index_by_ins_code(instruments)
    logger.info("Received %d instruments for flow %s", len(instruments), TSETMC_FLOW)

    # Also index underlying assets from all flows if missing
    missing_underlying = {
        o["underlying_ins_code"]
        for o in normalized_options
        if o.get("underlying_ins_code") and o["underlying_ins_code"] not in inst_index
    }
    if missing_underlying:
        logger.info("Fetching general instrument list for %d underlying assets...", len(missing_underlying))
        try:
            all_instruments = fetch_instruments(client, flow=0)
            inst_index.update(index_by_ins_code(all_instruments))
        except TsetmcAPIError as exc:
            logger.warning("Could not fetch general instruments: %s", exc)

    logger.info("Fetching last day trades (Flow=%s)...", TSETMC_FLOW)
    progress(stage="trades", message="در حال دریافت معاملات روز")
    raw_trades = fetch_trade_last_day(client, flow=TSETMC_FLOW)
    option_trades = filter_for_ins_codes(raw_trades, option_ins_codes)
    trade_index = {
        normalize_trade(t)["ins_code"]: normalize_trade(t) for t in option_trades
    }
    logger.info("Matched %d option trades from TradeLastDay", len(trade_index))

    underlying_ins_codes = {
        o["underlying_ins_code"]
        for o in normalized_options
        if o.get("underlying_ins_code")
    }
    underlying_trade_index: Dict[int, Dict[str, Any]] = {}
    if underlying_ins_codes:
        logger.info("Fetching underlying last day trades for %d assets...", len(underlying_ins_codes))
        try:
            raw_underlying_trades = fetch_trade_last_day(client, flow=0)
            underlying_trades = filter_for_ins_codes(raw_underlying_trades, underlying_ins_codes)
            underlying_trade_index = {
                normalize_trade(t)["ins_code"]: normalize_trade(t) for t in underlying_trades
            }
            logger.info("Matched %d underlying trades", len(underlying_trade_index))
        except TsetmcAPIError as exc:
            logger.warning("Could not fetch underlying trades: %s", exc)

    enriched_contracts: List[Dict[str, Any]] = []
    open_interest_rows: List[Dict[str, Any]] = []

    for opt in normalized_options:
        inst = inst_index.get(opt["ins_code"])
        enriched = enrich_with_instrument(opt, inst)
        enriched = _merge_trade(enriched, trade_index.get(opt["ins_code"]))
        enriched = enrich_with_underlying(
            enriched,
            inst_index.get(opt.get("underlying_ins_code")),
            underlying_trade_index.get(opt.get("underlying_ins_code")),
        )
        enriched_contracts.append(enriched)
        open_interest_rows.append(
            {
                "ins_code": opt["ins_code"],
                "buy_open_positions": opt["buy_open_positions"],
                "sell_open_positions": opt["sell_open_positions"],
                "yesterday_open_positions": opt["yesterday_open_positions"],
            }
        )

    storage.upsert_contracts(enriched_contracts)
    storage.insert_contract_snapshot(enriched_contracts)
    storage.insert_open_interest(open_interest_rows)
    logger.info("Stored %d contracts and open interest snapshots", len(enriched_contracts))
    progress(
        stage="contracts_stored",
        message=f"{len(enriched_contracts)} قرارداد ذخیره شد",
        options_stored=len(enriched_contracts),
    )

    client_type_rows: List[Dict[str, Any]] = []
    if not skip_client_type:
        total = len(option_ins_codes)
        logger.info("Fetching client type data for %d contracts...", total)
        progress(
            stage="client_type",
            message=f"در حال دریافت حقیقی/حقوقی 0/{total}",
            client_type_done=0,
            client_type_total=total,
        )
        for i, ins_code in enumerate(sorted(option_ins_codes), 1):
            try:
                raw_ct = fetch_client_type_by_ins(client, ins_code)
                for row in raw_ct:
                    normalized = normalize_client_type(row)
                    if normalized["ins_code"]:
                        client_type_rows.append(normalized)
                if i % 50 == 0 or i == total:
                    logger.info("Client type progress: %d/%d", i, total)
                    progress(
                        stage="client_type",
                        message=f"در حال دریافت حقیقی/حقوقی {i}/{total}",
                        client_type_done=i,
                        client_type_total=total,
                    )
                if delay_between_calls > 0:
                    time.sleep(delay_between_calls)
            except TsetmcAPIError as exc:
                logger.warning("ClientType failed for ins_code=%s: %s", ins_code, exc)

        storage.insert_client_type_stats(client_type_rows)
        storage.insert_money_flow(client_type_rows)
        logger.info("Stored %d client type records", len(client_type_rows))
        progress(
            stage="client_type_stored",
            message=f"{len(client_type_rows)} رکورد حقیقی/حقوقی ذخیره شد",
            client_type_records=len(client_type_rows),
        )

    progress(stage="export", message="در حال ساخت خروجی CSV")
    export_paths = storage.export_csv()
    logger.info("Pipeline complete. Exports: %s", export_paths)

    return {
        "options": len(enriched_contracts),
        "client_type": len(client_type_rows),
        "exports": {k: str(v) for k, v in export_paths.items()},
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="TSETMC Options Data Pipeline")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of option contracts (for testing)",
    )
    parser.add_argument(
        "--skip-client-type",
        action="store_true",
        help="Skip per-contract client type API calls",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.1,
        help="Delay between ClientTypeByIns calls (seconds)",
    )
    args = parser.parse_args(argv)

    try:
        result = run_pipeline(
            limit=args.limit,
            skip_client_type=args.skip_client_type,
            delay_between_calls=args.delay,
        )
        print("Pipeline result:", result)
        return 0
    except (TsetmcAPIError, ValueError) as exc:
        logger.error("Pipeline failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
