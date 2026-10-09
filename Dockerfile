# Docker Hub's official python image, via Google's public mirror: the same
# image without Docker Hub's auth server, whose 504s failed the deploy build
# on 2026-10-09.
FROM mirror.gcr.io/library/python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# No CMD/ENTRYPOINT — docker-compose.yml sets a different command per
# service (api, worker, crawl-worker, one-off scripts) from this same image.
