#!/bin/bash
# Worker : surveille les demandes de script déposées par le dashboard (work/inbox/*/request_*.json)
# et prévient dès qu'une nouvelle demande attend une réponse (reply_*.json).
cd "$(dirname "$0")/.." || exit 1
seen=""
while true; do
  for f in work/inbox/*/request_*.json; do
    [ -e "$f" ] || continue
    r="${f/request_/reply_}"
    if [ ! -e "$r" ] && [[ "$seen" != *"$f"* ]]; then
      seen="$seen $f"; echo "NOUVELLE DEMANDE DE SCRIPT: $f"
    fi
  done
  sleep 2
done
