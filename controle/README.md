# controle — o robô vai até a AprilTag

Roda **no Pi**. Lê a tag que o `camera_server.py` já calcula (`http://127.0.0.1:8080/tags`), decide a velocidade linear e angular (v, w) e manda para o ESP32, que faz o controle PI de cada roda.

```
camera_server.py (/tags) ──► seguir_tag.py ──CMD_VEL (v, w) pela serial──► ESP32: PI por roda a 100 Hz ──► DRV8833 ──► motores NXT
```

| Arquivo | Papel |
|---|---|
| `seguir_tag.py` | Máquina de estados e controle proporcional. Por padrão roda **simulado** (só imprime). Com `--motores`, manda pela serial. |
| `config_seguir.json` | **Todos os parâmetros**: tag alvo, distância de parada, tolerâncias, ganhos, limites, rampas, serial. Edite aqui. |
| `protocolo_esp32.py` | Formato dos quadros Pi↔ESP32 (relatório §5.5). **Referência do firmware**: o ESP32 tem de seguir este arquivo. `python3 protocolo_esp32.py` roda o autoteste. |

## Uso (no Pi)

```bash
cd ~/yolo/robotica/controle
python3 seguir_tag.py                    # simulado: mostra estado, v, w e a tag
python3 seguir_tag.py --tag 3 --parar-a 25   # muda alvo/distância só nesta execução (--tag -1 = qualquer tag)
python3 seguir_tag.py --motores          # DE VERDADE (precisa do ESP32 com o firmware do protocolo)
```

O `camera_server.py` precisa estar rodando (`visao/pc/iniciar.sh` ou `ground_station/iniciar_gs.sh` sobem ele). Usa o `python3` do sistema, que já tem o `pyserial`. Para enviar ao Pi: `scp controle/*.py controle/config_seguir.json puc@<IP>:yolo/robotica/controle/`.

## Comportamento

| Estado | Quando | O que faz |
|---|---|---|
| APROXIMANDO | tag alvo vista e \|ângulo\| ≤ 15° | `v = ganho_v × (dist − parar_a_cm)`, entre `v_min` e `v_max`, menor quanto mais torto; `w = −ganho_w × ângulo` |
| ALINHANDO | \|ângulo\| > 15° | gira no lugar (`v = 0`) |
| CHEGOU | dist a ±`tolerancia_cm` do alvo e \|ângulo\| ≤ `tolerancia_graus` | para; só sai se o erro passar do dobro da tolerância (histerese) |
| PERDIDA | tag sumiu há mais de 0,5 s | para |
| PROCURANDO | tag sumiu há mais de 2 s | gira a `procurar_w_rad_s` (padrão 0 = continua parado) |

- Convenção: `v > 0` = frente; `w > 0` = gira para a **esquerda**. No `/tags`, ângulo > 0 = tag à **direita**, por isso `w` sai negativo.
- Tudo passa por uma rampa (`rampa_v_m_s2`, `rampa_w_rad_s2`) para não dar tranco.
- Tag de um quadro com mais de 0,5 s é ignorada: se o `camera_server` travar, o robô para.
- Ao sair (Ctrl+C ou erro), manda `v = w = 0` e o modo ESPERA. O ESP32 também precisa parar sozinho se passar 250 ms sem quadro (watchdog do relatório).

## Protocolo (resumo de `protocolo_esp32.py`)

`0xAA 0x55 | tipo | seq | len | carga | CRC-16/CCITT-FALSE (LE, sobre tipo..carga)`. `CMD_VEL` = 0x01 com `v` int16 em mm/s e `w` int16 em mrad/s; `MODE` = 0x03 (0 ESPERA, 1 AUTÔNOMO, 2 MANUAL); `HEARTBEAT` = 0x04; `TELEMETRIA` = 0x81 (a definir com o firmware). Comandos a 20 Hz.

## Validação (11/11/2026)

- Autoteste do protocolo: CRC (vetor `123456789` → 0x29B1), quadro partido ao meio, lixo antes do quadro e CRC errado descartado.
- Simulação de cinemática: tag a 80 cm e 25° → alinha, aproxima e para a 21,9 cm em 7 s; ignora outra tag quando há alvo definido; para quando a tag some ou quando o quadro é velho.
- No Pi real, simulado, com a tag ID 0 a 53,4 cm e +4,4°: `v = +0,106 m/s`, `w = −0,13 rad/s` (frente, virando para a direita), conforme a conta.
- **Ainda não testado com motores**: falta o firmware do ESP32, que depende da pinagem, da ligação USB/UART e da geometria das rodas (perguntas enviadas à eletrônica).
