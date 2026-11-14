// Retrying provisioning changes data: ops only, and only for a well-formed CorpOne account number.
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"{{TARGET}}___retryProvisioning",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("cognito:groups") &&
  principal.getTag("cognito:groups") like "*\"ops\"*" &&
  context.input.account_number like "CA*"
};
