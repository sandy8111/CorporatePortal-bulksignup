// Incident kill switch: denies every tool call on every gateway attached to the engine. Not deployed by
// default; create it with infra/deploy.py --kill-switch on, delete it to restore service.
forbid(
  principal,
  action,
  resource is AgentCore::Gateway
);
