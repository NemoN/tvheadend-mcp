FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE requirements.lock ./
COPY src ./src
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip install --no-cache-dir --no-deps .
USER 65534:65534
ENTRYPOINT ["tvheadend-mcp"]
