from datetime import datetime, timedelta, timezone

from jevtweet.history_experiment import (
    SCOPE,
    eligible_evidence,
    method_a,
    retrieve_shortlist,
    run_target,
    spearman,
    summarize,
    weighted_log_mean,
)

T = datetime(2026, 1, 10, tzinfo=timezone.utc)


def _post(pid, text, days_before, views, post_type="original"):
    return {
        "post_id": pid,
        "text": text,
        "post_type": post_type,
        "claimed_date": T - timedelta(days=days_before),
        "metrics": {"views": views},
    }


def _target():
    return _post("t", "alpha beta gamma delta", 0, 100)


def test_scope_is_development_only():
    assert SCOPE == "development_only"


def test_eligibility_excludes_self_future_missing_and_neardup():
    target = _target()
    pool = [
        _post("self", "alpha beta gamma delta", 1, 50),
        _post("future", "alpha beta", -1, 50),
        {
            "post_id": "nomet",
            "text": "alpha beta",
            "post_type": "original",
            "claimed_date": T - timedelta(days=2),
            "metrics": {},
        },
        _post("ok", "alpha beta quite different words here", 2, 60),
    ]
    pool[0]["post_id"] = "t"
    eligible = eligible_evidence(target, pool, exclude_ids={"other"})
    assert [r["post_id"] for r in eligible] == ["ok"]


def test_retrieval_rank_tiebreak_and_dedupe():
    ev = [_post("b", "alpha beta", 3, 10), _post("a", "alpha beta", 4, 20), _post("c", "alpha beta", 5, 30)]
    short = retrieve_shortlist("alpha beta", ev, k=8)
    assert [r["post_id"] for _, r in short] == ["a"]
    assert short[0][0] == 1.0


def test_estimator_and_fallback_chain():
    target = _target()
    assert method_a(target, [])[0] is None
    thin = [_post("o1", "x y", 1, 10), _post("o2", "x z", 2, 1000)]
    est, flag, _ = method_a(target, thin)
    assert flag == "account_fallback"
    row = run_target(target, [], noul_map=None)
    assert row["A"]["estimate"] is None and row["B"]["fallback"] == "method_A"
    assert row["C"]["fallback"] == "method_A" and row["scope"] == "development_only"


def test_c_requires_full_noul_coverage():
    target = _target()
    ev = [_post("e1", "alpha beta gamma", 1, 50), _post("e2", "alpha beta delta", 2, 200)]
    row = run_target(target, ev, noul_map={("t", "e1"): 0.9})
    assert row["C"]["estimate"] is None and row["C"]["fallback"] == "noul_unavailable"
    row = run_target(target, ev, noul_map={("t", "e1"): 0.9, ("t", "e2"): 0.1})
    assert row["C"]["estimate"] is not None and row["C"]["fallback"] is None


def test_weighted_mean_prefers_higher_scores():
    pairs = [({"metrics": {"views": 100}}, 0.0), ({"metrics": {"views": 10000}}, 1.0)]
    est, weights = weighted_log_mean(pairs, lambda _r, s: s)
    assert weights[1] > weights[0] and est is not None


def test_summarize_reports_error_ranking_coverage():
    rows = [
        {
            "post_id": str(i),
            "actual_log_views": float(i),
            "A": {"estimate": float(i), "fallback": None},
            "B": {"estimate": float(i) + 1.0, "fallback": None},
            "C": {"estimate": None, "fallback": "noul_unavailable"},
        }
        for i in range(5)
    ]
    summary = summarize(rows)
    assert summary["methods"]["A"]["mae_log"] == 0.0
    assert summary["methods"]["A"]["spearman"] == 1.0
    assert summary["methods"]["B"]["mae_log"] == 1.0
    assert summary["methods"]["C"]["covered"] == 0
    assert summary["methods"]["C"]["mae_log"] is None
    assert spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None
