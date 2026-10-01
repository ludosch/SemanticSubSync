# Worker image. Same base and pinned library versions as the NAS proof of concept (J4025, 2026-10-01).
FROM python:3.11-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY pyproject.toml README.md /src/
COPY src /src/src
RUN pip install --no-cache-dir --no-deps /src
CMD ["semantic-subsync-worker", "run"]
