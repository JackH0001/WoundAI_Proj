# Build context: repository root. Explicit COPY excludes app data and keys.
ARG PYTHON_IMAGE=python:3.11.16-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84
FROM ${PYTHON_IMAGE}
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WEB_CONCURRENCY=1
COPY Backend/AccessManager/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.lock
COPY Backend/AccessManager/__init__.py Backend/AccessManager/accounts.py Backend/AccessManager/audit.py Backend/AccessManager/authentication.py Backend/AccessManager/iap_adapter.py Backend/AccessManager/iap_policy.py Backend/AccessManager/runtime.py Backend/AccessManager/sessions.py Backend/AccessManager/web.py /app/Backend/AccessManager/
COPY Backend/AccessManager/static /app/Backend/AccessManager/static
USER 65532:65532
EXPOSE 8080
# In-memory sessions require a single process; no preload/fork or request threads.
# Cloud deployment must additionally enforce max-instances=1 and concurrency=1.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "1", "--worker-class", "sync", "--timeout", "180", "--access-logfile", "/dev/null", "Backend.AccessManager.runtime:cloud_app()"]
