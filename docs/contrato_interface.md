# Contrato de interface das equipes — Competição Rover (Mars 2020 · Perseverance)

> Cópia do documento da organização (recebido em 11/11/2026). Como a equipe implementa isso: [`ground_station.md`](ground_station.md).

Este documento define o que atravessa a fronteira entre o sistema da equipe (robô + ground station) e a infraestrutura da competição (servidor, painel, juízes). O que acontece dentro do robô e da ground station é livre. Os valores de pontuação e penalidade estão em `config/pontuacao.json`, divulgado pela organização.

```
 ROBÔ ──vídeo H.264 (protocolo livre; padrão §4.1)──► GROUND STATION ──WebRTC/WHIP──► SERVIDOR ──► painel / juízes
   │                                                     │   (operador vê só: POV do robô + câmera da arena)
   ├──────────────── telemetria MQTT (robô ou GS) ───────┴──────────────────────────► SERVIDOR
   ◄──────────── comandos MQTT rover/<id>/cmd/* (GS → broker do servidor → robô) ◄──────┘
                                                         ◄── câmera da arena (WebRTC) ──┘
```

Referências prontas (pasta `referencia/` do pacote das equipes): ground station completa (`rover-gs`, Windows), envio de vídeo do Raspberry Pi (`robo_video.py`) e agente MQTT do robô (`robo_agente.py`). Usar as referências é opcional; cumprir este contrato é obrigatório.

## 1. Rede e credenciais

| Item | Valor |
|---|---|
| Rede | Wi‑Fi dedicada da arena (SSID/senha entregues no dia) |
| Servidor | `IP_DO_SERVIDOR` (informado no dia) |
| ID da equipe | `roverNN` — vale para MQTT e vídeo |
| Senha | entregue a cada equipe (linha própria do `credenciais.csv`) |
| Portas no servidor | MQTT 1883 · MQTT/WebSocket 9001 · WebRTC HTTP 8889 · WebRTC mídia 8189 UDP · Painel 8080 |

Cada equipe só escreve em `rover/<id>/#` (o broker recusa o resto) e só publica vídeo em `rover/<id>`.

## 2. Regras da prova que afetam a interface

**Arena:** 8 artefatos num grid (hiperboloide, argola pequena, argola grande, cilindro, exótico; tipos podem se repetir — aí são numerados, ex.: "Cilindro 1", "Cilindro 2"). As AprilTags só orientam o robô: não valem pontos.

A equipe escolhe para quais artefatos navegar.

**Pontuação por artefato** — o juiz marca três caixas, cada uma com valor próprio por tipo de artefato:

| Caixa | Quando o juiz marca |
|---|---|
| Detectou | o robô identificou o artefato corretamente (YOLO) |
| Descarregou | o robô deixou o artefato na área de descarga |
| Autônomo | bônus: Detectou + Descarregou com a navegação em modo autônomo (a descarga em si pode ser manual) |

Coleta é um estado que o robô publica (`COLETA`), sem pontos próprios.

**Artefato derrubado** (no transporte ou por contato durante a navegação) é removido da rodada pelo juiz e perde os pontos já marcados.

**Autonomia obrigatória** na aproximação do artefato e na navegação até a área de descarga. Pilotar manualmente nessas fases gera penalidade de tempo (segundos descontados do tempo restante), aplicada pelo juiz. O servidor alerta o juiz quando o robô se declara `MANUAL` nos estados `NAVEGACAO` ou `BUSCA_ARTEFATO`.

**Troca de ferramenta:** manual, por um membro da equipe, no local do artefato; o relógio continua.

**Fim da rodada:** por tempo (o servidor encerra sozinho). Desempate: mais artefatos descarregados, depois mais detectados.

## 3. Telemetria (MQTT)

Broker `IP_DO_SERVIDOR:1883` (ou WebSocket `:9001`), usuário = ID da equipe. JSON UTF‑8; `ts` = epoch em ms.

### 3.1 Publicados pela equipe

| Tópico | Payload | QoS / retain | Frequência |
|---|---|---|---|
| `rover/<id>/status` | texto `online` / `offline` — `offline` como Last Will (obrigatório) | 1 / retain | ao conectar |
| `rover/<id>/state` | `{"ts":…, "state":"IDLE"\|"BUSCA"\|"NAVEGACAO"\|"BUSCA_ARTEFATO"\|"ARTEFATO_ENCONTRADO"\|"COLETA"}` | 1 / retain | a cada mudança |
| `rover/<id>/mode` | `{"ts":…, "mode":"MANUAL"\|"AUTONOMO"}` | 1 / retain | a cada mudança |
| `rover/<id>/battery` | `{"ts":…, "v":7.82, "pct":71.0, "i":1.3}` (V, %, A) | 0 / — | 1–2 Hz |
| `rover/<id>/detection` | `{"ts":…, "artifact":"cilindro", "conf":0.87}` (opcional `"tag":4`) | 1 / sem retain | 1 por detecção |
| `rover/<id>/mission` (opcional) | `{"ts":…, "tags":[{"id":4,"status":"ENCONTRADA"\|"COLETADA"}]}` | 1 / retain | a cada mudança |

Semântica dos estados (o alerta de autonomia depende deles — reporte com fidelidade):

| Estado | Significado |
|---|---|
| `IDLE` | parado, sem tarefa |
| `BUSCA` | explorando a arena (procurando tags/artefatos) |
| `NAVEGACAO` | navegando até um alvo (artefato escolhido ou área de descarga) — **autonomia obrigatória** |
| `BUSCA_ARTEFATO` | aproximação final do artefato — **autonomia obrigatória** |
| `ARTEFATO_ENCONTRADO` | parado diante do artefato (identificação pelo YOLO) |
| `COLETA` | pegando o artefato com a ferramenta |

**Detecção:** publique uma mensagem `detection` por identificação; `artifact` ∈ `hiperboloide` \| `argola_pequena` \| `argola_grande` \| `cilindro` \| `exotico`. O servidor registra e mostra ao juiz (com hora e confiança); quem marca "Detectou" é o juiz.

### 3.2 Comandos da ground station para o robô (`rover/<id>/cmd/*`)

A ground station de referência publica estes tópicos. Se a equipe usar a GS de referência, o robô deve assiná-los (`rover/<id>/cmd/#`). Uma GS própria pode usar outros tópicos, desde que fiquem dentro de `rover/<id>/`.

| Tópico | Payload | QoS | Quando |
|---|---|---|---|
| `rover/<id>/cmd/vel` | `{"ts":…, "linear":-1..1, "angular":-1..1}` (normalizado; + = frente / girar à esquerda) | 0 | 10 Hz enquanto há comando; zeros ao soltar |
| `rover/<id>/cmd/stop` | `{"ts":…}` | 1 | botão PARAR |
| `rover/<id>/cmd/mode` | `{"ts":…, "mode":"MANUAL"\|"AUTONOMO"}` | 1 | pedido de troca; o robô confirma publicando `rover/<id>/mode` |
| `rover/<id>/cmd/arm` | `{"ts":…, "joints":[graus, …], "preset":"Coleta"}` (preset opcional) | 0 / 1 | ao mover as juntas / ao escolher posição pronta |
| `rover/<id>/cmd/gripper` | `{"ts":…, "action":"open"\|"close"}` | 1 | botões da garra |

**Segurança obrigatória (homem-morto):** o robô deve parar os motores se ficar mais de 0,5 s sem `cmd/vel` ou se perder a conexão com o broker. A rede da arena pode cair.

Juntas, limites e posições prontas do braço ficam em `config\gs.json` → `manipulador` na GS de referência. Ajuste ao braço da equipe.

### 3.3 Publicado pelo servidor (somente leitura)

`arena/state` (retain):

```json
{"round": 3, "round_id": 12, "active": "rover03", "run": "IDLE|RUNNING|PAUSED|DONE",
 "elapsed_ms": 125300, "t_ref": 1792650000000, "ts": 1792650010000, "time_limit_ms": 600000,
 "points": 14, "void": false,
 "manifest": [{"tag": 1, "artifact": "cilindro", "artifact_name": "Cilindro 1",
               "detected": 1, "unloaded": 3, "bonus": null, "removed": null}, …]}
```

- Tempo de prova em andamento = `elapsed_ms + (agora_servidor − t_ref)` quando `run = RUNNING` (`agora_servidor ≈ ts` no momento da mensagem). Penalidades de tempo aparecem como salto em `elapsed_ms`.
- `manifest`: os artefatos da rodada por posição (`tag` = posição no grid), com os pontos de cada caixa marcada; `removed ≠ null` = artefato derrubado e retirado da rodada.

## 4. Vídeo

### 4.1 Padrão obrigatório do vídeo do robô

| Parâmetro | Valor |
|---|---|
| Codec | H.264 baseline (ou constrained baseline) |
| B‑frames | nenhum |
| Fatias | 1 por quadro |
| Keyframe | a cada 1 s (GOP = fps), com SPS/PPS repetidos em todo keyframe |
| Resolução / fps | 640×480, até 30 fps |
| Bitrate | até 1500 kbps |

Motivo: a ground station retransmite sem recodificar e o navegador do painel decodifica direto; fora desse padrão a imagem aparece com listras, congela ou não abre.

### 4.2 Robô → ground station (livre; referência)

Referência: `robo_video.py` (H.264 em MPEG‑TS por UDP para a porta 5000 da GS). Requer `sudo apt install ffmpeg`. Detecta sozinho o formato da webcam USB (MJPEG se houver; webcams simples só têm YUYV, use `--formato yuyv422`). No Pi 3/4, o encoder de hardware usa ~5% de CPU; se não abrir, use `--encoder sw --fps 15`.

| Robô | Encoder | Comando |
|---|---|---|
| Pi 3 / Pi 4, câmera USB | `h264_v4l2m2m` (hardware) | `python3 robo_video.py --gs IP_DA_GS --pi 3 --camera usb` |
| Pi 3 / Pi 4, câmera CSI | hardware via `rpicam-vid` | `python3 robo_video.py --gs IP_DA_GS --pi 3 --camera csi` |
| Pi 5, câmera USB | `libx264 ultrafast` (Pi 5 não tem encoder H.264) | `python3 robo_video.py --gs IP_DA_GS --pi 5 --camera usb` |
| Pi 5, câmera CSI | `rpicam-vid` YUV + `libx264` | `python3 robo_video.py --gs IP_DA_GS --pi 5 --camera csi` |

`--mostrar` imprime o comando ffmpeg sem executar. No Pi 5, a codificação a 640×480@30 ocupa ~meio núcleo: considere isso se o YOLO também rodar no robô.

### 4.3 Ground station → servidor (obrigatório: WebRTC/WHIP)

Com a GS de referência (`rover-gs`) é só ligar o botão Retransmitir na tela. O restante desta seção é para quem fizer a própria GS.

```
URL           http://IP_DO_SERVIDOR:8889/rover/<id>/whip
Autorização   Authorization: Bearer <id>:<senha>     (ou HTTP Basic)
```

Referência sem tela: `gs_relay.py` + `whip_client.py` (Python, cliente WebRTC aiortc; não recodifica e funciona em Windows e Linux):

```
python -m pip install aiortc
python gs_relay.py --servidor IP_DO_SERVIDOR --id rover03 --senha SENHA
```

- Recebe o vídeo do robô em UDP `:5000`, entrega uma cópia local em `udp://127.0.0.1:5001` para o operador e publica no servidor sem recodificar, reconectando sozinho.
- Visualização do operador independe do servidor: `ffplay -fflags nobuffer -flags low_delay -framedrop udp://127.0.0.1:5001` ou OpenCV `cv2.VideoCapture("udp://127.0.0.1:5001", cv2.CAP_FFMPEG)`.
- Robô que serve RTSP: `--entrada rtsp://IP_DO_ROBO:8554/cam`.
- Alternativa com FFmpeg 8+ (`--cliente ffmpeg`): funciona no Linux; no Windows os builds do FFmpeg usam Schannel e o DTLS falha com o servidor ("Creating security context failed") — use o cliente Python.
- Conferir: `http://IP_DO_SERVIDOR:8080/arena.html?path=rover/<id>` deve mostrar o vídeo (Chrome, Edge ou Firefox).

### 4.4 Câmera da arena (para o operador)

Livre para todas as equipes, no navegador da ground station: `http://IP_DO_SERVIDOR:8080/arena.html` (WebRTC; WHEP em `http://IP_DO_SERVIDOR:8889/arena/whep`). O operador só pode ver o POV do próprio robô e a câmera da arena (não há visão direta da arena).

## 5. Proibições

- Publicar fora de `rover/<id>/#` ou vídeo fora de `rover/<id>` (bloqueado pelo servidor).
- Vídeo acima de 1500 kbps ou fora do padrão §4.1.
- Publicar `detection` em massa/repetida para "tentar" classes (vale a primeira válida).
- Declarar `AUTONOMO` enquanto pilota manualmente (o juiz decide pela observação, não pelo modo declarado).

## 6. Homologação (antes da prova, com o juiz)

| # | Verificação | Como o juiz confere |
|---|---|---|
| 1 | Conecta no MQTT com o ID da equipe; `status=online`; Last Will funciona (desligar → `offline`) | console do juiz, tabela Rovers |
| 2 | `state`, `mode` e `battery` chegam e mudam corretamente | painel / console |
| 3 | Vídeo do robô aparece no painel via relay da GS, sem listras, latência < 0,5 s | painel |
| 4 | Operador vê o POV local (sem servidor) e a câmera da arena | ground station |
| 5 | Mensagens `detection` aparecem no console do juiz; estados `ARTEFATO_ENCONTRADO` e `COLETA` chegam | rodada de teste |
| 6 | Alerta de autonomia aparece quando o robô está `MANUAL` em `NAVEGACAO` | console do juiz |
| 7 | Comandos da GS chegam ao robô; `cmd/mode` é confirmado em `rover/<id>/mode` | GS / log do robô |
| 8 | Homem-morto: desligar a GS com o robô andando → o robô para em ≤ 0,5 s | arena |

Ferramenta de autoteste (requer `pip install paho-mqtt aiortc` e um ffmpeg qualquer): `python sim_rover.py --host IP --id rover03 --password SENHA --tags 3 7 --video --ffmpeg C:\caminho\ffmpeg.exe`.
