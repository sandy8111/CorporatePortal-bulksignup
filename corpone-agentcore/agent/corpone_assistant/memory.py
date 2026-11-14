"""Short-term memory (AgentCore Memory events) for one conversation.

One event per turn holds the user message and the assistant reply, both redacted. Events expire after the
memory's eventExpiryDuration (7 days, set in infra). No long-term strategies are configured, so nothing is
extracted or kept beyond the session's events.
"""
from datetime import datetime, timezone

from corpone_assistant.redact import redact


class ShortTermMemory:
    def __init__(self, client, memory_id: str):
        self.client = client          # boto3 client("bedrock-agentcore")
        self.memory_id = memory_id

    @property
    def enabled(self) -> bool:
        return bool(self.memory_id)

    def history(self, actor_id: str, session_id: str, turns: int) -> list[dict]:
        """Last `turns` turns as Strands messages, oldest first."""
        if not self.enabled or not session_id:
            return []
        events, token = [], None
        while True:
            params = {"memoryId": self.memory_id, "actorId": actor_id, "sessionId": session_id,
                      "includePayloads": True, "maxResults": 100}
            if token:
                params["nextToken"] = token
            page = self.client.list_events(**params)
            events += page.get("events", [])
            token = page.get("nextToken")
            if not token:
                break
        events.sort(key=lambda e: e["eventTimestamp"])
        messages = []
        for e in events[-turns:] if turns > 0 else []:
            for p in e.get("payload", []):
                conv = p.get("conversational")
                if conv and conv.get("role") in ("USER", "ASSISTANT"):
                    messages.append({"role": conv["role"].lower(), "content": [{"text": conv["content"]["text"]}]})
        return messages

    def save_turn(self, actor_id: str, session_id: str, user_text: str, assistant_text: str) -> None:
        if not self.enabled or not session_id:
            return
        self.client.create_event(
            memoryId=self.memory_id, actorId=actor_id, sessionId=session_id,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"conversational": {"role": "USER", "content": {"text": redact(user_text)}}},
                     {"conversational": {"role": "ASSISTANT", "content": {"text": redact(assistant_text)}}}])
