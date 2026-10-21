# MTF CLI / orchestrator image: validation, API automation, AI planning and report generation.
# It contains no Android emulator and no Xcode: device runs need a host with adb or macOS + Xcode.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/mtf-venv \
    PATH=/opt/mtf-venv/bin:$PATH \
    MTF_HOME=/work

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv

WORKDIR /work
COPY pyproject.toml uv.lock README.md ./
COPY framework/mtf framework/mtf
RUN uv sync --frozen --no-dev --extra anthropic \
    && useradd --create-home --uid 10001 mtf

COPY contracts contracts
COPY config config
COPY plans plans
COPY examples examples

USER mtf
ENTRYPOINT ["mtf"]
CMD ["--help"]
