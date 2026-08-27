FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mqtt/ mqtt/
COPY notifications/ notifications/
COPY dashboard/ dashboard/
# config/ and logs/ are volume-mounted (see docker-compose.yml)
CMD ["python", "-m", "mqtt.mac_alert_monitor"]
