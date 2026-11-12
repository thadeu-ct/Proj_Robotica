#!/usr/bin/env bash
# Envia para o Raspberry Pi TUDO o que roda nele (as copias em ~/yolo/robotica ficam iguais ao repo).
# Nao reinicia nada, a nao ser com --reiniciar.
#
#   ./subir_pi.sh                    Pi em 192.168.1.70 (ou PI_IP=...)
#   ./subir_pi.sh 192.168.1.80
#   ./subir_pi.sh --reiniciar        envia e reinicia o servidor de camera (tmux "cam", sem H.264;
#                                    para a cadeia da competicao use ground_station/iniciar_gs.sh)
#
# O que vai:  visao/pi/  -> ~/yolo/robotica/          (camera_server, apriltag_pi, iniciar_servidor, calibracao)
#             controle/  -> ~/yolo/robotica/controle/ (seguir_tag, protocolo_esp32, config_seguir)
#             mqtt/      -> ~/yolo/robotica/mqtt/     (robo_agente, config_robo + config_local da ground station)
# Os modelos do YOLO nao vao: o YOLO roda no Mac.
set -euo pipefail

RAIZ="$(cd "$(dirname "$0")" && pwd)"
PI_IP="${PI_IP:-192.168.1.70}"
PI_USER="${PI_USER:-puc}"
REINICIAR=0
for a in "$@"; do
  case "$a" in
    --reiniciar) REINICIAR=1 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) PI_IP="$a" ;;
  esac
done
DEST="$PI_USER@$PI_IP"

say() { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m[erro] %s\033[0m\n' "$*" >&2; exit 1; }

say "Conectando em $DEST"
ssh -o ConnectTimeout=6 "$DEST" true </dev/null || die "nao conectou em $DEST. Mesma rede do Pi? IP certo (no Pi: hostname -I)?"

VISAO=("$RAIZ/visao/pi/camera_server.py" "$RAIZ/visao/pi/apriltag_pi.py" "$RAIZ/visao/pi/iniciar_servidor.sh" "$RAIZ/visao/pi/camera_lab.json")
[ -f "$RAIZ/visao/pi/camera_pi.json" ] && VISAO+=("$RAIZ/visao/pi/camera_pi.json")
CONTROLE=("$RAIZ/controle/seguir_tag.py" "$RAIZ/controle/protocolo_esp32.py" "$RAIZ/controle/config_seguir.json")
MQTT=("$RAIZ/mqtt/robo_agente.py" "$RAIZ/mqtt/config_robo.json")

say "Enviando visao (${#VISAO[@]}), controle (${#CONTROLE[@]}) e mqtt (${#MQTT[@]} arquivos)"
ssh "$DEST" "mkdir -p yolo/robotica/controle yolo/robotica/mqtt" </dev/null
scp -q "${VISAO[@]}" "$DEST:yolo/robotica/"
scp -q "${CONTROLE[@]}" "$DEST:yolo/robotica/controle/"
scp -q "${MQTT[@]}" "$DEST:yolo/robotica/mqtt/"
if [ -f "$RAIZ/ground_station/config_local.json" ]; then
  scp -q "$RAIZ/ground_station/config_local.json" "$DEST:yolo/robotica/mqtt/config_local.json"   # servidor/id/senha (fora do git)
  echo "   config_local.json (servidor, id, senha) enviado para o agente MQTT"
fi

say "Conferindo no Pi"
ssh "$DEST" 'bash -s' <<'REMOTO'
set -u
cd ~/yolo/robotica
ok=1
for f in camera_server.py apriltag_pi.py controle/seguir_tag.py controle/protocolo_esp32.py mqtt/robo_agente.py; do
  python3 -m py_compile "$f" 2>/dev/null && echo "   ok   $f" || { echo "   ERRO $f"; ok=0; }
done
python3 controle/protocolo_esp32.py >/dev/null && echo "   ok   autoteste do protocolo" || { echo "   ERRO autoteste do protocolo"; ok=0; }
~/yolo/.venv/bin/python -c "import cv2, serial, paho.mqtt" 2>/dev/null && echo "   ok   venv (opencv, pyserial, paho-mqtt)" || echo "   AVISO: falta pacote no ~/yolo/.venv (opencv/pyserial/paho-mqtt)"
ESP=$(ls /dev/serial/by-id/* /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | head -1 || true)
[ -n "$ESP" ] && echo "   ESP32 no USB: $ESP" || echo "   (nenhum ESP32 no USB agora)"
[ "$ok" = 1 ] || exit 1
REMOTO

if [ "$REINICIAR" = 1 ]; then
  say "Reiniciando o servidor de camera"
  ssh "$DEST" "bash yolo/robotica/iniciar_servidor.sh 8080 --tag-mm 42 --tag-familias 25h9" </dev/null
fi

cat <<FIM

Pronto. No Pi ($DEST):
  camera + tag:        bash yolo/robotica/iniciar_servidor.sh 8080          (ou ./subir_pi.sh --reiniciar)
  seguir a tag:        cd ~/yolo/robotica/controle && python3 seguir_tag.py             (simulado)
                       python3 seguir_tag.py --motores                                   (ESP32 no USB)
  cadeia completa:     no Mac, PI_IP=$PI_IP ground_station/iniciar_gs.sh  (MOTORES=1 para mover de verdade)
FIM
