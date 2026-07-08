# Minimal default-deny CONNECT proxy for egress containment.
# alpine + tinyproxy — no third-party image dependency (matches the step-0 spike).
# FILTER selects the allow-list at build time: `filter` (phase 1, combined),
# `filter.anthropic` (phase-2 executor side), `filter.discord` (phase-2 frontend side).
FROM alpine:3.20
ARG FILTER=filter
RUN apk add --no-cache tinyproxy
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY ${FILTER} /etc/tinyproxy/filter
EXPOSE 8888
# Foreground, log to stdout so `docker logs` / deny-log grep works.
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
