# Container image of the import worker (infrastructure-specification.md, "Compute model").
#
# BASE_IMAGE defaults to a tag so the file builds anywhere. The pipeline replaces it with the
# reviewed image *digest*, for example:
#   docker build --build-arg BASE_IMAGE=python:3.12-slim@sha256:<digest> .
# (cicd-pipeline.md). A digest cannot be read offline, so none is written here.
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install . && useradd --create-home --uid 10001 worker

# The worker keeps its download folder under the system temp folder (the task's 30 GiB disk).
USER worker

# Exec form: the worker is process 1 and receives SIGTERM directly (scale-in, scalability-design.md).
ENTRYPOINT ["model-import-worker"]
