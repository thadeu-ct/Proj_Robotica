# Ground station

Vídeo do robô → servidor da competição (WHIP) e câmera da arena → dashboard. Guia completo em [`docs/ground_station.md`](../docs/ground_station.md).

```
cp config_exemplo.json config_local.json      # preencher servidor, id e senha (fora do git)
PI_IP=192.168.1.70 ./iniciar_gs.sh            # liga tudo; dashboard em http://localhost:8000
./parar_gs.sh
```

| Arquivo | O que faz |
|---|---|
| `gs_video.py` | recebe H.264 do Pi em UDP :5000, publica por WHIP sem recodificar, copia para 5001/5002 e confere o padrão §4.1 |
| `ver_video.py` | POV cru da cópia local em MJPEG (`:8092/stream`) |
| `robo_simulado.py` | finge ser o robô (H.264 no padrão, ou `--fora-do-padrao`) |
| `iniciar_gs.sh` / `parar_gs.sh` | liga e desliga tudo |
