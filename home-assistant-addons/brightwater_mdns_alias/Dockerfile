FROM ghcr.io/home-assistant/amd64-base:3.21

RUN apk add --no-cache python3

WORKDIR /app
COPY mdns_alias.py /app/mdns_alias.py
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
