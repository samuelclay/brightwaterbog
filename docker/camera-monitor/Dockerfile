FROM python:3.13-alpine

RUN apk add --no-cache chromium font-noto

RUN addgroup -S camera-monitor \
    && adduser -S -G camera-monitor -h /data camera-monitor \
    && mkdir -p /app /config /data/camera_monitor /data/chromium \
    && chown -R camera-monitor:camera-monitor /app /config /data

WORKDIR /app
COPY --chown=camera-monitor:camera-monitor tools/camera_monitor.py /app/camera_monitor.py
COPY --chown=camera-monitor:camera-monitor tools/camera_warm_agent.py /app/camera_warm_agent.py
COPY --chown=camera-monitor:camera-monitor docker/camera-monitor/run.sh /app/run.sh
RUN chmod 755 /app/run.sh

USER camera-monitor
ENV HOME=/data \
    PYTHONUNBUFFERED=1 \
    CAMERA_MONITOR_CONFIG=/config/camera_monitor.json \
    CAMERA_MONITOR_CACHE_DIR=/data/camera_monitor \
    CAMERA_MONITOR_PORT=8765 \
    CAMERA_MONITOR_WARM_AGENT_ENABLED=0

EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=4s --start-period=30s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/status', timeout=3).read()"

ENTRYPOINT ["/app/run.sh"]
