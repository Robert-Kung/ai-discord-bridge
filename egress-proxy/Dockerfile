# Minimal default-deny CONNECT proxy for egress containment (phase 1).
# alpine + tinyproxy — no third-party image dependency (matches the step-0 spike).
FROM alpine:3.20
RUN apk add --no-cache tinyproxy
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY filter /etc/tinyproxy/filter
EXPOSE 8888
# Foreground, log to stdout so `docker logs` / deny-log grep works.
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
