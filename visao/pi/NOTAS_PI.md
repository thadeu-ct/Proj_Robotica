# Teste do detector no Raspberry Pi (28/10/2026)

## Modelo
- Arquivo: nano_LR.pt, YOLO26n (Ultralytics 8.4.55), 2,5 M parâmetros, 5,9 GFLOPs
- Treinado com imgsz=512, 300 épocas (o relatório cita 320; atualizar)
- Classes: 0 ampulheta (4 pts), 1 anel_base (3), 2 anel_haste (5), 3 cilindro (6), 4 icosaedro (4 cada), 5 peca_chata (8)
- Exportações: NCNN 320, NCNN 416, ONNX 320 (pacote visao_pi.tar.gz)

## Hardware de teste
- Raspberry Pi 3 Model B Rev 1.2, aarch64, 905 MB RAM, Python 3.10, usuário puc@puc1, IP 192.168.1.70 (rede do lab)
- venv em ~/yolo/.venv com ultralytics 8.4.191, ncnn, pupil_apriltags, opencv 5.0, onnxruntime
- Webcam USB genérica "USB2.0 PC CAMERA" (1908:2310) em /dev/video0, 640x480
- 39,7 °C, throttled=0x0 (fonte ok também sob carga)

## Execução (SSH, sem interface, dentro do tmux)
```
ssh puc@192.168.1.70
sudo iw wlan0 set power_save off
tmux new -s visao            # reentrar: tmux attach -t visao
export LC_ALL=C.UTF-8 LANG=C.UTF-8
cd ~/yolo/robotica && source ../.venv/bin/activate
python teste_visao.py --quiet --frames 150
python teste_visao.py --model models/nano_LR_416_ncnn_model --quiet --frames 150
python teste_visao.py --apriltag --stream               # http://192.168.1.70:8080
```
Saídas em out/: CSV por quadro (latência, inferência, detecções) e imagens anotadas a cada 30 quadros.

## Resultados (Pi 3, 150 quadros)
| Modelo | Inferência média | p95 | FPS de rede | Latência média / p95 |
|---|---|---|---|---|
| NCNN 320 | 220 ms | 247 ms | 4,54 | 252 / 290 ms |
| NCNN 416 | ~362 ms | | ~2,75 | |

- Metas do relatório: 5 fps e latência < 250 ms. O NCNN 320 fica logo abaixo das duas no Pi 3; o 416 está bem fora.
- Decisão provisória: NCNN 320 no Pi 3.
- O "fps médio" do resumo (3,18) inclui o carregamento do modelo; o número que vale é o de rede.
- Detecções corretas com conf 0,9+ (ampulheta, anel_haste), mas piscando entre quadros
- Bug: NCNN devolveu caixa com NaN e derrubou o script. Corrigido (caixas não finitas descartadas); 0 NaN em 150 quadros depois da correção
- Confirmação de alvo tolera até 2 quadros sem detecção

### Pendentes
- Avaliar visualmente (--stream) falsos positivos e alcance de detecção
- Teste com --apriltag (custo extra no fps)
- Se o Pi 3 for o hardware final: exportar NCNN 256/288 e/ou ajustar threads para alcançar 5 fps

## Problemas e soluções
- Wi-Fi do Pi derrubou o SSH: brcmfmac com power save ligado. `sudo iw wlan0 set power_save off`; permanente com `nmcli connection modify <rede> 802-11-wireless.powersave 2`
- Rodar dentro de tmux para o teste sobreviver a quedas do SSH
- Aviso de locale vem do Terminal do Mac (LC_CTYPE=UTF-8); resolver com export acima
- Patch aplicado duas vezes no Pi gera linha "caixas NaN" duplicada no resumo (só cosmético)

## Mudança (10/11/2026): o Pi só transmite, o computador processa

- O Pi 3 é o hardware final, mas os 4,5 fps do NCNN 320 não batem a meta. Agora o Pi roda só o `camera_server.py`, que captura a webcam e manda MJPEG na porta 8080. YOLO e AprilTag rodam no computador (`visao/pc/`).
- Para ligar: `cd visao/pc && ./iniciar.sh`. O passo a passo e a solução de problemas estão em `visao/pc/README.md`.
- AprilTag do lab: **tag25h9, ID 5**, confirmada nas fotos de 23/09. O relatório fala em tag36h11; o detector aceita as duas. O código antigo usava `tag_size=0.004` (4 mm), o que está errado: o lado da tag tem cerca de 100 mm (medir).
- O `pupil-apriltags` não detectou a tag25h9 nas fotos reais; o `cv2.aruco` (DICT_APRILTAG_25h9) detectou. A pose vem de `solvePnP` IPPE_SQUARE.
- Câmera: fx ≈ 826 px (cerca de 42° de campo horizontal), calculado com as 6 fotos do tabuleiro 8x5. A calibração antiga (fx 840, cx 262, k3 = 13) estava sobreajustada. Recalibrar com `calibrar_camera.py --auto`.
- Dataset em `~/Downloads/fotos_robotica_yolo` (355 fotos). O `data.yaml` de lá lista as classes em outra ordem, mas o `nano_LR.pt` acerta 109 de 109 rótulos com os nomes que traz dentro dele, então não há troca de nomes.


## 11/11/2026: Pi real, AprilTag embarcada e H.264

- A AprilTag passou a rodar **no Pi** (`apriltag_pi.py`, thread no `camera_server.py`, publicada em `/tags`), porque o robô precisa pilotar sozinho. O Mac só desenha. Pi 3: 14–25 ms por quadro, cerca de 11 fps (limitado pela câmera).
- **A tag do lab tem 42 mm** (medida com régua). Com o padrão antigo de 100 mm, o Pi marcava 150 cm para uma tag a 64 cm. Com `--tag-mm 42`, a distância bateu com a trena sem ajuste de escala, o que confirma a calibração `camera_lab.json` (fx 826).
- O vídeo MJPEG acumulava 3 s de atraso: o Wi-Fi do Pi 3 dá cerca de 300 KB/s, e cada quadro tinha 33 KB a 11 fps. A solução foi JPEG 65 e um buffer de envio de 32 KB (o servidor descarta quadros velhos). Resultado: 10–11 fps com cerca de 100 ms de atraso.
- H.264 para a ground station (contrato §4.1): `h264_v4l2m2m` com `-profile:v 578` (o encoder não aceita o nome), 10 fps fixos, GOP 10, `dump_extra`, 900 kbps. O relay conferiu pacote a pacote: Constrained Baseline, SPS/PPS em todo keyframe, 1 fatia por quadro.
- Bugs corrigidos no Pi real: `pkill -f camera_server.py` matava a própria sessão ssh (use `[c]amera_server.py`); um método chamado `_stderr` numa subclasse de `Thread` colidia com o atributo interno do Python, e o ffmpeg ficava sem leitor do stderr e caiu uma vez.
- Webcam: só YUYV (sem MJPEG nativo). Pi: Debian 13 trixie, ffmpeg 7.1.3, OpenCV 5.0 no `~/yolo/.venv` (Python 3.10).
