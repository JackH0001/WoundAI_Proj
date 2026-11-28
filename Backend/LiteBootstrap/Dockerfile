# Private one-shot bootstrap. No HTTP server, model, secret or default activation.
ARG PYTHON_IMAGE=python:3.11.16-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84
FROM ${PYTHON_IMAGE}
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA=true
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && pip check
COPY --chmod=a=rX . .
RUN useradd -m -u 1000 woundai
USER woundai
RUN --network=none python verify_bootstrap_package.py
ENTRYPOINT ["python", "-B", "lite_fresh_bootstrap_runner.py"]
