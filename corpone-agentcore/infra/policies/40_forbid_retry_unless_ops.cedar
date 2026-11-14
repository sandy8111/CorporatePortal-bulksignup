// Fail-safe: no other permit, now or later, can open retryProvisioning to non-ops users (forbid always wins).
forbid(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"{{TARGET}}___retryProvisioning",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
unless {
  principal.hasTag("cognito:groups") &&
  principal.getTag("cognito:groups") like "*\"ops\"*"
};
