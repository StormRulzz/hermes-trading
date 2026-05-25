#!/bin/sh
# Seed /app/state from /app/defaults on first boot (volume starts empty)
for f in /app/defaults/*; do
    dest="/app/state/$(basename "$f")"
    if [ ! -f "$dest" ]; then
        cp "$f" "$dest"
    fi
done
mkdir -p /app/state/history
exec "$@"
