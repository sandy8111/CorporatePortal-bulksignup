"""Which Gateway tools each role may see. Mirrors infra/policies (Cedar) as defence in depth: the agent never
offers a tool the Gateway would refuse, and the Gateway refuses it even if the agent tried."""
DELIMITER = "___"
ROLE_TOOLS = {
    "officer": {"listPlans", "validateUen"},
    "ops": {"listPlans", "validateUen", "getInventorySummary", "checkBlacklist", "getAccountStatus",
            "retryProvisioning"},
}


def plain_name(tool_name: str) -> str:
    return tool_name.split(DELIMITER, 1)[1] if DELIMITER in tool_name else tool_name


def for_role(tools: list, role: str) -> list:
    allowed = ROLE_TOOLS.get(role, set())
    return [t for t in tools if plain_name(getattr(t, "tool_name", "")) in allowed]
