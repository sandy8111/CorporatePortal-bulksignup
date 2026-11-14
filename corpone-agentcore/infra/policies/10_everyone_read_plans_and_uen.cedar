// Any signed-in user (account officer or ops) may list plans and validate a UEN.
permit(
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"{{TARGET}}___listPlans",
    AgentCore::Action::"{{TARGET}}___validateUen"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
);
