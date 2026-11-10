#!/usr/bin/env bash
# Roda NO Pi: inicia o camera_server.py em segundo plano (tmux "cam") e espera a camera transmitir.
# O iniciar.sh do computador chama este script sozinho. Para rodar a mao, no Pi:
#     cd ~/yolo/robotica && bash iniciar_servidor.sh
# Ver a saida do servidor: tmux attach -t cam   (sair sem parar: Ctrl+B, depois D)
set -u
PORTA="${1:-8080}"
export LC_ALL=C.UTF-8 LANG=C.UTF-8
cd "$(dirname "$0")" || exit 1

PY="$HOME/yolo/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
if ! "$PY" -c "import cv2" </dev/null 2>/dev/null; then
  echo "[erro] OpenCV nao encontrado em $PY. Rode no Pi: sudo apt install -y python3-opencv"
  exit 2
fi
echo "   python: $PY ($("$PY" -c 'import cv2;print("opencv",cv2.__version__)' </dev/null))"

# o Wi-Fi do Pi derrubava o SSH com o power save ligado
if sudo -n true </dev/null 2>/dev/null; then
  sudo -n iw wlan0 set power_save off </dev/null 2>/dev/null && echo "   power save do Wi-Fi: desligado"
else
  echo "   (aviso) sudo pede senha; se o SSH cair, rode no Pi: sudo iw wlan0 set power_save off"
fi

# libera a camera e a porta
if pgrep -f teste_visao.py >/dev/null; then
  echo "   teste_visao.py estava usando a camera: encerrando"
  pkill -f teste_visao.py
fi
tmux kill-session -t cam </dev/null 2>/dev/null
pkill -f camera_server.py 2>/dev/null
for _ in $(seq 1 20); do pgrep -f camera_server.py >/dev/null || break; sleep 0.25; done

ls /dev/video* >/dev/null 2>&1 || echo "   (aviso) nenhuma /dev/video*: a webcam esta conectada?"

: > camera_server.log
CMD="cd '$PWD' && '$PY' camera_server.py --port $PORTA 2>&1 | tee camera_server.log"
if command -v tmux >/dev/null; then
  tmux new-session -d -s cam -c "$PWD" "$CMD" </dev/null
  echo "   rodando no tmux 'cam' (ver no Pi: tmux attach -t cam ; sair sem parar: Ctrl+B depois D)"
else
  nohup bash -c "$CMD" </dev/null >/dev/null 2>&1 &
  echo "   rodando em segundo plano (log: $PWD/camera_server.log)"
fi

ok=0
for _ in $(seq 1 40); do
  if "$PY" -c "
import json, sys, urllib.request
s = json.load(urllib.request.urlopen('http://127.0.0.1:$PORTA/status', timeout=1))
sys.exit(0 if s.get('ok') and s.get('seq', 0) > 0 else 1)
" </dev/null 2>/dev/null; then ok=1; break; fi
  sleep 0.5
done
tail -n 4 camera_server.log 2>/dev/null | sed 's/^/   pi> /'
if [ "$ok" != 1 ]; then
  echo "[erro] o servidor nao comecou a mandar imagens. Veja: tmux attach -t cam  (ou cat camera_server.log)"
  exit 3
fi
echo "   camera transmitindo na porta $PORTA"
