# CorpOne assistant for AgentCore Runtime (ARM64)
FROM --platform=linux/arm64 python:3.12-slim
WORKDIR /app
COPY --chmod=644 requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chmod=755 corpone_assistant /app/corpone_assistant
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore
EXPOSE 8080
ENV PYTHONUNBUFFERED=1 \
    OTEL_PYTHON_DISABLED_INSTRUMENTATIONS=sqlalchemy,urllib3 \
    AGENT_OBSERVABILITY_ENABLED=true
# opentelemetry-instrument sends traces/logs to CloudWatch (AgentCore Observability)
CMD ["opentelemetry-instrument", "python", "-m", "corpone_assistant.app"]
