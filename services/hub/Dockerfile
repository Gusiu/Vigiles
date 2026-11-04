# Build from the repository root: docker build -f services/hub/Dockerfile .
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.11.32
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY libs libs
COPY services services
COPY tools tools
RUN uv sync --locked --no-dev --no-editable --package vigiles-hub

ENV PATH=/opt/venv/bin:$PATH
RUN useradd --uid 10001 --no-create-home vigiles
USER vigiles
WORKDIR /app
EXPOSE 8003
CMD ["sh", "-c", "vigiles-hub-migrate && exec vigiles-hub"]
