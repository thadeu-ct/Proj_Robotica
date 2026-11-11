# visao/pc — o que roda no Mac

O Mac recebe o vídeo do Pi, roda o **YOLO** (`../pi/models/nano_LR.pt`, no chip da Apple via MPS) e desenha a **AprilTag calculada no Pi**: a distância e o ângulo vêm prontos do `/tags`. O resultado vai para uma janela e/ou para `:8091` (vídeo anotado e JSON), que é o que o dashboard usa.

## Arquivos

| Arquivo | Papel |
|---|---|
| `detector_pc.py` | Programa principal: fonte de vídeo → YOLO → tags do Pi → desenho → janela / `:8091`. |
| `visao_lib.py` | Leitores de vídeo (`StreamReader` para MJPEG, `UdpReader` para H.264/UDP, `LocalReader` para webcam ou arquivo), `PiTags` (lê o `/tags` do Pi) e `Camera`. A detecção da tag é importada de `../pi/apriltag_pi.py`. |
| `classes_contrato.json` | **Tradução das classes do modelo para os artefatos do contrato.** Edite aqui, sem mexer em código. |
| `calibrar_camera.py` | Calibra a câmera do Pi com o tabuleiro 8x5 do lab. Gera `../pi/camera_pi.json`. |
| `iniciar.sh` / `parar.sh` | Modo laboratório: envia os arquivos ao Pi, sobe o servidor (MJPEG + tags) e abre a janela. / Para o servidor no Pi. |
| `requirements.txt` | Dependências (`ultralytics`, `opencv-python`, `numpy`). O `iniciar.sh` cria o `.venv` sozinho com `uv`. |

## Dois jeitos de rodar

**1. Cadeia da competição (H.264):** o `ground_station/iniciar_gs.sh` liga tudo, inclusive este detector, com:
```bash
.venv/bin/python detector_pc.py --pi 192.168.1.70 --udp udp://127.0.0.1:5001 --saida-mjpeg 8091 --sem-janela
```
O vídeo vem da cópia local do relay (`:5001`) e as tags vêm de `http://PI:8080/tags`. Ver `../../docs/ground_station.md`.

**2. Laboratório (MJPEG, sem ground station):**
```bash
./iniciar.sh                 # Pi em 192.168.1.70; pede a senha uma vez (ou nenhuma, com chave ssh)
./iniciar.sh 192.168.1.80    # outro IP
TAG_MM=42 ./iniciar.sh       # lado da tag (padrão 42 mm, o tamanho do lab)
./iniciar.sh local           # webcam do Mac, sem Pi (tag calculada aqui)
./parar.sh                   # para o servidor no Pi
```
Se a rede bloquear a porta 8080, o `iniciar.sh` passa o vídeo por um túnel SSH sozinho.

## Saída `:8091` (com `--saida-mjpeg 8091`)

- `/stream`: vídeo anotado (MJPEG). `/frame.jpg`: último quadro.
- `/deteccoes`: o que está na imagem agora:
  ```json
  {"ts": 1794420516.0,
   "objetos": [{"artifact": "hiperboloide", "classe_modelo": "ampulheta", "conf": 0.96, "box": [x1, y1, x2, y2]}],
   "tags": [{"id": 0, "dist_cm": 58.2, "angulo_deg": 3.1}]}
  ```
  `artifact` usa o nome do contrato; `null` quer dizer que a classe do modelo não é um artefato (hoje, o `icosaedro`).
- Por padrão escuta só em 127.0.0.1. Use `--saida-bind 0.0.0.0` para outras máquinas.

## Classes do modelo → artefatos do contrato

Arquivo `classes_contrato.json`, lido quando o detector inicia:

| Modelo | Contrato |
|---|---|
| ampulheta | hiperboloide |
| anel_haste | argola_pequena |
| anel_base | argola_grande |
| cilindro | cilindro |
| peca_chata | exotico |
| icosaedro | — (aparece na tela entre parênteses, mas não é publicado) |

A associação foi feita pelo formato das peças nas fotos do dataset. Se a organização mudar os nomes, atualize `artefatos_validos`; o detector avisa no terminal quando o mapa aponta para um nome fora da lista.

## Teclas da janela

| Tecla | Ação |
|---|---|
| q / Esc | sair |
| s | salvar a imagem (anotada e crua) em `capturas/` |
| c | ajustar a distância: UMA tag a `--dist-ref` cm (padrão 50). O ajuste fica salvo **no Pi** |
| r | zerar o ajuste |
| y / a | liga ou desliga o YOLO / o desenho da tag |
| + / - | confiança mínima do YOLO (padrão 0,50) |

## Tag e calibração

- Tag do lab: **tag25h9, 42 mm** de borda preta. Para imprimir: `../tags/tags25h9_id0-7_42mm.pdf`, em 100%.
- Com `--tag-mm 42` e a calibração padrão (`../pi/camera_lab.json`), a distância bateu com a trena (≈60 cm) **sem** nenhum ajuste de escala.
- Para calibrar a câmera de verdade: `.venv/bin/python calibrar_camera.py --pi 192.168.1.70 --auto`. Mova o tabuleiro 8x5 por toda a imagem; com 20 capturas ele salva `../pi/camera_pi.json`. Depois rode `./iniciar.sh` para enviar o arquivo ao Pi.

## Opções úteis do `detector_pc.py`

`--pi IP` · `--udp URL` · `--url URL_MJPEG` · `--cam 0` · `--arquivo foto_ou_video` · `--tags-local` (calcula a tag no Mac) · `--conf 0.5` · `--imgsz 512` (tamanho do treino) · `--classes arquivo.json` · `--saida-mjpeg PORTA` · `--sem-janela` · `--quadros N` · `--salvar-cada N`. Lista completa: `-h`.

## Problemas comuns

| Sintoma | Causa / o que fazer |
|---|---|
| Distância 2–3 vezes maior | `--tag-mm` errado. Meça a borda preta da tag (a do lab tem 42 mm). |
| Vídeo com segundos de atraso | Wi-Fi do Pi 3 saturado (~300 KB/s). Não deixe MJPEG e H.264 abertos ao mesmo tempo; o `/status` do Pi mostra `clients`. |
| Riscos na imagem por até 1 s | Perda de pacotes UDP no Wi-Fi; o vídeo se corrige no próximo keyframe. |
| "Aguardando imagem..." | No Pi: `tmux attach -t cam` (sair sem parar: Ctrl+B, depois D). |
| SSH cai | No Pi: `sudo iw wlan0 set power_save off`. |

## Validação

- **Ontem (10/11), sem o Pi:** distância em tags sintéticas com erro < 1% de 18 a 180 cm; YOLO acertou 109 de 109 objetos rotulados do dataset; fluxo completo contra um "Pi" simulado.
- **Hoje (11/11), com o Pi real:** tag a ~11 fps no Pi; distância conferida com a trena; H.264 conforme o §4.1; detector via UDP a ~10 fps, com YOLO em 10 ms.
