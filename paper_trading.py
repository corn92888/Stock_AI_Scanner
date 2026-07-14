import argparse
import json
import math
from dataclasses import asdict, dataclass

import pandas as pd

from backtest import BacktestConfig, PriceCache, download_price_data
from database import (
    CANDIDATE_EXECUTION_VERSION,
    DB_PATH,
    PAPER_POLICY_VERSION,
    get_connection,
    get_taipei_now,
    init_db,
)


@dataclass(frozen=True)
class PaperTradingConfig(BacktestConfig):
    policy_version: str = PAPER_POLICY_VERSION
    starting_cash: float = 1_000_000.0
    max_positions: int = 5
    position_size_pct: float = 0.20
    risk_budget_pct: float = 0.01
    cash_buffer_pct: float = 0.05
    min_trade_value: float = 10_000.0
    enforce_chase_limit: bool = True


@dataclass(frozen=True)
class PaperAccountSpec:
    account_key: str
    name: str
    strategy_kind: str
    evidence_mode: str
    source_type: str


ACCOUNT_SPECS = (
    PaperAccountSpec(
        account_key="rule_baseline_v1",
        name="規則基準帳戶",
        strategy_kind="rule",
        evidence_mode="recorded_signal_replay",
        source_type="candidate",
    ),
    PaperAccountSpec(
        account_key="ai_shadow_v1",
        name="AI 影子帳戶",
        strategy_kind="ai",
        evidence_mode="prospective_only",
        source_type="prediction",
    ),
)


TRADE_COLUMNS = (
    "source_type",
    "source_id",
    "candidate_id",
    "prediction_id",
    "signal_date",
    "signal_at",
    "code",
    "name",
    "industry",
    "rank_order",
    "model_version",
    "entry_at",
    "entry_price",
    "entry_fee",
    "quantity",
    "invested_amount",
    "chase_limit",
    "stop_price",
    "exit_at",
    "exit_price",
    "exit_cost",
    "exit_proceeds",
    "exit_reason",
    "net_return_pct",
    "realized_pnl",
    "mark_at",
    "mark_price",
    "market_value",
    "unrealized_pnl",
    "max_return_pct",
    "max_drawdown_pct",
    "status",
    "skip_reason",
)


def load_rule_signals(db_path=DB_PATH):
    with get_connection(db_path) as conn:
        init_db(conn)
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    'candidate' AS source_type,
                    ce.id AS source_id,
                    ce.id AS candidate_id,
                    NULL AS prediction_id,
                    sr.trade_date AS signal_date,
                    ce.as_of AS signal_at,
                    ce.code,
                    ce.name,
                    ce.industry,
                    ce.selection_rank AS rank_order,
                    NULL AS model_version,
                    ce.chase_limit AS raw_chase_limit,
                    ce.observation_price AS raw_stop_price,
                    co.entry_at,
                    co.entry_price,
                    co.entry_adjustment_factor,
                    co.entry_status,
                    co.skip_reason AS outcome_skip_reason,
                    co.exit_at,
                    co.exit_price,
                    co.exit_reason,
                    co.max_return_3d AS max_return_pct,
                    co.max_drawdown_3d AS max_drawdown_pct,
                    co.matured_horizon,
                    co.outcome_status
                FROM candidate_events ce
                JOIN scan_runs sr ON sr.id=ce.run_id
                LEFT JOIN candidate_outcomes co
                  ON co.candidate_id=ce.id AND co.execution_version=?
                WHERE ce.is_selected=1
                ORDER BY sr.trade_date, ce.selection_rank, ce.id
                """,
                (CANDIDATE_EXECUTION_VERSION,),
            ).fetchall()
        ]


def load_ai_signals(db_path=DB_PATH):
    with get_connection(db_path) as conn:
        init_db(conn)
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                    'prediction' AS source_type,
                    p.id AS source_id,
                    ce.id AS candidate_id,
                    p.id AS prediction_id,
                    sr.trade_date AS signal_date,
                    p.predicted_at AS signal_at,
                    p.code,
                    ce.name,
                    ce.industry,
                    p.rank_order,
                    p.model_version,
                    COALESCE(p.chase_limit, ce.chase_limit) AS raw_chase_limit,
                    COALESCE(p.stop_price, ce.observation_price) AS raw_stop_price,
                    co.entry_at,
                    co.entry_price,
                    co.entry_adjustment_factor,
                    co.entry_status,
                    co.skip_reason AS outcome_skip_reason,
                    co.exit_at,
                    co.exit_price,
                    co.exit_reason,
                    co.max_return_3d AS max_return_pct,
                    co.max_drawdown_3d AS max_drawdown_pct,
                    co.matured_horizon,
                    co.outcome_status
                FROM predictions p
                JOIN scan_runs sr ON sr.id=p.run_id
                LEFT JOIN candidate_events ce ON ce.id=(
                    SELECT ce2.id
                    FROM candidate_events ce2
                    WHERE ce2.run_id=p.run_id AND ce2.code=p.code
                    ORDER BY ce2.id DESC
                    LIMIT 1
                )
                LEFT JOIN candidate_outcomes co
                  ON co.candidate_id=ce.id AND co.execution_version=?
                WHERE p.is_prospective=1 AND p.is_selected=1
                  AND p.id=(
                      SELECT p2.id FROM predictions p2
                      WHERE p2.run_id=p.run_id AND p2.code=p.code
                        AND p2.is_prospective=1
                      ORDER BY p2.predicted_at, p2.id
                      LIMIT 1
                  )
                ORDER BY sr.trade_date, p.rank_order, p.id
                """,
                (CANDIDATE_EXECUTION_VERSION,),
            ).fetchall()
        ]


def _safe_number(value, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _normalized_date(value):
    if not value:
        return None
    return pd.Timestamp(value).normalize()


def _adjusted_limit(value, adjustment_factor):
    raw = _safe_number(value)
    if raw is None:
        return None
    return raw * (_safe_number(adjustment_factor, 1.0) or 1.0)


def _price_on_or_before(frame, date, column="Close"):
    if frame is None or frame.empty:
        return None
    eligible = frame[frame.index.normalize() <= pd.Timestamp(date).normalize()]
    if eligible.empty or column not in eligible.columns:
        return None
    return _safe_number(eligible.iloc[-1][column])


def _calendar(price_cache, signals, as_of, benchmark_code):
    dated_values = []
    for signal in signals:
        for key in ("signal_date", "entry_at", "exit_at"):
            value = _normalized_date(signal.get(key))
            if value is not None:
                dated_values.append(value)

    benchmark = price_cache.get_ticker(benchmark_code) if price_cache else None
    if benchmark is not None and not benchmark.empty:
        dates = {
            pd.Timestamp(value).normalize()
            for value in benchmark.index
            if pd.Timestamp(value).normalize() <= as_of
        }
    else:
        start = min(dated_values, default=as_of)
        dates = set(pd.bdate_range(start, as_of).normalize())
    dates.update(value for value in dated_values if value <= as_of)
    dates.add(max((value for value in dated_values if value <= as_of), default=as_of))
    return sorted(dates)


def _base_trade(signal):
    factor = _safe_number(signal.get("entry_adjustment_factor"), 1.0) or 1.0
    return {
        "source_type": signal["source_type"],
        "source_id": int(signal["source_id"]),
        "candidate_id": signal.get("candidate_id"),
        "prediction_id": signal.get("prediction_id"),
        "signal_date": str(signal["signal_date"]),
        "signal_at": signal.get("signal_at") or str(signal["signal_date"]),
        "code": str(signal["code"]),
        "name": signal.get("name") or "",
        "industry": signal.get("industry") or "",
        "rank_order": signal.get("rank_order"),
        "model_version": signal.get("model_version"),
        "entry_at": signal.get("entry_at"),
        "entry_price": _safe_number(signal.get("entry_price")),
        "entry_status": signal.get("entry_status") or "filled",
        "outcome_skip_reason": signal.get("outcome_skip_reason"),
        "entry_fee": None,
        "quantity": None,
        "invested_amount": None,
        "chase_limit": _adjusted_limit(signal.get("raw_chase_limit"), factor),
        "stop_price": _adjusted_limit(signal.get("raw_stop_price"), factor),
        "exit_at": signal.get("exit_at"),
        "exit_price": _safe_number(signal.get("exit_price")),
        "exit_cost": None,
        "exit_proceeds": None,
        "exit_reason": signal.get("exit_reason"),
        "net_return_pct": None,
        "realized_pnl": None,
        "mark_at": None,
        "mark_price": None,
        "market_value": None,
        "unrealized_pnl": None,
        "max_return_pct": _safe_number(signal.get("max_return_pct")),
        "max_drawdown_pct": _safe_number(signal.get("max_drawdown_pct")),
        "status": "pending",
        "skip_reason": None,
    }


def simulate_account(spec, signals, config=None, price_cache=None, as_of=None):
    config = config or PaperTradingConfig()
    as_of = _normalized_date(as_of or get_taipei_now().date())
    signals = sorted(
        (dict(signal) for signal in signals),
        key=lambda row: (
            str(row.get("entry_at") or "9999-12-31"),
            str(row.get("signal_at") or ""),
            int(row.get("rank_order") or 9999),
            int(row["source_id"]),
        ),
    )
    trades = [_base_trade(signal) for signal in signals]
    signal_by_id = {int(signal["source_id"]): signal for signal in signals}
    entries = {}
    exits = {}
    signal_dates = [
        value
        for signal in signals
        if (value := _normalized_date(signal.get("signal_date"))) is not None
    ]
    latest_signal_date = max(
        signal_dates,
        default=as_of,
    )

    for trade in trades:
        if trade["entry_status"] == "skipped" or trade["outcome_skip_reason"]:
            trade["status"] = "skipped"
            trade["skip_reason"] = trade["outcome_skip_reason"] or "execution_rejected"
            continue
        entry_at = _normalized_date(trade["entry_at"])
        if entry_at is None or trade["entry_price"] is None:
            signal_date = _normalized_date(trade["signal_date"])
            trade["skip_reason"] = (
                "awaiting_next_open"
                if signal_date is not None and signal_date >= latest_signal_date
                else "outcome_backfill_pending"
            )
            continue
        if entry_at > as_of:
            trade["skip_reason"] = "awaiting_next_open"
            continue
        entries.setdefault(entry_at, []).append(trade)
        exit_at = _normalized_date(trade["exit_at"])
        if exit_at is not None and exit_at <= as_of:
            exits.setdefault(exit_at, []).append(trade)

    calendar = _calendar(price_cache, signals, as_of, config.benchmark_code)
    cash = float(config.starting_cash)
    open_positions = {}
    snapshots = []
    peak_equity = cash
    max_drawdown = 0.0
    closed_count = 0

    def liquidation_value(trade, date):
        frame = price_cache.get_stock(trade["code"]) if price_cache else None
        mark_price = _price_on_or_before(frame, date)
        if mark_price is None:
            mark_price = trade["entry_price"]
        unit_proceeds = mark_price * (
            1 - config.sell_fee_rate - config.sell_tax_rate - config.slippage_rate
        )
        return mark_price, max(0.0, unit_proceeds * int(trade["quantity"] or 0))

    previous_equity = cash
    for date in calendar:
        day_entries = sorted(
            entries.get(date, []),
            key=lambda trade: (
                int(trade.get("rank_order") or 9999),
                int(trade["source_id"]),
            ),
        )
        sizing_equity = previous_equity
        for trade in day_entries:
            if trade["code"] in open_positions:
                trade["status"] = "skipped"
                trade["skip_reason"] = "duplicate_open_position"
                continue
            if len(open_positions) >= config.max_positions:
                trade["status"] = "skipped"
                trade["skip_reason"] = "max_positions_reached"
                continue
            if (
                config.enforce_chase_limit
                and trade["chase_limit"] is not None
                and trade["entry_price"] > trade["chase_limit"]
            ):
                trade["status"] = "skipped"
                trade["skip_reason"] = "above_chase_limit"
                continue
            if (
                trade["stop_price"] is not None
                and trade["stop_price"] >= trade["entry_price"]
            ):
                trade["status"] = "skipped"
                trade["skip_reason"] = "invalid_stop_at_entry"
                continue

            unit_cost = trade["entry_price"] * (
                1 + config.buy_fee_rate + config.slippage_rate
            )
            cash_reserve = sizing_equity * config.cash_buffer_pct
            risk_spend_limit = sizing_equity * config.position_size_pct
            if trade["stop_price"] is not None and trade["entry_price"] > 0:
                risk_fraction = (trade["entry_price"] - trade["stop_price"]) / trade["entry_price"]
                if risk_fraction > 0:
                    risk_spend_limit = min(
                        risk_spend_limit,
                        sizing_equity * config.risk_budget_pct / risk_fraction,
                    )
            spend_limit = min(
                risk_spend_limit,
                max(0.0, cash - cash_reserve),
            )
            quantity = int(spend_limit // unit_cost) if unit_cost > 0 else 0
            invested_amount = quantity * unit_cost
            if quantity < 1 or invested_amount < config.min_trade_value:
                trade["status"] = "skipped"
                trade["skip_reason"] = "insufficient_cash"
                continue

            gross_entry = trade["entry_price"] * quantity
            trade["quantity"] = quantity
            trade["entry_fee"] = invested_amount - gross_entry
            trade["invested_amount"] = invested_amount
            trade["status"] = "open"
            trade["skip_reason"] = None
            cash -= invested_amount
            open_positions[trade["code"]] = trade

        for trade in exits.get(date, []):
            if trade["status"] != "open" or trade["exit_price"] is None:
                continue
            quantity = int(trade["quantity"] or 0)
            gross_exit = trade["exit_price"] * quantity
            exit_cost = gross_exit * (
                config.sell_fee_rate + config.sell_tax_rate + config.slippage_rate
            )
            exit_proceeds = gross_exit - exit_cost
            realized_pnl = exit_proceeds - float(trade["invested_amount"] or 0)
            net_return = (
                realized_pnl / trade["invested_amount"] * 100
                if trade["invested_amount"]
                else None
            )
            trade["exit_cost"] = exit_cost
            trade["exit_proceeds"] = exit_proceeds
            trade["realized_pnl"] = realized_pnl
            trade["net_return_pct"] = net_return
            trade["market_value"] = 0.0
            trade["unrealized_pnl"] = 0.0
            trade["status"] = "closed"
            cash += exit_proceeds
            open_positions.pop(trade["code"], None)
            closed_count += 1

        market_value = 0.0
        for trade in open_positions.values():
            _, value = liquidation_value(trade, date)
            market_value += value
        equity = cash + market_value
        peak_equity = max(peak_equity, equity)
        drawdown = (equity / peak_equity - 1) * 100 if peak_equity else 0.0
        max_drawdown = min(max_drawdown, drawdown)
        snapshots.append(
            {
                "as_of": date.strftime("%Y-%m-%d"),
                "cash": cash,
                "market_value": market_value,
                "equity": equity,
                "total_return_pct": (equity / config.starting_cash - 1) * 100,
                "peak_equity": peak_equity,
                "drawdown_pct": drawdown,
                "open_positions": len(open_positions),
                "closed_trades": closed_count,
            }
        )
        previous_equity = equity

    if not snapshots:
        snapshot_date = latest_signal_date or as_of
        snapshots.append(
            {
                "as_of": snapshot_date.strftime("%Y-%m-%d"),
                "cash": cash,
                "market_value": 0.0,
                "equity": cash,
                "total_return_pct": 0.0,
                "peak_equity": cash,
                "drawdown_pct": 0.0,
                "open_positions": 0,
                "closed_trades": 0,
            }
        )

    final_date = _normalized_date(snapshots[-1]["as_of"])
    for trade in open_positions.values():
        mark_price, market_value = liquidation_value(trade, final_date)
        trade["mark_at"] = final_date.strftime("%Y-%m-%d")
        trade["mark_price"] = mark_price
        trade["market_value"] = market_value
        trade["unrealized_pnl"] = market_value - float(trade["invested_amount"] or 0)

    for trade in trades:
        if trade["status"] == "pending" and trade["skip_reason"] is None:
            source = signal_by_id[int(trade["source_id"])]
            trade["skip_reason"] = (
                "awaiting_exit_data" if source.get("entry_at") else "awaiting_next_open"
            )

    final_snapshot = snapshots[-1]
    closed = [trade for trade in trades if trade["status"] == "closed"]
    account = {
        "account_key": spec.account_key,
        "name": spec.name,
        "strategy_kind": spec.strategy_kind,
        "evidence_mode": spec.evidence_mode,
        "policy_version": config.policy_version,
        "execution_version": CANDIDATE_EXECUTION_VERSION,
        "starting_cash": config.starting_cash,
        "cash": final_snapshot["cash"],
        "equity": final_snapshot["equity"],
        "total_return_pct": final_snapshot["total_return_pct"],
        "max_drawdown_pct": max_drawdown,
        "closed_trades": len(closed),
        "winning_trades": sum((trade["realized_pnl"] or 0) > 0 for trade in closed),
        "open_positions": sum(trade["status"] == "open" for trade in trades),
        "pending_orders": sum(trade["status"] == "pending" for trade in trades),
        "skipped_orders": sum(trade["status"] == "skipped" for trade in trades),
        "first_signal_at": min(
            (trade["signal_at"] for trade in trades),
            default=None,
        ),
        "last_equity_at": final_snapshot["as_of"],
        "status": "shadow",
        "config_json": json.dumps(asdict(config), ensure_ascii=True, sort_keys=True),
    }
    return {"account": account, "trades": trades, "snapshots": snapshots}


def save_simulation(result, db_path=DB_PATH):
    account = result["account"]
    now = get_taipei_now().isoformat(timespec="seconds")
    account_columns = (
        "account_key",
        "name",
        "strategy_kind",
        "evidence_mode",
        "policy_version",
        "execution_version",
        "starting_cash",
        "cash",
        "equity",
        "total_return_pct",
        "max_drawdown_pct",
        "closed_trades",
        "winning_trades",
        "open_positions",
        "pending_orders",
        "skipped_orders",
        "first_signal_at",
        "last_equity_at",
        "status",
        "config_json",
    )
    with get_connection(db_path) as conn:
        init_db(conn)
        conn.execute(
            f"""
            INSERT INTO paper_accounts (
                {', '.join(account_columns)}, created_at, updated_at
            ) VALUES ({', '.join('?' for _ in account_columns)}, ?, ?)
            ON CONFLICT(account_key) DO UPDATE SET
                {', '.join(f'{column}=excluded.{column}' for column in account_columns if column != 'account_key')},
                updated_at=excluded.updated_at
            """,
            tuple(account[column] for column in account_columns) + (now, now),
        )
        account_id = conn.execute(
            "SELECT id FROM paper_accounts WHERE account_key=?",
            (account["account_key"],),
        ).fetchone()["id"]
        conn.execute("DELETE FROM paper_trades WHERE account_id=?", (account_id,))
        conn.execute("DELETE FROM paper_equity_snapshots WHERE account_id=?", (account_id,))

        for trade in result["trades"]:
            conn.execute(
                f"""
                INSERT INTO paper_trades (
                    account_id, {', '.join(TRADE_COLUMNS)}, created_at, updated_at
                ) VALUES (?, {', '.join('?' for _ in TRADE_COLUMNS)}, ?, ?)
                """,
                (account_id,)
                + tuple(trade.get(column) for column in TRADE_COLUMNS)
                + (now, now),
            )
        for snapshot in result["snapshots"]:
            conn.execute(
                """
                INSERT INTO paper_equity_snapshots (
                    account_id, as_of, cash, market_value, equity,
                    total_return_pct, peak_equity, drawdown_pct,
                    open_positions, closed_trades, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    snapshot["as_of"],
                    snapshot["cash"],
                    snapshot["market_value"],
                    snapshot["equity"],
                    snapshot["total_return_pct"],
                    snapshot["peak_equity"],
                    snapshot["drawdown_pct"],
                    snapshot["open_positions"],
                    snapshot["closed_trades"],
                    now,
                ),
            )
    return account_id


def run_paper_trading(
    db_path=DB_PATH,
    config=None,
    account_keys=None,
    price_loader=download_price_data,
    as_of=None,
):
    config = config or PaperTradingConfig()
    as_of_date = _normalized_date(as_of or get_taipei_now().date())
    selected_specs = [
        spec
        for spec in ACCOUNT_SPECS
        if not account_keys or spec.account_key in set(account_keys)
    ]
    signals_by_key = {
        "rule_baseline_v1": load_rule_signals(db_path=db_path),
        "ai_shadow_v1": load_ai_signals(db_path=db_path),
    }
    all_entries = [
        _normalized_date(signal.get("entry_at"))
        for spec in selected_specs
        for signal in signals_by_key[spec.account_key]
        if signal.get("entry_at")
    ]
    start = min(all_entries, default=as_of_date) - pd.Timedelta(days=5)
    price_cache = PriceCache(
        start=start,
        end=as_of_date + pd.Timedelta(days=1),
        loader=price_loader,
    )

    summaries = []
    for spec in selected_specs:
        result = simulate_account(
            spec,
            signals_by_key[spec.account_key],
            config=config,
            price_cache=price_cache,
            as_of=as_of_date,
        )
        save_simulation(result, db_path=db_path)
        summaries.append(result["account"])
        account = result["account"]
        print(
            f"{account['name']}: equity={account['equity']:.2f}, "
            f"return={account['total_return_pct']:.2f}%, "
            f"closed={account['closed_trades']}, pending={account['pending_orders']}"
        )
    return summaries


def main():
    parser = argparse.ArgumentParser(
        description="Replay rule and prospective AI signals with constrained paper capital."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--starting-cash", type=float, default=1_000_000.0)
    parser.add_argument(
        "--account",
        action="append",
        choices=[spec.account_key for spec in ACCOUNT_SPECS],
    )
    args = parser.parse_args()
    config = PaperTradingConfig(starting_cash=args.starting_cash)
    run_paper_trading(
        db_path=args.db,
        config=config,
        account_keys=args.account,
    )


if __name__ == "__main__":
    main()
