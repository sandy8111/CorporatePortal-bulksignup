"""Write the agent-facing OpenAPI document for the AgentCore Gateway target.

Usage (from portal/):  python scripts/export_agent_openapi.py https://portal.example.sg/gateway > ../infra/build/openapi.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.gateway.agent_openapi import build, problems  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        sys.exit("usage: export_agent_openapi.py https://<public portal host>/gateway")
    doc = build(sys.argv[1])
    issues = problems(doc)
    if issues:
        sys.exit("not Gateway-compatible:\n  " + "\n  ".join(issues))
    json.dump(doc, sys.stdout, indent=2)
    sys.stdout.write("\n")
