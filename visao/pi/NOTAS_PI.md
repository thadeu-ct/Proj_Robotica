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