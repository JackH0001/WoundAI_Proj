# Independent native service profile; never starts the medical/research app.
ARG PYTHON_IMAGE=python:3.11.16-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84
FROM ${PYTHON_IMAGE}
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock && pip check
# This context must be made by stage_lite_native_candidate.py (exact allowlist).
COPY . .
RUN python verify_native_package.py
RUN useradd -m -u 1000 woundai
USER woundai
ENV PORT=8080
EXPOSE 8080
# No preload/forked model copies. Secret/config pins are supplied explicitly
# at deployment; the image contains no account keys or environment defaults.
CMD exec gunicorn --bind "0.0.0.0:${PORT}" --workers 1 --threads 2 \
    --timeout 120 --graceful-timeout 30 --worker-tmp-dir /tmp \
    --access-logfile - --error-logfile - 'lite_native_startup:application()'
