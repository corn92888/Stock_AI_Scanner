import argparse
import json
import math
import os
from dataclasses import asdict, dataclass

from database import (
    ALPHA_FORWARD_START_DATE,
    DB_PATH,
    LIVE_CAPITAL_POLICY_VERSION,
    get_connection,
    get_taipei_now,
    init_db,
)


REFERENCE_CAPITAL_ENV = "LIVE_CAPITAL_REFERENCE"
DEFAULT_REFERENCE_CAPITAL = 1_000_000.0
MIN_PROMOTION_QUOTE_COVERAGE_PCT = 95.0
MIN_LIQUIDITY_BILLION = 1.0
VALIDITY_POLICY = "next_trading_session_open_only"


@dataclass(frozen=True)
class CapitalStagePolicy:
    key: str
    label: str
    min_decision_days: int
    min_closed_trades: int
    min_profitable_months: int
    min_profitable_month_rate_pct: float | None
    min_probabilistic_sharpe: float | None
    min_total_return_pct: float | None
    min_avg_excess_return_pct: float | None
    drawdown_floor_pct: float
    max_strategy_weight: float
    max_position_weight: float
    max_positions: int


CAPITAL_STAGES = (
    CapitalStagePolicy(
        key="SHADOW",
        label="影子觀察",
        min_decision_days=0,
        min_closed_trades=0,
        min_profitable_months=0,
        min_profitable_month_rate_pct=None,
        min_probabilistic_sharpe=None,
        min_total_return_pct=None,
        min_avg_excess_return_pct=None,
        drawdown_floor_pct=-12.0,
        max_strategy_weight=0.0,
        max_position_weight=0.0,
        max_positions=0,
    ),
    CapitalStagePolicy(
        key="MICRO",
        label="微型實盤",
        min_decision_days=20,
        min_closed_trades=30,
        min_profitable_months=0,
        min_profitable_month_rate_pct=None,
        min_probabilistic_sharpe=None,
        min_total_return_pct=0.0,
        min_avg_excess_return_pct=0.0,
        drawdown_floor_pct=-4.0,
        max_strategy_weight=0.02,
        max_position_weight=0.005,
        max_positions=4,
    ),
    CapitalStagePolicy(
        key="LIMITED",
        label="小額實盤",
        min_decision_days=60,
        min_closed_trades=75,
        min_profitable_months=2,
        min_profitable_month_rate_pct=50.0,
        min_probabilistic_sharpe=0.80,
        min_total_return_pct=0.0,
        min_avg_excess_return_pct=0.0,
        drawdown_floor_pct=-6.0,
        max_strategy_weight=0.10,
        max_position_weight=0.02,
        max_positions=6,
    ),
    CapitalStagePolicy(
        key="PRODUCTION",
        label="正式策略",
        min_decision_days=120,
        min_closed_trades=150,
        min_profitable_months=3,
        min_profitable_month_rate_pct=60.0,
        min_probabilistic_sharpe=0.95,
        min_total_return_pct=0.0,
        min_avg_excess_return_pct=0.0,
        drawdown_floor_pct=-12.0,
        max_strategy_weight=0.24,
        max_position_weight=0.06,
        max_positions=12,
    ),
)


def _decode_object(value):
    try:
        decoded = json.loads(value or "")
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _number(value, default=None):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _reference_capital(value=None):
    raw = value if value is not None else os.getenv(REFERENCE_CAPITAL_ENV)
    capital = _number(raw, DEFAULT_REFERENCE_CAPITAL)
    if capital is None or capital <= 0:
        raise ValueError("Reference capital must be greater than zero")
    return capital


def _gate(key, label, value, requirement, passed):
    return {
        "key": key,
        "label": label,
        "value": value,
        "requirement": requirement,
        "passed": bool(passed),
    }


def _stage_gates(metrics, policy, operational_ready):
    decision_days = int(metrics.get("decision_days", 0) or 0)
    closed_trades = int(metrics.get("closed_trades", 0) or 0)
    total_return = _number(metrics.get("total_return_pct"), 0.0)
    avg_excess = _number(metrics.get("avg_excess_return_pct"))
    max_drawdown = _number(metrics.get("max_drawdown_pct"), 0.0)
    month_count = int(metrics.get("profitable_month_count", 0) or 0)
    profitable_month_rate = _number(metrics.get("profitable_month_rate_pct"))
    psr = _number(metrics.get("probabilistic_sharpe"))

    gates = [
        _gate(
            "operational_ready",
            "資料與執行鏈完整",
            1 if operational_ready else 0,
            "= 1",
            operational_ready,
        ),
        _gate(
            "decision_days",
            "前瞻決策日",
            decision_days,
            f">= {policy.min_decision_days}",
            decision_days >= policy.min_decision_days,
        ),
        _gate(
            "closed_trades",
            "結案交易",
            closed_trades,
            f">= {policy.min_closed_trades}",
            closed_trades >= policy.min_closed_trades,
        ),
        _gate(
            "max_drawdown",
            "最大回撤",
            max_drawdown,
            f"> {policy.drawdown_floor_pct:.0f}%",
            max_drawdown > policy.drawdown_floor_pct,
        ),
    ]
    if policy.min_total_return_pct is not None:
        gates.append(
            _gate(
                "total_return",
                "成本後總報酬",
                total_return,
                f"> {policy.min_total_return_pct:.0f}%",
                total_return > policy.min_total_return_pct,
            )
        )
    if policy.min_avg_excess_return_pct is not None:
        gates.append(
            _gate(
                "average_excess_return",
                "平均成本後超額",
                avg_excess,
                f"> {policy.min_avg_excess_return_pct:.0f}%",
                avg_excess is not None
                and avg_excess > policy.min_avg_excess_return_pct,
            )
        )
    if policy.min_profitable_months:
        gates.append(
            _gate(
                "profitable_months",
                "可評估月份",
                month_count,
                f">= {policy.min_profitable_months}",
                month_count >= policy.min_profitable_months,
            )
        )
    if policy.min_profitable_month_rate_pct is not None:
        gates.append(
            _gate(
                "profitable_month_rate",
                "獲利月份比例",
                profitable_month_rate,
                f">= {policy.min_profitable_month_rate_pct:.0f}%",
                profitable_month_rate is not None
                and profitable_month_rate
                >= policy.min_profitable_month_rate_pct,
            )
        )
    if policy.min_probabilistic_sharpe is not None:
        gates.append(
            _gate(
                "probabilistic_sharpe",
                "機率夏普比率",
                psr,
                f">= {policy.min_probabilistic_sharpe:.2f}",
                psr is not None and psr >= policy.min_probabilistic_sharpe,
            )
        )
    return gates


def evaluate_capital_ladder(forward_metrics, reference_capital=None):
    metrics = dict(forward_metrics or {})
    quote = metrics.get("quote_health") or {}
    research = metrics.get("research_health") or {}
    coverage = _number(quote.get("coverage_pct"))
    candidate_pool_rows = int(metrics.get("candidate_pool_rows", 0) or 0)
    latest_status = str(metrics.get("latest_signal_status") or "not_run")
    signal_requires_pool = latest_status in {"active", "abstained", "paused"}
    operational_ready = (
        str(metrics.get("state") or "COLLECTING") != "PAUSED"
        and str(metrics.get("data_quality_status") or "waiting") != "critical"
        and latest_status in {"active", "abstained"}
        and (coverage is None or coverage >= MIN_PROMOTION_QUOTE_COVERAGE_PCT)
        and int(research.get("stale_outcomes", 0) or 0) == 0
        and (not signal_requires_pool or candidate_pool_rows > 0)
    )
    max_drawdown = _number(metrics.get("max_drawdown_pct"), 0.0)
    hard_paused = (
        str(metrics.get("state") or "") == "PAUSED"
        or str(metrics.get("data_quality_status") or "") == "critical"
        or max_drawdown <= -12.0
    )

    stage_rows = []
    qualified = []
    for policy in CAPITAL_STAGES:
        gates = _stage_gates(
            metrics,
            policy,
            operational_ready or policy.key == "SHADOW",
        )
        passed = all(gate["passed"] for gate in gates)
        if passed:
            qualified.append(policy)
        stage_rows.append(
            {
                **asdict(policy),
                "passed": passed,
                "progress_pct": round(
                    sum(gate["passed"] for gate in gates) / len(gates) * 100.0,
                    2,
                ),
                "gates": gates,
            }
        )

    selected = CAPITAL_STAGES[0]
    if not hard_paused and qualified:
        selected = qualified[-1]
    stage = "PAUSED" if hard_paused else selected.key
    selected_index = next(
        (
            index
            for index, policy in enumerate(CAPITAL_STAGES)
            if policy.key == selected.key
        ),
        0,
    )
    next_policy = (
        CAPITAL_STAGES[selected_index + 1]
        if selected_index + 1 < len(CAPITAL_STAGES)
        else None
    )
    reason_codes = []
    if hard_paused:
        reason_codes.append("capital_governance_paused")
    elif selected.key == "SHADOW":
        reason_codes.append("micro_live_evidence_not_reached")
    if not operational_ready:
        reason_codes.append("operational_promotion_gate_failed")

    capital = _reference_capital(reference_capital)
    return {
        "policy_version": LIVE_CAPITAL_POLICY_VERSION,
        "evidence_start_date": ALPHA_FORWARD_START_DATE,
        "stage": stage,
        "stage_label": "已暫停" if hard_paused else selected.label,
        "order_preview_enabled": not hard_paused and selected.key != "SHADOW",
        "live_transmission_enabled": False,
        "manual_approval_required": True,
        "position_ledger_connected": False,
        "reference_capital": capital,
        "max_strategy_weight": 0.0
        if hard_paused
        else selected.max_strategy_weight,
        "max_position_weight": 0.0
        if hard_paused
        else selected.max_position_weight,
        "max_positions": 0 if hard_paused else selected.max_positions,
        "next_stage": next_policy.key if not hard_paused and next_policy else None,
        "next_stage_label": next_policy.label
        if not hard_paused and next_policy
        else None,
        "operational_ready": operational_ready,
        "reason_codes": reason_codes,
        "stages": stage_rows,
    }


def _normalized_ratio(value):
    ratio = _number(value)
    if ratio is None:
        return None
    return ratio / 100.0 if ratio > 1.0 else ratio


def _market_gate(signal):
    market_return = _number(signal.get("market_return_20d"))
    above_ma200 = _normalized_ratio(signal.get("market_above_ma200"))
    up_ratio = _normalized_ratio(signal.get("market_up_ratio"))
    passed = (
        market_return is not None
        and market_return > 0.0
        and above_ma200 is not None
        and above_ma200 >= 0.50
        and up_ratio is not None
        and up_ratio >= 0.45
    )
    return passed, {
        "market_return_20d": market_return,
        "market_above_ma200": above_ma200,
        "market_up_ratio": up_ratio,
    }


def build_order_intents(governance, signals):
    signal_rows = [dict(signal) for signal in signals]
    if not signal_rows:
        return []
    stage = str(governance.get("stage") or "SHADOW")
    policies = {policy.key: policy for policy in CAPITAL_STAGES}
    current_policy = policies.get(stage, CAPITAL_STAGES[0])
    preview_policy = (
        policies["MICRO"] if stage in {"SHADOW", "PAUSED"} else current_policy
    )
    count = max(len(signal_rows), 1)
    proposed_weight = min(
        preview_policy.max_position_weight,
        preview_policy.max_strategy_weight / count,
    )
    reference_capital = float(governance["reference_capital"])
    intents = []
    for signal in signal_rows:
        reason_codes = []
        market_passed, market_context = _market_gate(signal)
        liquidity = _number(signal.get("turnover_20d_billion"), 0.0)
        predicted_alpha = _number(signal.get("predicted_alpha"))
        signal_price = _number(signal.get("signal_price"), 0.0)

        if stage == "PAUSED":
            reason_codes.append("governance_paused")
        elif stage == "SHADOW":
            reason_codes.append("capital_stage_shadow")
        if not market_passed:
            reason_codes.append("market_regime_blocked")
        if liquidity < MIN_LIQUIDITY_BILLION:
            reason_codes.append("liquidity_below_minimum")
        if predicted_alpha is None or predicted_alpha <= 0:
            reason_codes.append("non_positive_predicted_alpha")
        if signal_price <= 0:
            reason_codes.append("invalid_signal_price")

        eligible = (
            governance.get("order_preview_enabled", False)
            and stage not in {"SHADOW", "PAUSED"}
            and not reason_codes
        )
        target_weight = proposed_weight if eligible else 0.0
        max_notional = reference_capital * target_weight
        suggested_quantity = (
            int(max_notional // signal_price)
            if signal_price > 0 and max_notional > 0
            else 0
        )
        decision_status = (
            "manual_approval_required"
            if eligible
            else "blocked_by_pretrade_policy"
        )
        intents.append(
            {
                "signal_date": str(signal.get("signal_date") or ""),
                "code": str(signal.get("code") or ""),
                "name": signal.get("name") or "",
                "industry": signal.get("industry") or "",
                "side": "BUY",
                "signal_price": signal_price,
                "predicted_alpha": predicted_alpha,
                "proposed_weight": proposed_weight,
                "target_weight": target_weight,
                "max_notional": max_notional,
                "suggested_quantity": suggested_quantity,
                "decision_status": decision_status,
                "approval_status": "pending_manual"
                if eligible
                else "not_eligible",
                "validity_policy": VALIDITY_POLICY,
                "reason_codes": reason_codes,
                "market_gate_passed": market_passed,
                "market_context": market_context,
            }
        )
    return intents


def _load_latest_forward_metrics(conn):
    row = conn.execute(
        """
        SELECT metrics_json
        FROM alpha_forward_snapshots
        ORDER BY evaluated_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    return _decode_object(row["metrics_json"]) if row else {}


def _load_latest_alpha_signals(conn):
    run = conn.execute(
        """
        SELECT id, signal_date
        FROM alpha_live_runs
        ORDER BY signal_date DESC, generated_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    if not run:
        return []
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                r.signal_date,
                s.code,
                s.name,
                s.industry,
                s.signal_price,
                s.predicted_alpha,
                c.turnover_20d_billion,
                c.market_return_20d,
                c.market_above_ma200,
                c.market_up_ratio
            FROM alpha_live_signals s
            JOIN alpha_live_runs r ON r.id=s.run_id
            LEFT JOIN alpha_live_candidates c
              ON c.run_id=s.run_id AND c.code=s.code
            WHERE s.run_id=?
            ORDER BY s.rank_order, s.id
            """,
            (run["id"],),
        ).fetchall()
    ]


def save_capital_governance(governance, intents, db_path=DB_PATH):
    evaluated_at = get_taipei_now().isoformat(timespec="seconds")
    metrics = {
        **governance,
        "evaluated_at": evaluated_at,
        "order_intents_count": len(intents),
        "eligible_order_intents": sum(
            intent["decision_status"] == "manual_approval_required"
            for intent in intents
        ),
    }
    with get_connection(db_path) as conn:
        init_db(conn)
        conn.execute(
            """
            INSERT INTO live_capital_snapshots (
                evaluated_at, policy_version, evidence_start_date, stage,
                order_preview_enabled, live_transmission_enabled,
                reference_capital, max_strategy_weight, max_position_weight,
                max_positions, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(policy_version, evaluated_at) DO UPDATE SET
                evidence_start_date=excluded.evidence_start_date,
                stage=excluded.stage,
                order_preview_enabled=excluded.order_preview_enabled,
                live_transmission_enabled=excluded.live_transmission_enabled,
                reference_capital=excluded.reference_capital,
                max_strategy_weight=excluded.max_strategy_weight,
                max_position_weight=excluded.max_position_weight,
                max_positions=excluded.max_positions,
                metrics_json=excluded.metrics_json
            """,
            (
                evaluated_at,
                governance["policy_version"],
                governance["evidence_start_date"],
                governance["stage"],
                int(governance["order_preview_enabled"]),
                int(governance["live_transmission_enabled"]),
                governance["reference_capital"],
                governance["max_strategy_weight"],
                governance["max_position_weight"],
                governance["max_positions"],
                json.dumps(metrics, ensure_ascii=True, sort_keys=True),
            ),
        )
        snapshot_id = conn.execute(
            """
            SELECT id
            FROM live_capital_snapshots
            WHERE policy_version=? AND evaluated_at=?
            """,
            (governance["policy_version"], evaluated_at),
        ).fetchone()["id"]
        conn.execute(
            "DELETE FROM live_order_intents WHERE snapshot_id=?",
            (snapshot_id,),
        )
        for intent in intents:
            conn.execute(
                """
                INSERT INTO live_order_intents (
                    snapshot_id, signal_date, generated_at, code, name,
                    industry, side, signal_price, predicted_alpha,
                    proposed_weight, target_weight, max_notional,
                    suggested_quantity, decision_status, approval_status,
                    validity_policy, market_gate_passed, market_context_json,
                    reason_codes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    intent["signal_date"],
                    evaluated_at,
                    intent["code"],
                    intent["name"],
                    intent["industry"],
                    intent["side"],
                    intent["signal_price"],
                    intent["predicted_alpha"],
                    intent["proposed_weight"],
                    intent["target_weight"],
                    intent["max_notional"],
                    intent["suggested_quantity"],
                    intent["decision_status"],
                    intent["approval_status"],
                    intent["validity_policy"],
                    int(intent["market_gate_passed"]),
                    json.dumps(
                        intent["market_context"],
                        ensure_ascii=True,
                        sort_keys=True,
                    ),
                    json.dumps(intent["reason_codes"], ensure_ascii=True),
                ),
            )
    return metrics


def run_capital_governance(db_path=DB_PATH, reference_capital=None):
    with get_connection(db_path) as conn:
        init_db(conn)
        forward_metrics = _load_latest_forward_metrics(conn)
        signals = _load_latest_alpha_signals(conn)
    governance = evaluate_capital_ladder(
        forward_metrics,
        reference_capital=reference_capital,
    )
    intents = build_order_intents(governance, signals)
    metrics = save_capital_governance(governance, intents, db_path=db_path)
    print(
        "Capital governance: "
        f"stage={metrics['stage']}, "
        f"preview={metrics['order_preview_enabled']}, "
        f"eligible_intents={metrics['eligible_order_intents']}, "
        "live_transmission=false"
    )
    return {"governance": metrics, "order_intents": intents}


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate staged live-capital governance and order previews."
    )
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--reference-capital", type=float)
    args = parser.parse_args()
    run_capital_governance(
        db_path=args.db,
        reference_capital=args.reference_capital,
    )


if __name__ == "__main__":
    main()
