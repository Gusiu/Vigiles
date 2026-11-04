# Build from the repository root: docker build -f tools/importer/Dockerfile .
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.11.32
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY libs libs
COPY services services
COPY tools tools
RUN uv sync --locked --no-dev --no-editable --package vigiles-importer

ENV PATH=/opt/venv/bin:$PATH \
    IMPORTER_DATA_DIR=/data
RUN useradd --uid 10001 --no-create-home vigiles && mkdir /data && chown vigiles /data
USER vigiles
WORKDIR /app
CMD ["vigiles-import", "watch"]
