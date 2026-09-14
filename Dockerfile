FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# No CMD/ENTRYPOINT — docker-compose.yml sets a different command per
# service (api, worker, crawl-worker, one-off scripts) from this same image.
