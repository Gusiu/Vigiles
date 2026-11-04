# Build from the repository root: docker build -f services/field_app/Dockerfile .
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.11.32
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY libs libs
COPY services services
COPY tools tools
RUN uv sync --locked --no-dev --no-editable --package vigiles-field-app

ENV PATH=/opt/venv/bin:$PATH
ENV FIELD_APP_DB_PATH=/var/lib/vigiles/field_app.sqlite
RUN useradd --uid 10001 --no-create-home vigiles && mkdir -p /var/lib/vigiles && chown vigiles /var/lib/vigiles
USER vigiles
WORKDIR /app
EXPOSE 8002
CMD ["vigiles-field-app"]
