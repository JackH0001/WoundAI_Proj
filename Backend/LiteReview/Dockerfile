# Build context: repository root. No medical bootstrap, models, data or credentials.
ARG PYTHON_IMAGE=python:3.11.16-slim-bookworm@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84
FROM ${PYTHON_IMAGE}
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY Backend/LiteReview/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.lock
COPY Backend/LiteReview/__init__.py Backend/LiteReview/contracts.py Backend/LiteReview/auth.py Backend/LiteReview/reader.py Backend/LiteReview/storage.py Backend/LiteReview/web.py Backend/LiteReview/runtime.py /app/Backend/LiteReview/
COPY Backend/LiteReview/templates /app/Backend/LiteReview/templates
COPY Backend/LiteReview/static /app/Backend/LiteReview/static
USER 65532:65532
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "1", "--timeout", "120", "--access-logfile", "/dev/null", "Backend.LiteReview.runtime:portal()"]
