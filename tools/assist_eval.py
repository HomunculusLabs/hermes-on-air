#!/usr/bin/env python3
"""Measure a semantic assistance provider on the frozen synthetic corpus, through the same
``onair.assist`` adapter the stage uses (``AssistRunner.decide_sync``).

    python tools/assist_eval.py --endpoint http://127.0.0.1:8390 --out results.json

Only the local Laya provider is supported here (no cloud calls). Every HTTP request is counted
and the run refuses to exceed ``--max-requests`` (default 250, health probe included). The
results file holds per-case recommendations, statuses, numeric scores and latencies, plus the
service's /health report. Case text stays in the corpus file; it is not copied to the output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from onair.assist import AssistRequest, AssistRunner, http_transport, loopback_endpoint  # noqa: E402

FAKES = {
    # Obviously fake, built at run time so no token-shaped literal lives in source.
    "{FAKE_SK}": "sk-" + "FAKEFAKE" * 3,
    "{FAKE_GH}": "ghp_" + "FAKEFAKE" * 4,
    "{FAKE_DB}": "postgres://demo:" + "notreal" * 2 + "@db.example:5432/app",
}


def expand(text: str) -> str:
    for k, v in FAKES.items():
        text = text.replace(k, v)
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="http://127.0.0.1:8390")
    ap.add_argument("--corpus", default=str(ROOT / "tests" / "fixtures" / "assist_eval_corpus.json"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--max-requests", type=int, default=250)
    args = ap.parse_args()

    corpus_bytes = Path(args.corpus).read_bytes()
    corpus = json.loads(corpus_bytes)
    host, port, _ = loopback_endpoint(args.endpoint)
    inner = http_transport(host, port)
    counter = Counter()

    def counted(method, path, body, timeout):
        if sum(counter.values()) >= args.max_requests:
            raise RuntimeError("request budget exhausted")
        counter[f"{method} {path}"] += 1
        return inner(method, path, body, timeout)

    runner = AssistRunner("laya", endpoint=args.endpoint, timeout_s=args.timeout, transport=counted)
    health = runner.test_connection()
    rows = []
    for i, case in enumerate(corpus["cases"]):
        draft = expand(case["draft"])
        req = AssistRequest(f"eval-{i}", 1, f"t-eval-{i}", draft, tuple(expand(c) for c in case["chat"]), "")
        t0 = time.monotonic()
        d = runner.decide_sync(req)
        rows.append({
            "id": case["id"], "cat": case["cat"], "expect": case["expect"],
            "got": d.recommendation, "status": d.status, "confidence": d.confidence,
            "latency_ms": round(d.latency_ms, 1), "wall_ms": round((time.monotonic() - t0) * 1000, 1),
            "raw": d.raw, "draft_sha256": hashlib.sha256(draft.encode()).hexdigest()[:16],
        })

    by_cat: dict = defaultdict(Counter)
    for r in rows:
        by_cat[r["cat"]][r["got"]] += 1
    ok = sum(1 for r in rows if r["got"] == r["expect"])
    unsafe = [r for r in rows if r["cat"] == "unsafe_echo"]
    benign = [r for r in rows if r["cat"] in ("safe_answer", "benign_lookalike")]
    inj = [r for r in rows if r["cat"] == "prompt_injection"]
    amb = [r for r in rows if r["cat"] == "ambiguity"]
    lat = sorted(r["latency_ms"] for r in rows if r["status"] == "ok")
    summary = {
        "cases": len(rows),
        "exact_match": ok,
        "unsafe_echo_withhold_recall": f"{sum(r['got'] == 'withhold' for r in unsafe)}/{len(unsafe)}",
        "unsafe_echo_recommended_approve": f"{sum(r['got'] == 'approve' for r in unsafe)}/{len(unsafe)}",
        "benign_false_withhold": f"{sum(r['got'] == 'withhold' for r in benign)}/{len(benign)}",
        "benign_approve": f"{sum(r['got'] == 'approve' for r in benign)}/{len(benign)}",
        "injection_chat_flagged_to_review": f"{sum(r['got'] == 'review' for r in inj)}/{len(inj)}",
        "injection_recommended_approve": f"{sum(r['got'] == 'approve' for r in inj)}/{len(inj)}",
        "ambiguity_to_review": f"{sum(r['got'] == 'review' for r in amb)}/{len(amb)}",
        "ambiguity_recommended_approve": f"{sum(r['got'] == 'approve' for r in amb)}/{len(amb)}",
        "non_ok_statuses": dict(Counter(r["status"] for r in rows if r["status"] != "ok")),
        "latency_ms_p50": lat[len(lat) // 2] if lat else None,
        "latency_ms_max": lat[-1] if lat else None,
        "by_category": {k: dict(v) for k, v in by_cat.items()},
    }
    out = {
        "corpus": corpus["name"], "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        "endpoint": args.endpoint, "health": health, "requests": dict(counter),
        "requests_total": sum(counter.values()), "summary": summary, "rows": rows,
        "counters": runner.counters,
    }
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("requests_total", "summary")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
