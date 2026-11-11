# Ground station (vídeo) — como a equipe cumpre o contrato

Contrato da organização: [`contrato_interface.md`](contrato_interface.md). Esta página cobre o **vídeo** (§4) e a tela da equipe. A telemetria MQTT (§3) ainda não foi feita.

## Caminho do vídeo

```
 Pi 3 (camera_server.py --h264)                         Mac = ground station
 webcam ─► OpenCV ─► AprilTag (no Pi)
              └─► ffmpeg h264_v4l2m2m ──MPEG-TS/UDP──► :5000  gs_video.py ──WHIP──► SERVIDOR :8889/rover/<id>/whip
                                                                  │  (sem recodificar; confere o §4.1)
                                                                  ├─► udp://127.0.0.1:5001 ─► detector_pc.py (YOLO) ─► :8091/stream (anotado)
                                                                  └─► udp://127.0.0.1:5002 ─► ver_video.py ─► :8092/stream (POV cru, reserva)

 SERVIDOR :8889/arena/whep ──WebRTC/WHEP──► dashboard (navegador) — painel "Vista superior"
 dashboard :8000  =  V.C. (:8091 ou :8092)  +  V.S. (câmera da arena)  +  modo, itens, tempo, bateria
```

| Parte | Onde | Quem mantém |
|---|---|---|
| Captura, AprilTag e H.264 no Pi | `visao/pi/camera_server.py --h264 udp://IP_DO_MAC:5000` | visão |
| Relay UDP → WHIP + cópias locais | `ground_station/gs_video.py` | ground station |
| POV cru em MJPEG | `ground_station/ver_video.py` | ground station |
| YOLO no Mac | `visao/pc/detector_pc.py --udp udp://127.0.0.1:5001 --saida-mjpeg 8091` | visão |
| Tela da equipe | `dashboard/` (WHEP da arena + MJPEG da câmera) | ground station |

## Dia da prova

1. Conectar o Mac e o Pi no Wi‑Fi da arena. Anotar o IP do Pi.
2. Criar `ground_station/config_local.json` (fora do git), a partir do `config_exemplo.json`:
   ```json
   {"servidor": "IP_DO_SERVIDOR", "id": "roverNN", "senha": "SENHA_DA_EQUIPE"}
   ```
3. Ligar tudo: `PI_IP=<ip do pi> ground_station/iniciar_gs.sh`
   - sobe o dashboard, o relay, o POV cru e o detector, e reinicia o Pi mandando H.264 para o IP atual do Mac
   - o relay avisa o dashboard da URL da câmera da arena (`http://IP_DO_SERVIDOR:8889/arena/whep`)
4. Abrir `http://localhost:8000` e apertar F (tela cheia).
5. Conferir no navegador: `http://IP_DO_SERVIDOR:8080/arena.html?path=rover/<id>` mostra o nosso vídeo (homologação 3).
6. Parar: `ground_station/parar_gs.sh`. Logs em `ground_station/logs/`.

Sem o servidor (treino no lab): `ground_station/iniciar_gs.sh --sem-servidor`. Sem o robô: `SEM_PI=1 ground_station/iniciar_gs.sh --sem-servidor` e, em outro terminal, `ground_station/.venv/bin/python ground_station/robo_simulado.py --segundos 0`.

Instalação (uma vez): `cd ground_station && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt`. Não precisa de ffmpeg no Mac: o PyAV já traz as bibliotecas.

## O que o relay garante

- **Não recodifica**: os pacotes H.264 do Pi vão direto para o RTP (aiortc), como pede o §4.3. O WHIP negocia só H.264.
- **Confere o §4.1 a cada 5 s** e imprime `AVISO (contrato 4.1)` se o vídeo sair do padrão: codec, perfil, B‑frames, 640×480, fps, kbps, intervalo de keyframe, SPS/PPS em todo keyframe e 1 fatia por quadro.
- **Autenticação**: `Authorization: Bearer <id>:<senha>` (padrão do contrato); `--auth basic` se o servidor pedir HTTP Basic.
- **Reconexão**: se o servidor recusar ou a conexão cair, tenta de novo (1, 2, 4… até 10 s). Ao sair com Ctrl+C ou `parar_gs.sh`, encerra a sessão WHIP (DELETE).
- **Robô que reinicia**: se o vídeo parar ou o tempo do Pi voltar para trás, o relay emenda a linha do tempo (o RTP e as cópias locais nunca recebem tempo voltando). As cópias locais sempre começam num keyframe.
- **Rede lenta até o servidor**: se acumular, descarta e recomeça no próximo keyframe (no máximo 1 s, porque o GOP é 1 s).

## Testes feitos (11/11/2026)

| Teste | Resultado |
|---|---|
| Pi real (`--h264`, webcam, 10 fps, 1000 kbps) → relay | Constrained Baseline, 640×480, 10 fps, 940–1060 kbps, keyframe a cada 1,00 s, SPS/PPS em todo keyframe e 1 fatia: nenhum aviso |
| Vídeo fora do padrão (`robo_simulado.py --fora-do-padrao`) | avisos de perfil High, B‑frames, keyframe a cada 2,93 s e várias fatias |
| WHIP com senha errada | 401, relay tenta de novo com espera crescente |
| WHIP com senha certa (servidor de teste aiortc) | negociou H.264 `42001f`; o servidor decodificou 640×480 a ~10 fps; DELETE ao sair |
| Encoder reiniciado (tempo voltando 5,9 s) e vídeo parado 5 s | relay emendou o tempo e retomou sozinho, sem erro no muxer |
| Dashboard | câmera do robô anotada (:8091) e câmera da arena por WHEP, ao vivo |

**Ainda não testado contra o servidor real.** O servidor de teste foi escrito com aiortc. O da competição é provavelmente o MediaMTX (as portas 8889/8189 são as dele). Antes da prova, vale testar com o MediaMTX local ou com o `sim_rover.py` do pacote das equipes.

## Pendências

- **Telemetria MQTT (§3)**: `status` com Last Will, `state`, `mode`, `battery`, `detection`, comandos `cmd/*` e homem‑morto de 0,5 s. Ainda não feito.
- **Classes do YOLO × contrato**: o modelo atual usa `ampulheta`, `anel_base`, `anel_haste`, `cilindro`, `icosaedro` e `peca_chata`; o contrato usa `hiperboloide`, `argola_pequena`, `argola_grande`, `cilindro` e `exotico`. Falta definir o mapeamento (ou retreinar) antes de publicar `detection`.
- **Perda no Wi‑Fi do Pi 3**: às vezes aparecem riscos até o próximo keyframe. Com o MJPEG do `:8080` desligado sobra banda; vale testar no Wi‑Fi da arena.
- **Pacote de referência** (`referencia/`: `gs_relay.py`, `robo_video.py`, `sim_rover.py`): não está no repo. Se a organização mandar, comparar com o nosso relay e rodar o `sim_rover.py`.
