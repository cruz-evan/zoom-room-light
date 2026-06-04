FROM python:3.12-slim

ENV HOST=0.0.0.0
ENV PORT=5050
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY zoom_light_webhook.py zoom_schedule.py ./
COPY zoom_light ./zoom_light

EXPOSE 5050

CMD ["python", "zoom_light_webhook.py"]
