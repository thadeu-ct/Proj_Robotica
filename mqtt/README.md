# mqtt — agente do robô

`robo_agente.py` roda **no Pi** e cumpre o contrato da competição (§3, em `docs/contrato_interface.md`): publica `status` (com Last Will), `state`, `mode` e `battery`, assina `rover/<id>/cmd/#`, faz o homem‑morto e é o **dono da serial do ESP32**. Visão geral em [`docs/ground_station.md`](../docs/ground_station.md).

O `ground_station/iniciar_gs.sh` instala e liga o agente no Pi (tmux `mqtt`), usando o `ground_station/config_local.json`.

```
python3 robo_agente.py                 # motores simulados (só imprime)
python3 robo_agente.py --motores       # manda para o ESP32 (serial/baud de controle/config_seguir.json)
```

| Arquivo | O que é |
|---|---|
| `robo_agente.py` | o agente |
| `config_robo.json` | parâmetros: v_max/w_max do MANUAL, homem‑morto (0,35 s), porta UDP do autônomo, bateria e mapeamento de estados |
| `config_local.json` | servidor, id e senha (fora do git; no Pi vem do `ground_station/config_local.json`) |
| `testar_agente.py` | autoteste estilo homologação: `python testar_agente.py --servidor 127.0.0.1 --id rover03 --senha X` |

Quem manda a velocidade: no **MANUAL**, o `cmd/vel` da GS; no **AUTONOMO**, o `controle/seguir_tag.py --agente` por UDP em `127.0.0.1:8095` (`{"v", "w", "estado"}`), traduzindo o estado para o contrato pelo `config_robo.json`.
