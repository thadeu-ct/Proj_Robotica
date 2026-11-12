# firmware — ESP32 da locomoção

O ESP32 recebe do Pi a velocidade desejada (v, w), converte em velocidade de cada roda e controla as duas com um **PI a 100 Hz** usando os encoders dos motores NXT. Ele devolve telemetria a 50 Hz. Com isso, o robô **vai até a AprilTag**: o `controle/seguir_tag.py` (no Pi) decide v e w, e este firmware faz as rodas obedecerem.

```
Pi: camera_server (/tags) -> seguir_tag.py ou mqtt/robo_agente.py --CMD_VEL/MODE pela USB--> ESP32
ESP32: cinemática inversa -> PI por roda (100 Hz, encoders x4) -> DRV8833 -> motores NXT
ESP32 --TELEMETRIA (50 Hz)--> Pi
```

| Arquivo | Papel |
|---|---|
| `esp32_locomocao/config.h` | **Tudo o que se ajusta**: pinos, ligação com o Pi, roda e bitola, contagens por volta, ganhos, inversões, watchdog e modo bancada. |
| `esp32_locomocao/esp32_locomocao.ino` | Programa principal: PWM, encoders, protocolo, watchdog, laço de controle, telemetria e teste de bancada. |
| `esp32_locomocao/protocolo.h` | Protocolo do relatório (§5.5), igual a `controle/protocolo_esp32.py`. C++ puro. |
| `esp32_locomocao/controle_roda.h` | Cinemática diferencial, PI da roda e medidor de velocidade. C++ puro. |
| `teste_host/rodar_testes.sh` | Testes no computador, sem ESP32: protocolo C++↔Python e PI contra o modelo do motor. |

Pinos: **padrão da eletrônica** (`eletronica/Programação/DRV8833.ino`, commit `a5f0ba3`, e `eletronica/ESP32 DevKit/pinout.md`). Motor A: IN 12/13, encoder 34/35. Motor B: IN 18/19, encoder 36/39. nSLEEP: 14. Alimentação do encoder: 32 (HIGH) e 33 (LOW).

## Gravar

Arduino IDE: placa **ESP32 Dev Module** (pacote "esp32" da Espressif), abrir `esp32_locomocao/esp32_locomocao.ino` e gravar. Pela linha de comando (no Mac já está instalado em `~/.local/bin/arduino-cli`, com o core esp32 3.3.12):

```bash
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/esp32_locomocao
arduino-cli upload  --fqbn esp32:esp32:esp32 -p /dev/cu.usbserial-XXXX firmware/esp32_locomocao   # porta: arduino-cli board list
```

## Primeiro uso (robô SUSPENSO, rodas no ar)

1. Em `config.h`, coloque `MODO_BANCADA 1`, grave e abra o Monitor Serial a **115200**.
2. Para cada motor, o teste gira 2 s para um lado e 2 s para o outro e mostra as contagens. Confira:
   - qual motor é a roda **esquerda** → `MOTOR_ESQ`/`MOTOR_DIR`;
   - com comando "+", a roda vai para **frente**? Se não, `INVERTE_MOTOR[m] = true`;
   - com a roda indo para frente, as contagens são **positivas**? Se não, `INVERTE_ENCODER[m] = true`.
3. Gire cada roda uma volta à mão quando ele pedir e anote as contagens → `CONTAGENS_POR_VOLTA` (esperado ~720).
4. Meça o diâmetro da roda (`RAIO_RODA_M` = diâmetro/2) e a bitola (`BITOLA_M`).
5. Volte para `MODO_BANCADA 0`, grave, ligue o ESP32 no USB do Pi e rode, ainda suspenso: `python3 controle/seguir_tag.py --motores`.

## Comportamento

- A ponte H começa **desligada** (nSLEEP baixo). Ela só liga quando chega `MODE` AUTÔNOMO ou MANUAL. Em ESPERA, o `CMD_VEL` é ignorado.
- **Watchdog**: 250 ms sem quadro válido → zera, desliga a ponte, vai para ESPERA e marca `falhas` bit 0. Ela volta quando chegar `MODE` de novo, e por isso quem comanda (o `seguir_tag.py --motores` ou o agente MQTT) reenvia o `MODE` a cada 1 s.
- **PI por roda** (relatório §1.3): `u = FATOR_FF·(ref + τ·dref/dt)/K + Kp·e + Ki·∫e`, com rampa na referência, anti-windup por integração condicional e `u` em [-1, 1]. O erro compara a velocidade medida (média de 50 ms + EMA) com a **referência passada pelo mesmo filtro**, para o atraso da medição não virar sobressinal.
- PWM a 20 kHz, slow decay (IN1 em 1 e IN2 com o duty invertido), como no `DRV8833.ino`.
- Encoders em quadratura x4 por interrupção, nas bordas dos dois canais.
- Telemetria (0x81, 20 bytes): encoders, velocidade medida de cada roda (mm/s), modo, falhas. **Bateria, rumo e ToF ainda vão como 0** (INA219, MPU-6050 e ToF não estão integrados).

## Testes no computador (`firmware/teste_host/rodar_testes.sh`)

| Teste | Resultado (12/11/2026) |
|---|---|
| Protocolo: C++ lê os quadros do Python (lixo antes, quadro partido ao meio, CRC errado descartado) | ok |
| Telemetria: Python lê a montada em C++ | ok |
| PI, planta do relatório (K 17,8, τ 0,10 s), encoder de 720 contagens, ±8 rad/s | 95% em 0,21 s, sobressinal 0,1%, erro 0,05% |
| PI com motor 33% mais fraco / 35% mais forte que o modelo | sobressinal 1,6% / 14,5%, erro < 0,1% |
| Compilação para ESP32 (core 3.3.12), modo normal e modo bancada, `--warnings all` | sem avisos |

**Ainda não testado no robô.** Falta medir no robô: o sentido dos motores e dos encoders, as contagens por volta, o diâmetro da roda e a bitola. Também falta identificar K e τ de verdade com o ensaio ao degrau (relatório §1.2) e confirmar que o PWM de 20 kHz funciona bem no DRV8833 com os NXT.
