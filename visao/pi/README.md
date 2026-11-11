# visao/pi — o que roda no Raspberry Pi

O Pi captura a webcam, calcula a **AprilTag aqui mesmo**, para o robô poder pilotar sozinho sem o Mac, e manda o vídeo para a ground station. O YOLO **não** roda no Pi: ele fica no Mac (`visao/pc/`).

## Arquivos

| Arquivo | Papel |
|---|---|
| `camera_server.py` | **Processo principal do Pi.** Captura (OpenCV, uma thread), AprilTag (outra thread, sempre no quadro mais recente), H.264 por ffmpeg (`--h264`) e servidor HTTP (MJPEG, `/tags`, `/status`, `/calibrar`, `/zerar`). |
| `apriltag_pi.py` | Detecção da tag (`cv2.aruco`) e pose (`solvePnP`). É a **única** implementação: o `camera_server.py` e o Mac (`visao/pc/visao_lib.py`) importam daqui. Também roda sozinho: `python apriltag_pi.py` imprime a distância no terminal. |
| `iniciar_servidor.sh` | Sobe o `camera_server.py` no tmux `cam`, libera a câmera e a porta e espera o primeiro quadro. `bash iniciar_servidor.sh PORTA [args do camera_server]`. |
| `camera_lab.json` | Intrínsecos padrão da webcam do lab: fx = 826 px (~42°), centro óptico fixo, sem distorção. Se existir um `camera_pi.json` (gerado por `visao/pc/calibrar_camera.py`), ele é usado no lugar. |
| `teste_visao.py`, `setup_pi.sh`, `NOTAS_PI.md` | Teste antigo (28/10) com o YOLO rodando no próprio Pi (4,5 fps), checagem do Pi e o histórico de decisões. |
| `models/` | Pesos do YOLO (fora do git; vêm do `visao_pi.tar.gz`). O Mac usa `models/nano_LR.pt`. |

## Uso

```bash
# no Mac, o jeito normal (envia os arquivos e sobe tudo):
visao/pc/iniciar.sh                       # modo laboratório (MJPEG + janela)
PI_IP=192.168.1.70 ground_station/iniciar_gs.sh    # cadeia da competição (H.264)

# direto no Pi:
cd ~/yolo/robotica && source ../.venv/bin/activate
python apriltag_pi.py                                   # só a tag, imprime distância e ângulo
python camera_server.py --tag-mm 42                     # servidor (Ctrl+C encerra)
python camera_server.py --h264 udp://IP_DO_MAC:5000     # + H.264 para a ground station
```

Opções principais do `camera_server.py`: `--tag-mm` (42), `--tag-familias` (25h9), `--tag-ids`, `--h264 URL`, `--h264-fps` (10, igual ao GOP), `--h264-kbps` (900), `--quality` (JPEG 65), `--sndbuf-kb` (32), `--sem-mjpeg`, `--sem-tags`, `--source video.mp4` (testar sem câmera; roda também no Mac). Lista completa: `python camera_server.py -h`.

## Formato do `/tags`

```json
{"seq": 812, "frame_seq": 815, "ts_frame": 1794418400.64, "proc_ms": 18.1, "fps": 10.9,
 "tag_mm": 42.0, "escala": 1.0, "erro": "",
 "tags": [{"id": 0, "familia": "25h9", "dist_cm": 58.2, "angulo_deg": 3.1,
           "x_cm": 3.2, "y_cm": 5.0, "z_cm": 58.0, "yaw_tag_deg": 160.4, "lado_px": 59.1,
           "corners": [[x, y], ...], "center": [x, y], "rvec": [...], "tvec": [...]}]}
```

- `dist_cm`: distância do centro óptico da câmera ao centro da tag. `angulo_deg` positivo = tag à direita.
- `x_cm` (lateral, + direita), `y_cm` (+ para baixo) e `z_cm` (para a frente) estão no referencial da câmera.
- `GET /tags?depois=N` espera até sair um resultado mais novo que `N` (long-poll, até 1 s). Assim o cliente recebe cada quadro processado uma única vez.
- `GET /calibrar?ref=50`, com UMA tag a 50 cm, ajusta a escala da distância e salva em `ajuste_distancia.json` (no Pi). `GET /zerar` desfaz o ajuste.

## H.264 (contrato, §4.1)

`ffmpeg -c:v h264_v4l2m2m -profile:v 578 -bf 0 -g FPS -bsf:v dump_extra -f mpegts`, alimentado com YUV420 a taxa **fixa** (o último quadro é repetido se a câmera atrasar), para o GOP ficar em exatamente 1 s. Medido no Pi real: Constrained Baseline, 640x480, 10 fps, keyframe a cada 1,00 s, SPS/PPS em todo keyframe e 1 fatia por quadro, cerca de 900–1000 kbps. Erros do ffmpeg aparecem no `camera_server.log` com o prefixo `[h264] ffmpeg:`.
