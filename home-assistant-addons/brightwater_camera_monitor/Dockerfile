FROM ghcr.io/home-assistant/amd64-base:3.22

RUN apk add --no-cache python3

WORKDIR /app
COPY camera_monitor.py /app/camera_monitor.py
COPY camera_backends.py /app/camera_backends.py
COPY camera_warm_agent.py /app/camera_warm_agent.py
COPY camera_monitor.local.json /app/camera_monitor.local.json
COPY run.sh /run.sh
RUN chmod 755 /run.sh

CMD ["/run.sh"]
