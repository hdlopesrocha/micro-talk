#!/bin/sh
# Prepare writable Docker volumes, then run the service as the unprivileged
# MicroTalk account.
#
# Docker creates named volumes as root-owned directories. When several
# containers mount the same volume (for example, the gateway reads the TTS
# volume while the TTS service writes it), a read-only mount can win the race
# and leave the volume root-owned. The image entrypoint starts as root only for
# this ownership check and immediately drops to UID/GID 10001.
set -eu

SERVICE_UID=10001
SERVICE_GID=10001
SERVICE_HOME=/home/microtalk

if [ "$(id -u)" = "0" ]; then
    for directory in ${MICROTALK_WRITABLE_DIRS:-}; do
        [ -d "$directory" ] || continue
        if [ "$(stat -c '%u' "$directory")" != "$SERVICE_UID" ]; then
            chown -R "$SERVICE_UID:$SERVICE_GID" "$directory"
        fi
    done
    exec setpriv \
        --reuid="$SERVICE_UID" \
        --regid="$SERVICE_GID" \
        --init-groups \
        env HOME="$SERVICE_HOME" \
        "$@"
fi

exec "$@"
