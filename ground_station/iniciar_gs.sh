#!/usr/bin/env bash
# Liga a ground station inteira neste Mac:
#   1. dashboard            http://localhost:8000          (tela do operador/equipe)
#   2. gs_video.py          UDP :5000 -> WHIP no servidor + copias 5001 (detector) e 5002 (POV cru)
#   3. ver_video.py         POV cru em http://localhost:8092/stream (reserva, sem depender do detector)
#   4. Pi                   reinicia o camera_server mandando H.264 para ESTE Mac (:5000)
#   5. detector_pc.py       YOLO lendo udp://127.0.0.1:5001, anotado em http://localhost:8091/stream
#
#   ./iniciar_gs.sh                     # tudo; servidor/id/senha vem do config_local.json
#   ./iniciar_gs.sh --sem-servidor      # sem publicar no servidor (treino no lab)
#   SEM_PI=1 ./iniciar_gs.sh            # nao mexe no Pi (ex.: robo_simulado.py)
#   SEM_DETECTOR=1 ./iniciar_gs.sh      # dashboard mostra o POV cru (:8092)
#   PI_IP=192.168.0.50 ./iniciar_gs.sh  # IP do Pi na rede da arena
#
# Parar tudo: ./parar_gs.sh    Logs: ground_station/logs/
set -euo pipefail

GS="$(cd "$(dirname "$0")" && pwd)"
RAIZ="$(cd "$GS/.." && pwd)"
PY="$GS/.venv/bin/python"
PY_DET="$RAIZ/visao/pc/.venv/bin/python"
LOGS="$GS/logs"
PI_IP="${PI_IP:-192.168.1.70}"
PI_USER="${PI_USER:-puc}"
TAG_MM="${TAG_MM:-42}"
TAG_FAMILIAS="${TAG_FAMILIAS:-25h9}"

say()  { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[aviso] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[erro] %s\033[0m\n' "$*" >&2; exit 1; }

[ -x "$PY" ] || die "falta o venv: cd ground_station && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt"
mkdir -p "$LOGS"
"$GS/parar_gs.sh" >/dev/null 2>&1 || true

# IP deste Mac na rede atual (o Pi manda o video para ca)
MEU_IP="${MEU_IP:-$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)}"
[ -n "$MEU_IP" ] || die "nao achei o IP do Mac; rode com MEU_IP=x.x.x.x"

rodar() {  # nome, comando...
  local nome="$1"; shift
  nohup "$@" > "$LOGS/$nome.log" 2>&1 &
  echo $! > "$LOGS/$nome.pid"
}

say "dashboard em http://localhost:8000"
rodar dashboard python3 -u "$RAIZ/dashboard/servidor.py"
sleep 1

say "relay de video (Mac $MEU_IP:5000)"
rodar gs_video "$PY" -u "$GS/gs_video.py" --local udp://127.0.0.1:5001 --local udp://127.0.0.1:5002 "$@"

say "POV cru em http://localhost:8092/stream"
rodar ver_video "$PY" -u "$GS/ver_video.py" udp://127.0.0.1:5002 --porta 8092

if [ -z "${SEM_PI:-}" ]; then
  say "Pi $PI_USER@$PI_IP: camera_server com H.264 para $MEU_IP:5000"
  ssh -o ConnectTimeout=5 "$PI_USER@$PI_IP" \
    "bash yolo/robotica/iniciar_servidor.sh 8080 --tag-mm $TAG_MM --tag-familias $TAG_FAMILIAS --h264 udp://$MEU_IP:5000" \
    || warn "nao consegui iniciar o Pi; o relay fica esperando video em :5000"
fi

VC="http://127.0.0.1:8092/stream"
if [ -z "${SEM_DETECTOR:-}" ] && [ -x "$PY_DET" ]; then
  say "detector (YOLO) anotado em http://localhost:8091/stream"
  rodar detector "$PY_DET" -u "$RAIZ/visao/pc/detector_pc.py" --pi "$PI_IP" --udp udp://127.0.0.1:5001 --saida-mjpeg 8091 --sem-janela
  VC="http://127.0.0.1:8091/stream"
fi
curl -s -X POST localhost:8000/api/estado -d "{\"video\": {\"vc\": \"$VC\"}}" >/dev/null || warn "dashboard nao respondeu"

sleep 4
say "estado do relay:"
tail -n 6 "$LOGS/gs_video.log" || true
echo
say "pronto. Dashboard: http://localhost:8000  (F = tela cheia)   Logs: $LOGS   Parar: $GS/parar_gs.sh"
