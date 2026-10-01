# Queue worker image (integrations/bazarr). Library versions pinned as validated on a 2-core Celeron J4025 NAS.
FROM python:3.11-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY pyproject.toml README.md LICENSE /src/
COPY src /src/src
RUN pip install --no-cache-dir --no-deps /src
CMD ["semantic-subsync-worker", "run"]
