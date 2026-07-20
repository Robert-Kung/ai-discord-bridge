# Minimal default-deny CONNECT proxy for egress containment.
# alpine + tinyproxy — no third-party image dependency (matches the step-0 spike).
# FILTER selects the allow-list at build time: `filter` (phase 1, combined),
# `filter.anthropic` (phase-2 executor side), `filter.discord` (phase-2 frontend side).
#
# EXTRA_FILTER optionally appends an opt-in filter file (currently only
# `filter.pypi`). It is empty by default, and it is accepted ONLY on top of
# `filter.anthropic`: appending read-only index hosts to the frontend filter or to
# the combined single-container filter would hand index egress to the container
# holding the Discord token, so those builds fail here rather than silently
# producing a widened proxy.
FROM alpine:3.20
ARG FILTER=filter
ARG EXTRA_FILTER=""
RUN apk add --no-cache tinyproxy
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY filter filter.anthropic filter.discord filter.pypi /tmp/filters/
RUN set -eu; \
    if [ -n "$EXTRA_FILTER" ] && [ "$FILTER" != "filter.anthropic" ]; then \
        echo "build refused: EXTRA_FILTER=$EXTRA_FILTER requires FILTER=filter.anthropic (executor side of a split deployment), got FILTER=$FILTER" >&2; \
        exit 1; \
    fi; \
    cp "/tmp/filters/$FILTER" /etc/tinyproxy/filter; \
    if [ -n "$EXTRA_FILTER" ]; then \
        printf '\n' >>/etc/tinyproxy/filter; \
        cat "/tmp/filters/$EXTRA_FILTER" >>/etc/tinyproxy/filter; \
    fi; \
    rm -rf /tmp/filters
EXPOSE 8888
# Foreground, log to stdout so `docker logs` / deny-log grep works.
CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
