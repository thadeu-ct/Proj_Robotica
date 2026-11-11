#!/usr/bin/env bash
# Para o servidor de camera no Pi e fecha a conexao SSH reaproveitada.
#   ./parar.sh               (Pi em 192.168.1.70)
#   ./parar.sh 192.168.1.80
PI_IP="${1:-192.168.1.70}"
PI_USER="${PI_USER:-puc}"
CTL="$HOME/.ssh/cm-robotica-%r@%h-%p"
DEST="$PI_USER@$PI_IP"
ssh -o ControlMaster=auto -o "ControlPath=$CTL" -o ConnectTimeout=8 "$DEST" \
  'tmux kill-session -t cam 2>/dev/null; pkill -f '[c]amera_server.py' 2>/dev/null; echo "servidor de camera parado"'
ssh -o "ControlPath=$CTL" -O exit "$DEST" 2>/dev/null || true
