# Ground station — como a equipe cumpre o contrato

Contrato da organização: [`contrato_interface.md`](contrato_interface.md). Esta página cobre o **vídeo** (§4), a **telemetria MQTT** (§3) e a tela da equipe.

## Caminho do vídeo

```
 Pi 3 (camera_server.py --h264)                         Mac = ground station
 webcam ─► OpenCV ─► AprilTag (no Pi)
              └─► ffmpeg h264_v4l2m2m ──MPEG-TS/UDP──► :5000  gs_video.py ──WHIP──► SERVIDOR :8889/rover/<id>/whip
                                                                  │  (sem recodificar; confere o §4.1)
                                                                  ├─► udp://127.0.0.1:5001 ─► detector_pc.py (YOLO) ─► :8091/stream (anotado)
                                                                  └─► udp://127.0.0.1:5002 ─► ver_video.py ─► :8092/stream (POV cru, reserva)

 SERVIDOR :8889/arena/whep ──WebRTC/WHEP──► dashboard (navegador) — painel "Vista superior"
 dashboard :8000  =  V.C. (:8091 ou :8092)  +  V.S. (câmera da arena)  +  modo, estado, itens, tempo, bateria
```

## Caminho do MQTT

```
 Pi: robo_agente.py (dono da serial do ESP32)              broker do SERVIDOR :1883            Mac: gs_mqtt.py
   publica status (LWT offline), state, mode, battery ──►    rover/<id>/#     ◄── cmd/vel, stop, mode, state, arm, gripper
   assina rover/<id>/cmd/#                           ◄──                      ◄── cmd/heartbeat a 10 Hz (sinal de vida da GS)
   CMD_VEL a 20 Hz para o ESP32                                               ◄── detection (YOLO, 1 por parada)
   ▲ UDP 127.0.0.1:8095: (v, w, estado) do controle/seguir_tag.py --agente     ──► arena/state → tempo no dashboard
```

| Parte | Onde | Quem mantém |
|---|---|---|
| Captura, AprilTag e H.264 no Pi | `visao/pi/camera_server.py --h264 udp://IP_DO_MAC:5000` | visão |
| Relay UDP → WHIP + cópias locais | `ground_station/gs_video.py` | ground station |
| POV cru em MJPEG | `ground_station/ver_video.py` | ground station |
| YOLO no Mac | `visao/pc/detector_pc.py --udp udp://127.0.0.1:5001 --saida-mjpeg 8091` | visão |
| Agente MQTT do robô (Pi) | `mqtt/robo_agente.py` (parâmetros em `mqtt/config_robo.json`) | ground station |
| Controle autônomo (Pi) | `controle/seguir_tag.py --agente` | visão |
| MQTT da GS e controle do operador | `ground_station/gs_mqtt.py` + `controle.html` (`config_gs.json`) | ground station |
| Tela da equipe | `dashboard/` (WHEP da arena + MJPEG da câmera) | ground station |

## Dia da prova

1. Conectar o Mac e o Pi no Wi‑Fi da arena. Anotar o IP do Pi.
2. Criar `ground_station/config_local.json` (fora do git), a partir do `config_exemplo.json`. Esse arquivo vale para tudo (vídeo, MQTT da GS e agente do Pi, que o recebe por `scp`):
   ```json
   {"servidor": "IP_DO_SERVIDOR", "id": "roverNN", "senha": "SENHA_DA_EQUIPE"}
   ```
3. Ligar tudo: `PI_IP=<ip do pi> MOTORES=1 ground_station/iniciar_gs.sh`
   - sobe o dashboard, o relay, o POV cru e o detector, e reinicia o Pi mandando H.264 para o IP atual do Mac
   - instala e liga o agente MQTT no Pi (tmux `mqtt`). **Sem `MOTORES=1` os motores ficam simulados.**
   - liga o `gs_mqtt.py`: controle do operador em `http://localhost:8001`
4. Abrir `http://localhost:8000` (F = tela cheia) e, para o operador, `http://localhost:8001`.
5. Homologação (contrato §6): ver a tabela abaixo.
6. Parar: `ground_station/parar_gs.sh` (o robô para sozinho pelo homem‑morto). Logs em `ground_station/logs/`; o agente do Pi aparece com `ssh puc@<IP> -t tmux attach -t mqtt`.

Sem o servidor (treino no lab): `ground_station/iniciar_gs.sh --sem-servidor` (sem MQTT). Sem o robô: `SEM_PI=1 ground_station/iniciar_gs.sh --sem-servidor` e, em outro terminal, `ground_station/.venv/bin/python ground_station/robo_simulado.py --segundos 0`.

Instalação (uma vez): `cd ground_station && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt`. Não precisa de ffmpeg no Mac: o PyAV já traz as bibliotecas. No Pi, o `~/yolo/.venv` já tem `paho-mqtt` e `pyserial`.

## Operador (`http://localhost:8001`)

| Tecla / botão | Faz |
|---|---|
| W A S D ou setas | `cmd/vel` a 10 Hz enquanto segura; zeros ao soltar. Velocidade máxima no controle deslizante |
| Espaço / PARAR | `cmd/stop`: o robô zera os motores e volta para MANUAL + IDLE |
| M / N | `cmd/mode` MANUAL / AUTONOMO (o robô confirma em `rover/<id>/mode`) |
| botões de fase | `cmd/state` (nosso tópico): o operador declara a fase (ex.: COLETA). No AUTONOMO, quem manda na fase é o controle |
| O / F | garra: abrir / fechar (`cmd/gripper`) |
| posições do braço | `cmd/arm` com os presets de `config_gs.json` (vazio até o braço ser definido) |
| detecção | automática: **1 por parada em ARTEFATO_ENCONTRADO**, com a classe estável por 1 s e conf ≥ 0,6, pegando a peça de maior caixa; a mesma classe não se repete em 30 s (sobrevive a reinício da GS). Botão manual pede confirmação (2 cliques) |

A página avisa quando o robô está em MANUAL em NAVEGACAO ou BUSCA_ARTEFATO (o juiz recebe alerta e aplica penalidade).

## Homem‑morto (contrato §3.2, homologação 8)

O agente do Pi zera os motores se ficar sem:
- `cmd/vel` por mais de **0,35 s** (MANUAL);
- `cmd/heartbeat` da GS por mais de **1 s** (AUTONOMO): GS desligada ou rede caída → para;
- o controle autônomo (UDP local) por mais de 0,35 s; `PARADO` do `seguir_tag` para na hora;
- conexão com o broker (os dois modos).

0,35 s no MANUAL, e não 0,5 s, porque o juiz mede da GS desligada até o robô parar (≤ 0,5 s), e o último `cmd/vel` chega até 100 ms antes. 1 s no AUTONOMO porque, no Wi‑Fi do Pi 3 com o vídeo no ar, o MQTT às vezes chega até ~540 ms atrasado; com 0,35 s o robô autônomo dava trancos sozinho. Os dois valores ficam em `mqtt/config_robo.json`. O agente manda CMD_VEL a 20 Hz (zeros quando parado), o que também alimenta o watchdog de 250 ms do ESP32.

## O que o relay de vídeo garante

- **Não recodifica**: os pacotes H.264 do Pi vão direto para o RTP (aiortc), como pede o §4.3. O WHIP negocia só H.264.
- **Confere o §4.1 a cada 5 s** e imprime `AVISO (contrato 4.1)` se o vídeo sair do padrão: codec, perfil, B‑frames, 640×480, fps, kbps, intervalo de keyframe, SPS/PPS em todo keyframe e 1 fatia por quadro.
- **Autenticação**: `Authorization: Bearer <id>:<senha>` (padrão do contrato); `--auth basic` se o servidor pedir HTTP Basic. O MediaMTX aceita os dois.
- **Só conecta com vídeo chegando**: o MediaMTX derruba a sessão WHIP se o vídeo não chega em ~2 s.
- **Reconexão**: se o servidor recusar ou a conexão cair, tenta de novo (1, 2, 4… até 10 s). Ao sair com Ctrl+C ou `parar_gs.sh`, encerra a sessão WHIP (DELETE).
- **Robô que reinicia**: se o vídeo parar ou o tempo do Pi voltar para trás, o relay emenda a linha do tempo. As cópias locais sempre começam num keyframe.
- **Rede lenta até o servidor**: se acumular, descarta e recomeça no próximo keyframe (no máximo 1 s, porque o GOP é 1 s).

## Testes feitos (11/11/2026)

| Teste | Resultado |
|---|---|
| Pi real (`--h264`, webcam, 10 fps) → relay | Constrained Baseline, 640×480, 10 fps, 900–1060 kbps, keyframe a cada 1,00 s, SPS/PPS em todo keyframe e 1 fatia: nenhum aviso |
| Vídeo fora do padrão (`robo_simulado.py --fora-do-padrao`) | avisos de perfil High, B‑frames, keyframe a cada 2,93 s e várias fatias |
| **MediaMTX v1.21.3 local** (o servidor provável), Pi real → WHIP | Bearer e Basic aceitos; senha errada → 401; path `rover/rover03` com 1 track H264 |
| Navegador lendo o nosso vídeo do MediaMTX (WHEP) | 640×480 a 10 fps, 0 pacotes perdidos, ~43 ms de jitter buffer |
| Câmera da arena (MediaMTX, WHEP) no dashboard | ao vivo |
| Encoder reiniciado e vídeo parado 5 s | relay emendou o tempo e retomou sozinho |
| `mqtt/testar_agente.py` (broker local com ACL `rover/rover03/#`) | 16/16: online, Last Will, mode/state ao conectar, `cmd/mode` confirmado em ~2 ms, estados do controle, PARADO em ~30 ms, battery, `cmd/stop`, `cmd/state` e homem‑morto (355 ms depois do último `cmd/vel`; ~1 s sem heartbeat no AUTONOMO) |
| Broker derrubado e religado | agente parou, reconectou sozinho e republicou `online` |
| **Pi real** (agente no tmux `mqtt`, Wi‑Fi do lab, vídeo no ar), GS morta com `kill -9` dirigindo no MANUAL | parou **367 ms** depois do último `cmd/vel` (relógio do Pi) |
| Pi real, GS morta no AUTONOMO | parou ~1 s depois do último heartbeat; com a GS viva, 30 s sem nenhuma parada falsa (heartbeat: mediana 103 ms, p95 233 ms, máximo 543 ms) |
| Pi real, 30 s dirigindo no MANUAL | nenhuma parada falsa |
| `gs_mqtt.py` com o YOLO ao vivo | heartbeat a 10 Hz; `cmd/vel` + zeros ao soltar; 1 detection por parada; sem repetição em 30 s, nem depois de reiniciar a GS; tempo de `arena/state` no dashboard |

**Servidor real do professor (18/11/2026, LabIoT, `192.168.1.154`, equipe `rover02`)**: é um MediaMTX, como esperado. Validado:
- câmera da arena por WHEP (201, sem senha) no dashboard;
- broker MQTT recusa conexão sem senha; com a senha da equipe, o agente do Pi entrou, publicou `online`, `mode` e `state`, e virou `offline` ao ser desligado;
- nosso vídeo publicado por WHIP (Bearer) e lido de volta do servidor: 640×480, ~9–10 fps;
- `arena/state` chega (traz também o campo `collected` no `manifest`, fora do contrato). Quando a rodada é de outra equipe, o dashboard mostra "vez do roverNN" em vez do tempo e dos pontos dela.

Ainda não testados no servidor real: Last Will por queda (só a saída limpa), `detection`, alerta de autonomia, `cmd/mode` e o homem‑morto com o ESP32. Para mandar só o vídeo, sem MQTT: `SEM_MQTT=1 PI_IP=<ip> ground_station/iniciar_gs.sh`.

## Pendências


- **ESP32**: o firmware existe (`firmware/esp32_locomocao/`), mas ainda não foi ligado ao Pi. O agente já segue o comportamento dele, testado com um ESP32 falso numa pty: manda o MODE na hora e de novo a cada 1 s (o ESP32 começa em ESPERA, ignora CMD_VEL em ESPERA e cai para ESPERA no watchdog de 250 ms), manda ESPERA sem o broker ou sem a GS por 1 s, não publica bateria de 0 mV e acha a serial sozinho (`/dev/serial/by-id`, `ttyUSB*`, `ttyACM*`), reabrindo se o cabo cair. `cmd/arm` e `cmd/gripper` só aparecem no log até haver quadro para eles.
- **Bateria**: vem da TELEMETRIA do ESP32. Para publicar a %, preencher `bateria_v_vazia` e `bateria_v_cheia` em `mqtt/config_robo.json` (confirmar a bateria com a eletrônica).
- **Braço**: posições prontas em `ground_station/config_gs.json` → `braco.presets`.
- **Perda no Wi‑Fi do Pi 3**: às vezes aparecem riscos até o próximo keyframe; vale testar no Wi‑Fi da arena.
- **Pacote de referência** (`referencia/`: `gs_relay.py`, `robo_video.py`, `robo_agente.py`, `sim_rover.py`): não está no repo. Se a organização mandar, rodar o `sim_rover.py` contra a nossa GS.
