# Minimal image for local/demo use. SQLite lives inside the container, so
# data doesn't survive a rebuild unless you mount a volume over /app/runway.db
# — see the "Docker" section in README.md.
FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
