# Queue worker image (integrations/bazarr). requirements.txt pins every dependency with its hash,
# exported from uv.lock:
#   uv export --frozen --no-dev --extra model --no-emit-project --format requirements-txt -o requirements.txt
FROM python:3.11-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt
COPY pyproject.toml README.md LICENSE /src/
COPY src /src/src
RUN pip install --no-cache-dir --no-deps /src && rm -rf /src
# An unprivileged user (uid/gid 1000); `user:` in compose gives the owner of the media files instead.
# /models holds the downloaded models: mount it to keep them when the container is recreated.
RUN groupadd --gid 1000 semsync \
 && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /models --shell /usr/sbin/nologin semsync \
 && mkdir -p /models && chmod 1777 /models
ENV SEMSYNC_DIR=/data/.semsync \
    SEMSYNC_MODEL_DIR=/models \
    HF_HOME=/models/hf \
    FASTEMBED_CACHE_PATH=/models/hf
USER 1000:1000
CMD ["semantic-subsync-worker", "run"]
