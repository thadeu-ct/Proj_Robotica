# AGENTS.md — mapa do projeto para agentes (e pessoas com pressa)

Rover de resgate da disciplina de Robótica (ENG4461). Este arquivo é o ponto de partida: o que roda onde, como ligar, onde mexer e o que já deu errado. Os detalhes estão nos README de cada pasta.

## Hardware e onde cada coisa roda

| Máquina | O que roda | Como acessar |
|---|---|---|
| **Raspberry Pi 3B** (hostname `puc1`, Debian 13 trixie) | `visao/pi/camera_server.py`: captura, **AprilTag embarcada** (distância e ângulo), H.264 para a ground station e MJPEG opcional | `ssh puc@<IP>` (no lab: 192.168.1.70). Código em `~/yolo/robotica/`; Python 3.10 + OpenCV 5.0 em `~/yolo/.venv` |
| **Mac** (ground station) | YOLO (`visao/pc/detector_pc.py`), relay de vídeo para o servidor da competição (`ground_station/`) e dashboard (`dashboard/`) | local |
| **ESP32 DevKit** | motores LEGO NXT (DRV8833) com encoders, sensores I2C e servos da garra. **Ainda não integrado ao Pi** | `eletronica/` |

A webcam (`USB2.0 PC CAMERA`, `/dev/video0`) só entrega YUYV (640x480 a 30 ou 15 fps). O Pi 3 tira cerca de 11 fps com a AprilTag rodando.

## Fluxo de dados

```
webcam ─► Pi: camera_server.py ─┬─► AprilTag (apriltag_pi.py) ─► HTTP :8080/tags  (o controle autônomo vai usar isto)
                                ├─► ffmpeg h264_v4l2m2m ─► MPEG-TS/UDP ─► Mac :5000 (ground_station/gs_video.py)
                                │                                         ├─► WHIP para o servidor da competição
                                │                                         ├─► udp://127.0.0.1:5001 ─► detector_pc.py (YOLO)
                                │                                         │        └─► :8091/stream (vídeo anotado) e :8091/deteccoes (JSON)
                                │                                         └─► udp://127.0.0.1:5002 ─► ver_video.py :8092
                                └─► MJPEG :8080/stream (só para o modo de laboratório, visao/pc/iniciar.sh)
```

A AprilTag roda **no Pi**, e não no Mac, porque o robô precisa pilotar sozinho mesmo se o Wi-Fi cair. O YOLO roda no Mac porque o Pi 3 só faz cerca de 4,5 fps com ele.

## Portas

| Porta | Processo | Conteúdo |
|---|---|---|
| Pi :8080 | `camera_server.py` | `/stream` (MJPEG), `/frame.jpg`, `/status` (JSON, inclui o estado do H.264), `/tags` (JSON; `?depois=N` faz long-poll), `/calibrar?ref=CM`, `/zerar` |
| Mac UDP :5000 | `ground_station/gs_video.py` | H.264 do Pi (contrato §4.1) |
| Mac UDP :5001 / :5002 | cópias locais do relay | entrada do detector / do `ver_video.py` |
| Mac :8091 | `detector_pc.py --saida-mjpeg 8091` | `/stream` (vídeo anotado), `/frame.jpg`, `/deteccoes` (JSON com `artifact` no nome do contrato) |
| Mac :8092 | `ground_station/ver_video.py` | vídeo cru, de reserva |
| Mac :8000 | `dashboard/servidor.py` | tela da equipe |

## Como ligar

- **Competição / cadeia completa**: `PI_IP=<ip> ground_station/iniciar_gs.sh`. Reinicia o Pi com `--h264` e sobe relay, detector e dashboard. Ver `docs/ground_station.md`.
- **Laboratório (só visão, MJPEG)**: `visao/pc/iniciar.sh [IP]`. Envia os arquivos ao Pi, sobe o servidor e abre a janela do detector. Ver `visao/pc/README.md`.
- **Só o Pi**: `ssh puc@<IP> 'bash yolo/robotica/iniciar_servidor.sh 8080 --tag-mm 42 --tag-familias 25h9 [--h264 udp://IP_DO_MAC:5000]'`. A saída aparece com `tmux attach -t cam` no Pi.
- **Parar o Pi**: `visao/pc/parar.sh [IP]`.

Os arquivos que ficam em `~/yolo/robotica` no Pi são **cópias**: edite no repo e reenvie com `iniciar.sh` ou `scp`. Nunca edite direto no Pi.

## Onde mexer

| Quero mudar... | Arquivo |
|---|---|
| nome dos artefatos (modelo → contrato) | `visao/pc/classes_contrato.json` (não precisa mexer em código) |
| tamanho e família da tag | `--tag-mm` / `--tag-familias` no `camera_server.py` (padrão 42 mm, 25h9) e `TAG_MM` no `visao/pc/iniciar.sh` |
| calibração da câmera | `visao/pi/camera_lab.json` (padrão) ou `visao/pi/camera_pi.json` (gerado pelo `visao/pc/calibrar_camera.py --auto`) |
| ajuste fino da distância | tecla `c` no detector, ou `GET :8080/calibrar?ref=CM`. Fica salvo em `visao/pi/ajuste_distancia.json`, no Pi |
| detecção e pose da tag (código único, usado pelo Pi e pelo Mac) | `visao/pi/apriltag_pi.py` |
| parâmetros do H.264 (§4.1) | `H264Sender` em `visao/pi/camera_server.py` |
| relay, WHIP e dashboard | `ground_station/`, `dashboard/` (ver `docs/ground_station.md`) |

## Convenções

- Tudo em **português** (código, comentários e commits). Nos `.py`, os comentários vão sem acento, por compatibilidade de terminal.
- Commits **sem** `Co-Authored-By`. Use `git add` arquivo por arquivo (nunca `git add .`) e `git pull --ff-only` antes do push.
- **Fora do git**: modelos (`visao/pi/models/`, vêm do `visao_pi.tar.gz`), venvs, `ajuste_distancia.json`, logs e `ground_station/config_local.json` (senha). O dataset de treino fica em `~/Downloads/fotos_robotica_yolo` no Mac do Lucas.
- Valide no Pi real antes de documentar. Para testar sem o Pi, `camera_server.py --source video.mp4` roda no Mac.

## Armadilhas já encontradas (não repetir)

- **A tag do lab é `tag25h9` com 42 mm de lado** (borda preta). O relatório fala em tag36h11 de 100 mm; está errado para o lab. Com 100 mm, a distância sai 2,4 vezes maior.
- **O `pupil_apriltags` não detecta a tag25h9 nas fotos reais.** Use `cv2.aruco` (`DICT_APRILTAG_25h9`) com `solvePnP` (IPPE_SQUARE), como em `apriltag_pi.py`.
- **`pkill -f camera_server.py` por ssh mata a própria sessão**, porque o texto aparece na linha de comando. Use `pkill -f '[c]amera_server.py'`.
- **O `h264_v4l2m2m` não aceita o perfil pelo nome**: use `-profile:v 578` (Constrained Baseline). Ele também ignora `-maxrate`.
- **Não dê o nome `_stderr` (nem `_target`, `_args`...) a métodos de subclasses de `threading.Thread`**: esses nomes são atributos internos do Python.
- **Wi-Fi do Pi 3 ≈ 300 KB/s.** H.264 (~110 KB/s) e MJPEG juntos ficam no limite; sem o buffer de envio pequeno (`--sndbuf-kb`), o vídeo acumulava 3 s de atraso.
- **O `sudo` do Pi pede senha**: o power save do Wi-Fi (`sudo iw wlan0 set power_save off`) precisa ser desligado à mão se o SSH começar a cair.

## Estado (11/11/2026) e próximos passos

- Feito e validado no Pi real: AprilTag embarcada (11 fps; distância bate com a trena), H.264 conforme o §4.1 (verificado pacote a pacote pelo relay), YOLO no Mac a cerca de 10 fps e nomes de artefato do contrato.
- **Próximo: seguir a tag com os motores.** O ESP32 deve rodar um PI de velocidade por roda a 100 Hz e o protocolo binário do relatório (§5.5: `0xAA 0x55`, tipo, sequência, tamanho, carga, CRC-16; `CMD_VEL` = 0x01; watchdog de 250 ms). O Pi lê `/tags` e manda `CMD_VEL`. Falta a eletrônica confirmar a pinagem (o relatório, `eletronica/ESP32 DevKit/pinout.md` e `eletronica/Programação/DRV8833.ino` divergem), a ligação Pi↔ESP32 (USB ou UART), o diâmetro da roda e a bitola.
- Telemetria MQTT do contrato (§3): pendente (ground station).
