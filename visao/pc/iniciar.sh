#!/usr/bin/env bash
# Liga tudo com um comando: envia o servidor de camera para o Pi, inicia ele la (no tmux)
# e abre o detector (YOLO + AprilTag) aqui no computador.
#
#   ./iniciar.sh                      # Pi em 192.168.1.70, usuario puc
#   ./iniciar.sh 192.168.1.80         # outro IP
#   PI_USER=pi ./iniciar.sh           # outro usuario
#   TAG_MM=80 ./iniciar.sh            # lado da tag em mm (a AprilTag roda NO Pi)
#   ./iniciar.sh 192.168.1.70 --conf 0.6      # o que vier depois do IP vai para o detector_pc.py
#   ./iniciar.sh local                # teste sem Pi: webcam deste computador
#
# Pede a senha do Pi no maximo uma vez (a conexao SSH fica aberta e e reaproveitada).
set -euo pipefail

PI_IP="${1:-192.168.1.70}"
[ $# -gt 0 ] && shift
PI_USER="${PI_USER:-puc}"
PORTA="${PORTA:-8080}"
PORTA_TUNEL="${PORTA_TUNEL:-18080}"
DIR_PI="${DIR_PI:-yolo/robotica}"      # relativo a home do usuario no Pi
TAG_MM="${TAG_MM:-100}"
TAG_FAMILIAS="${TAG_FAMILIAS:-25h9}"

PC="$(cd "$(dirname "$0")" && pwd)"
VISAO="$(cd "$PC/.." && pwd)"
PY="$PC/.venv/bin/python"

say()  { printf '\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[aviso] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[erro] %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- ambiente local
if [ ! -x "$PY" ]; then
  say "Criando o ambiente Python (so na primeira vez, ~2 min)"
  command -v uv >/dev/null || die "uv nao encontrado. Instale: curl -LsSf https://astral.sh/uv/install.sh | sh"
  uv venv -q --python 3.12 "$PC/.venv"
  uv pip install -q --python "$PY" -r "$PC/requirements.txt"
fi
[ -f "$VISAO/pi/models/nano_LR.pt" ] || die "modelo nao encontrado em visao/pi/models/nano_LR.pt (fica fora do git; extraia o visao_pi.tar.gz)"

if [ "$PI_IP" = "local" ]; then
  say "Modo local: webcam deste computador"
  exec "$PY" "$PC/detector_pc.py" --cam 0 "$@"
fi

# ---------------------------------------------------------------- SSH com conexao reaproveitada
CTL="$HOME/.ssh/cm-robotica-%r@%h-%p"
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=$CTL" -o ControlPersist=2h
          -o ConnectTimeout=8 -o ServerAliveInterval=5 -o ServerAliveCountMax=3
          -o StrictHostKeyChecking=accept-new)
DEST="$PI_USER@$PI_IP"

say "Conectando em $DEST (se pedir, digite a senha do Pi)"
if ! ssh "${SSH_OPTS[@]}" "$DEST" true; then
  ping -c1 -W1000 "$PI_IP" >/dev/null 2>&1 \
    && die "o Pi responde ao ping mas o SSH falhou (usuario/senha?). Teste: ssh $DEST" \
    || die "o Pi nao responde em $PI_IP. Mesmo Wi-Fi? IP mudou? No Pi: hostname -I"
fi

ARQS=("$VISAO/pi/camera_server.py" "$VISAO/pi/apriltag_pi.py" "$VISAO/pi/iniciar_servidor.sh" "$VISAO/pi/camera_lab.json")
[ -f "$VISAO/pi/camera_pi.json" ] && ARQS+=("$VISAO/pi/camera_pi.json")
say "Enviando ${#ARQS[@]} arquivos para ~/$DIR_PI"
ssh "${SSH_OPTS[@]}" "$DEST" "mkdir -p $DIR_PI" </dev/null
scp -q "${SSH_OPTS[@]}" "${ARQS[@]}" "$DEST:$DIR_PI/"

say "Iniciando o servidor de camera no Pi"
ssh "${SSH_OPTS[@]}" "$DEST" "bash $DIR_PI/iniciar_servidor.sh $PORTA --tag-mm $TAG_MM --tag-familias $TAG_FAMILIAS" </dev/null \
  || die "o servidor de camera nao subiu no Pi (mensagem acima). Para ver de perto: ssh $DEST  e  tmux attach -t cam"

# ---------------------------------------------------------------- caminho do video: direto ou por tunel
URL="http://$PI_IP:$PORTA/stream"
ST="$(curl -s -m 3 "http://$PI_IP:$PORTA/status" || true)"
if [[ "$ST" == *'"ok": true'* ]]; then
  say "Video direto pela rede: $URL"
else
  warn "a rede nao deixa o computador acessar a porta $PORTA do Pi; usando tunel pelo SSH"
  ssh "${SSH_OPTS[@]}" -O forward -L "$PORTA_TUNEL:127.0.0.1:$PORTA" "$DEST" 2>/dev/null \
    || ssh "${SSH_OPTS[@]}" -f -N -L "$PORTA_TUNEL:127.0.0.1:$PORTA" "$DEST"
  URL="http://127.0.0.1:$PORTA_TUNEL/stream"
  ST="$(curl -s -m 3 "http://127.0.0.1:$PORTA_TUNEL/status" || true)"
  [[ "$ST" == *'"ok": true'* ]] || die "nem o tunel funcionou. Veja no Pi: tmux attach -t cam"
  say "Video pelo tunel: $URL"
fi

say "Abrindo o detector (q fecha a janela; o servidor continua no Pi, pare com ./parar.sh)"
exec "$PY" "$PC/detector_pc.py" --url "$URL" "$@"
