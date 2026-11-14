"""Offline evaluation of the deployed CorpOne assistant (release gate).

  python evals/run_offline_eval.py --runtime-arn <arn> --region ap-southeast-1 [--batch] [--min-pass 0.9]

Needs Cognito access tokens for one user per role in OFFICER_TOKEN and OPS_TOKEN. Each scenario in
evals/dataset.jsonl runs in its own runtime session. Local checks: expected tools called, forbidden tools not
called, no forbidden text in the answer. With --batch, the same sessions are also scored by AgentCore
Evaluations (StartBatchEvaluation) with the scenario's assertions and expected tool trajectory as ground
truth. Exits non-zero when the local pass rate is below --min-pass, so CI can block a release.
"""
import argparse
import json
import os
import sys
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BATCH_EVALUATORS = ["Builtin.GoalSuccessRate", "Builtin.TrajectoryAnyOrderMatch", "Builtin.Correctness",
                    "Builtin.Helpfulness"]


def load(path=HERE / "dataset.jsonl"):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def score(case: dict, results: list[dict]) -> dict:
    """results: one runtime response per turn."""
    used = [t for r in results for t in r.get("metadata", {}).get("tools_used", [])]
    answer = " ".join(r.get("response", "") for r in results)
    checks = {
        "status_ok": all(r.get("status") == "success" for r in results),
        "expected_tools": all(t in used for t in case["expected_tools"]),
        "no_forbidden_tools": not any(t in used for t in case["forbidden_tools"]),
        "no_forbidden_text": not any(s.lower() in answer.lower() for s in case["must_not_contain"]),
    }
    return {"id": case["id"], "role": case["role"], "passed": all(checks.values()), "checks": checks, "tools_used": used}


def invoke(region, runtime_arn, token, session_id, prompt, timeout=120):
    import httpx
    url = (f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
           f"{urllib.parse.quote(runtime_arn, safe='')}/invocations?qualifier=DEFAULT")
    r = httpx.post(url, json={"prompt": prompt}, timeout=timeout,
                   headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id})
    r.raise_for_status()
    return r.json()


def batch_request(runtime_name, runtime_id, sessions, started, ended, name=None):
    """StartBatchEvaluation request scoring the given sessions against their ground truth."""
    return {
        "batchEvaluationName": name or f"corpone_offline_{started:%Y%m%d%H%M%S}",
        "evaluators": [{"evaluatorId": e} for e in BATCH_EVALUATORS],
        "dataSourceConfig": {"cloudWatchLogs": {
            "serviceNames": [f"{runtime_name}.DEFAULT"],
            "logGroupNames": ["aws/spans", f"/aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT"],
            "filterConfig": {"sessionIds": [s["session_id"] for s in sessions],
                             "timeRange": {"startTime": started, "endTime": ended}}}},
        "evaluationMetadata": {"sessionMetadata": [
            {"sessionId": s["session_id"], "testScenarioId": s["case"]["id"],
             "groundTruth": {"inline": {"assertions": [{"text": a} for a in s["case"]["assertions"]],
                                        "expectedTrajectory": {"toolNames": s["case"]["expected_tools"]}}},
             "metadata": {"role": s["case"]["role"]}} for s in sessions]},
        "description": "CorpOne assistant offline release gate",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runtime-arn", required=True)
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "ap-southeast-1"))
    ap.add_argument("--batch", action="store_true", help="also start an AgentCore batch evaluation")
    ap.add_argument("--min-pass", type=float, default=0.9)
    ap.add_argument("--out", default=str(HERE / "results"))
    a = ap.parse_args(argv)
    tokens = {"officer": os.environ.get("OFFICER_TOKEN", ""), "ops": os.environ.get("OPS_TOKEN", "")}
    if not all(tokens.values()):
        sys.exit("Set OFFICER_TOKEN and OPS_TOKEN (Cognito access tokens for a user in each group)")
    started = datetime.now(timezone.utc)
    sessions, scores = [], []
    for case in load():
        sid = f"eval-{case['id']}-{uuid.uuid4().hex}"[:100]
        results = [invoke(a.region, a.runtime_arn, tokens[case["role"]], sid, turn) for turn in case["turns"]]
        s = score(case, results)
        scores.append(s)
        sessions.append({"session_id": sid, "case": case})
        print(f"{'PASS' if s['passed'] else 'FAIL'}  {case['id']:32} tools={s['tools_used']}")
    rate = sum(s["passed"] for s in scores) / len(scores)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    report = {"started": started.isoformat(), "pass_rate": rate, "results": scores}
    if a.batch:
        import boto3
        runtime_id = a.runtime_arn.rsplit("/", 1)[-1]
        runtime_name = runtime_id.rsplit("-", 1)[0]
        req = batch_request(runtime_name, runtime_id, sessions, started - timedelta(minutes=1),
                            datetime.now(timezone.utc) + timedelta(minutes=30))
        resp = boto3.client("bedrock-agentcore", region_name=a.region).start_batch_evaluation(**req)
        report["batch_evaluation"] = {k: v for k, v in resp.items() if k != "ResponseMetadata"}
        print("Batch evaluation started:", report["batch_evaluation"])
    out = Path(a.out) / f"offline_{started:%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nPass rate {rate:.0%} (gate {a.min_pass:.0%}) · report {out}")
    sys.exit(0 if rate >= a.min_pass else 1)


if __name__ == "__main__":
    main()
