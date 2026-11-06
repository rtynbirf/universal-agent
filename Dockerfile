# UniversalAgent — изолированный non-root рантайм агента.
# Сборка:  docker build -t universal-agent .
# Жизнь:   docker run -d --name cua universal-agent
# Задача:  docker run --rm universal-agent task --task "текст задачи"
# Враждебный код внутри execute_python: добавьте --network none (см. SECURITY.md).

FROM python:3.12-slim

WORKDIR /agent

COPY universal_agent.py pyproject.toml ./

RUN useradd --create-home agent \
 && mkdir -p workspace memory knowledge \
 && chown -R agent:agent /agent

USER agent

ENV UA_WORKSPACE_ROOT=/agent/workspace \
    UA_MEMORY_DB=/agent/memory/agent.db \
    UA_KNOWLEDGE_DIR=/agent/knowledge \
    UA_RESULTS_LOG=/agent/results.log \
    UA_QUEUE_PATH=/agent/task_queue.txt

VOLUME ["/agent/workspace", "/agent/memory", "/agent/knowledge"]

ENTRYPOINT ["python", "universal_agent.py"]
CMD ["daemon"]
