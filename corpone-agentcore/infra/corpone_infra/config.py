"""Deployment settings, from environment variables (see infra/.env.example). Secrets are never written to disk."""
import os
import re
from dataclasses import dataclass, field


def _env(name, default=""):
    return os.environ.get(name, default).strip()


@dataclass
class InfraConfig:
    project: str = field(default_factory=lambda: _env("PROJECT", "corpone-agentcore"))
    region: str = field(default_factory=lambda: _env("AWS_REGION", "ap-southeast-1"))
    account_id: str = field(default_factory=lambda: _env("AWS_ACCOUNT_ID"))
    portal_gateway_url: str = field(default_factory=lambda: _env("PORTAL_GATEWAY_URL"))   # https://<host>/gateway
    model_id: str = field(default_factory=lambda: _env("MODEL_ID"))
    agent_image_uri: str = field(default_factory=lambda: _env("AGENT_IMAGE_URI"))          # ECR image, linux/arm64
    policy_mode: str = field(default_factory=lambda: _env("POLICY_MODE", "LOG_ONLY"))      # LOG_ONLY, then ENFORCE
    eval_sampling_percent: float = field(default_factory=lambda: float(_env("EVAL_SAMPLING_PERCENT", "10")))
    memory_expiry_days: int = field(default_factory=lambda: int(_env("MEMORY_EXPIRY_DAYS", "7")))
    target_name: str = "CorpOnePortal"

    # ---------------------------------------------------------------- derived names (AWS naming rules differ per service)
    @property
    def ident(self) -> str:
        """Letters, digits and underscores, starting with a letter (Memory, Runtime, eval config, policy engine)."""
        s = re.sub(r"[^A-Za-z0-9_]", "_", self.project)
        return s if s[:1].isalpha() else f"c_{s}"

    @property
    def dashed(self) -> str:
        return re.sub(r"[^A-Za-z0-9-]", "-", self.project).strip("-")

    def names(self) -> dict:
        return {
            "user_pool": f"{self.dashed}-users",
            "app_client": f"{self.dashed}-assistant",
            "gateway_role": f"{self.dashed}-gateway-role",
            "runtime_role": f"{self.dashed}-runtime-role",
            "eval_role": f"{self.dashed}-eval-role",
            "credential_provider": f"{self.dashed}-portal-api-key",
            "policy_engine": f"{self.ident}_policy",
            "gateway": f"{self.dashed}-gateway",
            "memory": f"{self.ident}_stm",
            "runtime": f"{self.ident}_assistant",
            "online_eval": f"{self.ident}_online_eval",
        }

    def problems(self, for_apply: bool) -> list[str]:
        p = []
        if not self.portal_gateway_url.startswith("https://"):
            p.append("PORTAL_GATEWAY_URL must be the portal's public HTTPS gateway base, e.g. https://portal.example.sg/gateway")
        if self.policy_mode not in ("LOG_ONLY", "ENFORCE"):
            p.append("POLICY_MODE must be LOG_ONLY or ENFORCE")
        if not 0 < self.eval_sampling_percent <= 100:
            p.append("EVAL_SAMPLING_PERCENT must be between 0 and 100")
        if not 7 <= self.memory_expiry_days <= 3650:
            p.append("MEMORY_EXPIRY_DAYS must be 7-3650")
        if for_apply:
            for name, val in (("MODEL_ID", self.model_id), ("AGENT_IMAGE_URI", self.agent_image_uri)):
                if not val:
                    p.append(f"{name} is required to deploy")
            if not os.environ.get("PORTAL_API_KEY", "").startswith("cpk_"):
                p.append("PORTAL_API_KEY (a cpk_... key issued in the portal back office) is required; it is passed "
                         "to AgentCore Identity and never written to disk")
        return p
