#!/usr/bin/env bash
# Grava o firmware de locomocao (firmware/esp32_locomocao) no ESP32. Tudo num comando.
#
#   firmware/gravar_esp32.sh                    ESP32 no USB deste computador: compila e grava (modo normal)
#   firmware/gravar_esp32.sh --bancada          grava o TESTE DE BANCADA e abre o monitor (115200). Robo SUSPENSO!
#   firmware/gravar_esp32.sh --porta /dev/cu.usbserial-0001    escolhe a porta (padrao: detecta sozinho)
#   firmware/gravar_esp32.sh --via-pi [IP]      ESP32 ligado no USB do Pi: compila aqui e grava pelo Pi (esptool)
#   firmware/gravar_esp32.sh --via-pi 192.168.1.70 --bancada
#   firmware/gravar_esp32.sh --so-compilar      so confere se compila
#
# Precisa do arduino-cli com o core esp32 (neste Mac: ~/.local/bin/arduino-cli, core 3.3.12).
# Instalar em outro computador: https://arduino.github.io/arduino-cli/ e
#   arduino-cli config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
#   arduino-cli core update-index && arduino-cli core install esp32:esp32
set -euo pipefail

AQUI="$(cd "$(dirname "$0")" && pwd)"
SKETCH="$AQUI/esp32_locomocao"
FQBN="esp32:esp32:esp32"
CLI="$(command -v arduino-cli || echo "$HOME/.local/bin/arduino-cli")"
BANCADA=0; PORTA=""; VIA_PI=""; SO_COMPILAR=0
PI_USER="${PI_USER:-puc}"

say()  { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[erro] %s\033[0m\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --bancada) BANCADA=1 ;;
    --porta) PORTA="$2"; shift ;;
    --via-pi) VIA_PI="${PI_IP:-192.168.1.70}"; if [ $# -gt 1 ] && [[ "$2" != --* ]]; then VIA_PI="$2"; shift; fi ;;
    --so-compilar) SO_COMPILAR=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) die "opcao desconhecida: $1 (veja --help)" ;;
  esac
  shift
done

[ -x "$CLI" ] || die "arduino-cli nao encontrado (veja o cabecalho deste script)"
"$CLI" core list 2>/dev/null | grep -q "^esp32:esp32" || die "core esp32 nao instalado: $CLI core install esp32:esp32"

# ---------------------------------------------------------------- compilar
SAIDA="$(mktemp -d)"
trap 'rm -rf "$SAIDA"' EXIT
FLAGS=""
[ "$BANCADA" = 1 ] && FLAGS="-DMODO_BANCADA=1"
say "Compilando ($([ "$BANCADA" = 1 ] && echo 'TESTE DE BANCADA' || echo 'modo normal'))"
"$CLI" compile --fqbn "$FQBN" --build-property "compiler.cpp.extra_flags=$FLAGS" --output-dir "$SAIDA" "$SKETCH" \
  | grep -E "Sketch uses|Global variables" || die "falhou ao compilar"
[ "$SO_COMPILAR" = 1 ] && { say "Compilou. Nada foi gravado (--so-compilar)."; exit 0; }

# ---------------------------------------------------------------- gravar pelo Pi
if [ -n "$VIA_PI" ]; then
  DEST="$PI_USER@$VIA_PI"
  say "Gravando pelo Pi ($DEST)"
  ssh -o ConnectTimeout=6 "$DEST" true || die "nao conectou em $DEST (mesma rede? IP certo? no Pi: hostname -I)"
  # imagem completa (bootloader + particoes + app) a partir de 0x0; o fim e enchimento 0xFF (= flash apagada):
  # cortar reduz de 4 MB para ~300 KB pelo Wi-Fi do Pi
  python3 -c "import sys; d=open(sys.argv[1],'rb').read().rstrip(b'\xff'); d+=b'\xff'*(-len(d)%4); open(sys.argv[2],'wb').write(d)" \
    "$SAIDA/esp32_locomocao.ino.merged.bin" "$SAIDA/firmware.bin"
  scp -q "$SAIDA/firmware.bin" "$DEST:/tmp/esp32_locomocao.bin"
  # o script remoto vai como ARQUIVO (nao pela entrada padrao): o monitor do teste de bancada precisa do teclado
  cat > "$SAIDA/gravar_no_pi.sh" <<'REMOTO'
set -u
PY="$HOME/yolo/.venv/bin/python"
if ! "$PY" -c "import esptool" 2>/dev/null; then
  echo "   instalando esptool no ~/yolo/.venv"
  "$HOME/.local/bin/uv" pip install -q --python "$PY" esptool || { echo "[erro] nao instalou o esptool"; exit 1; }
fi
PORTA=$(ls /dev/serial/by-id/* 2>/dev/null | head -1)
[ -n "$PORTA" ] || PORTA=$(ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | head -1)
[ -n "$PORTA" ] || { echo "[erro] nenhum ESP32 no USB do Pi (ls /dev/ttyUSB* /dev/ttyACM*)"; exit 1; }
echo "   porta: $PORTA"
# quem estiver usando a serial (agente MQTT, seguir_tag) precisa soltar a porta para gravar
if tmux has-session -t mqtt 2>/dev/null; then echo "   parando o agente MQTT (tmux mqtt) para liberar a serial"; tmux kill-session -t mqtt; fi
pkill -f '[s]eguir_tag.py' && echo "   parei o seguir_tag.py" || true
sleep 0.5
"$PY" -m esptool --chip esp32 --port "$PORTA" --baud 460800 write_flash 0x0 /tmp/esp32_locomocao.bin || exit 1
echo "   gravado."
if [ "$BANCADA" = 1 ]; then
  echo "   monitor do teste de bancada (115200). Sair: Ctrl+]"
  sleep 1
  "$PY" -m serial.tools.miniterm "$PORTA" 115200
else
  echo "   pronto. Para seguir a tag: cd ~/yolo/robotica/controle && python3 seguir_tag.py --motores"
  echo "   (ou o agente MQTT: MOTORES=1 ground_station/iniciar_gs.sh no Mac)"
fi
REMOTO
  scp -q "$SAIDA/gravar_no_pi.sh" "$DEST:/tmp/gravar_no_pi.sh"
  ssh -t "$DEST" "BANCADA=$BANCADA bash /tmp/gravar_no_pi.sh"
  exit $?
fi

# ---------------------------------------------------------------- gravar no USB deste computador
if [ -z "$PORTA" ]; then
  PORTA=$("$CLI" board list --format json | python3 -c '
import json, sys
d = json.load(sys.stdin)
itens = d.get("detected_ports", d) if isinstance(d, dict) else d
ps = [i["port"]["address"] for i in itens if i.get("port", {}).get("protocol") == "serial"]
ps = [p for p in ps if any(k in p for k in ("usbserial", "SLAB", "wchusbserial", "usbmodem", "ttyUSB", "ttyACM"))]
print("\n".join(ps))')
  N=$(printf '%s\n' "$PORTA" | grep -c . || true)
  [ "$N" = 0 ] && die "nenhum ESP32 no USB. Conecte o cabo (de dados, nao so de carga) ou use --porta. Portas: $("$CLI" board list | tail -n +2 | awk '{print $1}' | tr '\n' ' ')"
  [ "$N" -gt 1 ] && die "mais de uma porta serial: $(echo $PORTA). Escolha com --porta"
fi
say "Gravando em $PORTA"
"$CLI" upload --fqbn "$FQBN" -p "$PORTA" --input-dir "$SAIDA" "$SKETCH" || die "falhou ao gravar (segure o botao BOOT do ESP32 ao comecar, se precisar)"
say "Gravado."
if [ "$BANCADA" = 1 ]; then
  say "Monitor do teste de bancada (115200). Sair: Ctrl+C"
  sleep 1
  exec "$CLI" monitor -p "$PORTA" --config baudrate=115200
fi
echo "Agora ligue o ESP32 no USB do Pi e rode no Pi: python3 ~/yolo/robotica/controle/seguir_tag.py --motores"
