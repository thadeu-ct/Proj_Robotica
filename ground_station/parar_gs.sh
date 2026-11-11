#!/usr/bin/env bash
# Para o que o iniciar_gs.sh ligou neste Mac (nao mexe no Pi).
# SIGINT primeiro (o relay encerra a sessao WHIP no servidor); quem ignorar recebe SIGTERM.
GS="$(cd "$(dirname "$0")" && pwd)"
for f in "$GS"/logs/*.pid; do
  [ -e "$f" ] || continue
  pid="$(cat "$f")"
  nome="$(basename "$f" .pid)"
  if kill -0 "$pid" 2>/dev/null; then
    kill -INT "$pid" 2>/dev/null
    for _ in 1 2 3 4 5 6; do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -0 "$pid" 2>/dev/null && kill -TERM "$pid" 2>/dev/null
    echo "parado: $nome ($pid)"
  fi
  rm -f "$f"
done
