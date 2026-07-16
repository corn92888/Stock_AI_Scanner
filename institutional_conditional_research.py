import argparse
import hashlib
import json
from pathlib import Path

from ai_pipeline import MODEL_FEATURES
from cross_sectional_research import (
    ALPHA_PREDICTION_QUANTILE,
    CROSS_SECTIONAL_FEATURE_MODE,
    RankingSpec,
)
from database import DB_PATH
from institutional_research import (
    InstitutionalAblationStudy,
    evaluate_institutional_spec,
    load_institutional_research_frame,
)


CONDITIONAL_EVALUATION_FAMILY = "generation_2_institutional_interaction_v1"
CONDITIONAL_MODEL_VERSION = "hist_gradient_peer_ranker_institutional_interaction_v1"
LOCKED_INTERACTION_FEATURE = "foreign_trust_industry_consensus"
LOCKED_CONDITION_KEY = "foreign_trust_consensus_buy_x_industry_breadth_strong"
LOCKED_CONDITION_LABEL = "外資與投信同向買超 × 產業上漲家數至少 55%"
LOCKED_DISCOVERY_FLOW_KEY = "foreign_trust_consensus_buy"
LOCKED_DISCOVERY_SEGMENT_KEY = "industry_breadth_strong"
LOCKED_CONDITION_RULES = (
    ("foreign_net_z20", ">", 0.0),
    ("trust_net_z20", ">", 0.0),
    ("agreement_score_1d", ">=", 2.0),
    ("industry_up_ratio", ">=", 55.0),
)
CONDITIONAL_FEATURES = tuple(MODEL_FEATURES) + (LOCKED_INTERACTION_FEATURE,)
CONDITIONAL_SPECS = (
    RankingSpec(
        method="next_open",
        horizon=5,
        target="peer_rank",
        prediction_quantile=ALPHA_PREDICTION_QUANTILE,
        feature_mode=CROSS_SECTIONAL_FEATURE_MODE,
        feature_columns=CONDITIONAL_FEATURES,
        experiment_key="g2_institutional_interaction_next_open_t5_q80_v1",
    ),
    RankingSpec(
        method="next_open",
        horizon=10,
        target="peer_rank",
        prediction_quantile=ALPHA_PREDICTION_QUANTILE,
        feature_mode=CROSS_SECTIONAL_FEATURE_MODE,
        feature_columns=CONDITIONAL_FEATURES,
        experiment_key="g2_institutional_interaction_next_open_t10_q80_v1",
    ),
)
CONDITIONAL_STUDY = InstitutionalAblationStudy(
    evaluation_family=CONDITIONAL_EVALUATION_FAMILY,
    model_version=CONDITIONAL_MODEL_VERSION,
    strategy_family="generation_2_institutional_interaction",
    selector="all_complete_replay_candidates_with_locked_interaction",
    name_prefix="Generation 2 institutional interaction",
    hypothesis_template=(
        "Test whether one development-locked interaction between lagged foreign and "
        "investment-trust consensus buying and contemporaneously known strong industry "
        "breadth adds after-cost T+{horizon} excess-return lift over the identical "
        "technical peer-ranker control."
    ),
    incremental_features=(LOCKED_INTERACTION_FEATURE,),
    comparisons=len(CONDITIONAL_SPECS),
    condition_key=LOCKED_CONDITION_KEY,
    condition_label=LOCKED_CONDITION_LABEL,
)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_locked_interaction(frame):
    result = frame.copy()
    condition = (
        (result["foreign_net_z20"] > 0.0)
        & (result["trust_net_z20"] > 0.0)
        & (result["agreement_score_1d"] >= 2.0)
        & (result["industry_up_ratio"] >= 55.0)
    )
    result[LOCKED_INTERACTION_FEATURE] = condition.astype(int)
    return result


def validate_attribution_report(attribution_path):
    report = json.loads(Path(attribution_path).read_text(encoding="utf-8"))
    if report.get("evaluation_scope") != "historical_development_discovery_only":
        raise ValueError("Conditional research requires a development-only attribution.")
    if report.get("validation_evaluated") or report.get("holdout_evaluated"):
        raise ValueError("Conditional research attribution must not evaluate reserved data.")
    selected = report.get("selected_confirmation_candidate") or {}
    observed = (selected.get("flow_key"), selected.get("segment_key"))
    expected = (LOCKED_DISCOVERY_FLOW_KEY, LOCKED_DISCOVERY_SEGMENT_KEY)
    if observed != expected or not selected.get("eligible_for_confirmation"):
        raise ValueError(
            "Development attribution does not match the pre-registered interaction."
        )
    return report


def run_conditional_ablation(
    dataset_path,
    db_path=DB_PATH,
    specs=CONDITIONAL_SPECS,
    gates=None,
    min_train_rows=500,
    attribution_path=None,
):
    if attribution_path is not None:
        validate_attribution_report(attribution_path)
    dataset_path = Path(dataset_path)
    frame = load_institutional_research_frame(dataset_path)
    if frame.empty:
        return []
    frame = add_locked_interaction(frame)
    fingerprint = _sha256(dataset_path)
    return [
        evaluate_institutional_spec(
            frame,
            spec,
            fingerprint,
            db_path=db_path,
            gates=gates,
            min_train_rows=min_train_rows,
            study=CONDITIONAL_STUDY,
        )
        for spec in specs
    ]


def main():
    parser = argparse.ArgumentParser(
        description="Run pre-registered conditional institutional ablations."
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--attribution", required=True)
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()
    result = run_conditional_ablation(
        args.dataset,
        db_path=args.db,
        attribution_path=args.attribution,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
