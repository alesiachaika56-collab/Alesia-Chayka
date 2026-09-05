FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/data/heavenly_stories.db

WORKDIR /app
COPY bot.py /app/bot.py
RUN mkdir -p /data

VOLUME ["/data"]
CMD ["python", "/app/bot.py"]
