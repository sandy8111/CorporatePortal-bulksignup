import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import botocore.session
from botocore.validate import ParamValidator

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "evals"), str(ROOT / "agent"), str(ROOT / "portal")]
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

import run_offline_eval as ev  # noqa: E402
from corpone_assistant.tools import ROLE_TOOLS  # noqa: E402


def test_dataset_is_consistent_with_roles_and_policy():
    cases = ev.load()
    assert len(cases) == 12 and len({c["id"] for c in cases}) == 12
    assert {c["role"] for c in cases} == {"officer", "ops"}
    for c in cases:
        allowed = ROLE_TOOLS[c["role"]]
        assert set(c["expected_tools"]) <= allowed, c["id"]          # never expect a tool the policy denies
        assert c["assertions"] and c["turns"]
        assert not set(c["expected_tools"]) & set(c["forbidden_tools"]), c["id"]
    officer_forbidden = {t for c in cases if c["role"] == "officer" for t in c["forbidden_tools"]}
    assert {"retryProvisioning", "checkBlacklist", "getInventorySummary"} <= officer_forbidden


def test_scoring():
    case = next(c for c in ev.load() if c["id"] == "officer-no-blacklist-lookup")
    good = [{"status": "success", "response": "I can't check people.", "metadata": {"tools_used": []}}]
    bad = [{"status": "success", "response": "S1234567D is clean", "metadata": {"tools_used": ["checkBlacklist"]}}]
    assert ev.score(case, good)["passed"]
    s = ev.score(case, bad)
    assert not s["passed"] and not s["checks"]["no_forbidden_tools"] and not s["checks"]["no_forbidden_text"]
    two = next(c for c in ev.load() if c["id"] == "ops-retry-confirmed")
    turns = [{"status": "success", "response": "found", "metadata": {"tools_used": ["getAccountStatus"]}},
             {"status": "success", "response": "retried", "metadata": {"tools_used": ["retryProvisioning"]}}]
    assert ev.score(two, turns)["passed"]


def test_batch_request_matches_the_aws_api_model():
    cases = ev.load()[:2]
    sessions = [{"session_id": f"eval-{c['id']}-0123456789abcdef0123456789", "case": c} for c in cases]
    now = datetime.now(timezone.utc)
    req = ev.batch_request("corpone_agentcore_assistant", "corpone_agentcore_assistant-AbCdE12345", sessions, now, now)
    meta = botocore.session.get_session().create_client("bedrock-agentcore", region_name="ap-southeast-1").meta
    shape = meta.service_model.operation_model("StartBatchEvaluation").input_shape
    report = ParamValidator().validate(req, shape)
    assert not report.has_errors(), report.generate_report()
    gt = req["evaluationMetadata"]["sessionMetadata"][0]["groundTruth"]["inline"]
    assert gt["expectedTrajectory"]["toolNames"] == cases[0]["expected_tools"] and gt["assertions"]
