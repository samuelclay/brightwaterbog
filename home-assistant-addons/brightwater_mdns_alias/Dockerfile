FROM ghcr.io/home-assistant/amd64-base:3.22

RUN apk add --no-cache python3

WORKDIR /app
COPY mdns_alias.py /app/mdns_alias.py
COPY run.sh /run.sh
RUN chmod 755 /run.sh

CMD ["/run.sh"]
