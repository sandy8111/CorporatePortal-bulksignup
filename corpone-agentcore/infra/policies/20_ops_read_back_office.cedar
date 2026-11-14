// Inventory, blacklist and account status cover every company, so only CorpOne ops may read them.
// Cognito puts the user's groups in the access token's "cognito:groups" claim; AgentCore turns every claim
// into a string tag, so an array claim is matched as JSON text.
permit(
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"{{TARGET}}___getInventorySummary",
    AgentCore::Action::"{{TARGET}}___checkBlacklist",
    AgentCore::Action::"{{TARGET}}___getAccountStatus"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("cognito:groups") &&
  principal.getTag("cognito:groups") like "*\"ops\"*"
};
