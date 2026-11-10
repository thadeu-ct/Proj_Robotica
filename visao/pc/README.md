# Visão no computador (o Pi só manda a imagem)

O Raspberry Pi 3 só captura a webcam e transmite em MJPEG (`visao/pi/camera_server.py`).
O computador recebe o vídeo e roda tudo:

- **YOLO** (`visao/pi/models/nano_LR.pt`): caixas de ampulheta, anel_base, anel_haste, cilindro, icosaedro e peca_chata.
- **AprilTag** (tag25h9, que é a do lab, e tag36h11): contorno, ID, **distância em cm** e ângulo (+ = tag à direita).

```
Pi 3 (webcam USB) --MJPEG pelo Wi-Fi (ou túnel SSH)--> Mac: YOLO + AprilTag + janela
```

## Na aula: passo a passo

1. Ligue o Pi com a webcam USB e conecte o Mac **no mesmo Wi-Fi** do Pi.
2. No Mac:
   ```bash
   cd ~/Proj_Robotica/visao/pc
   ./iniciar.sh
   ```
   O script pede a senha do Pi uma vez. Depois ele envia o servidor de câmera, inicia esse servidor no Pi e abre a janela do detector.
   - Se o IP mudou: `./iniciar.sh 192.168.1.XX`. Também dá para tentar `./iniciar.sh puc1.local`. Para descobrir o IP, rode no Pi: `hostname -I`.
3. **Meça a tag**: o lado da borda preta externa, sem a margem branca. O padrão é 100 mm. Se for diferente:
   `./iniciar.sh 192.168.1.70 --tag-mm 80`
4. **Ajuste da distância** (30 segundos): ponha a tag a 50 cm da câmera, medidos com trena, de frente, e aperte **c** na janela. Isso corrige o erro da calibração e do tamanho da tag. Para usar outra distância de referência: `--dist-ref 40`.
5. Para encerrar: **q** fecha a janela. O servidor continua rodando no Pi; para parar, use `./parar.sh`.

### Teclas da janela

| Tecla | Ação |
|---|---|
| q / Esc | sair |
| s | salvar a imagem (anotada e crua) em `pc/capturas/` |
| c | ajustar a distância com UMA tag a `--dist-ref` cm (padrão 50) |
| r | zerar o ajuste |
| y / a | liga ou desliga o YOLO / a AprilTag |
| + / - | confiança mínima do YOLO (padrão 0,50) |

### Calibrar a câmera (opcional, melhora a distância em ângulo e nas bordas)

Hoje ele usa `camera_lab.json`, que foi calculado com as 6 fotos do tabuleiro de 23/09 (fx ≈ 826, cerca de 42° de campo). Para calibrar a câmera do Pi com o tabuleiro 8x5 do lab:

```bash
.venv/bin/python calibrar_camera.py --pi 192.168.1.70 --auto
```

Mova o tabuleiro devagar por toda a imagem (perto, longe, nos cantos, inclinado). Com 20 capturas ele salva `camera_pi.json`, e o detector passa a usar esse arquivo sozinho. O erro precisa ficar abaixo de 1 px; o ideal é abaixo de 0,5.

## Se der problema

| Sintoma | O que fazer |
|---|---|
| `o Pi nao responde` | Mesmo Wi-Fi? Confira o IP com `hostname -I` no Pi e rode `./iniciar.sh IP_NOVO` |
| SSH cai no meio | No Pi: `sudo iw wlan0 set power_save off` (power save do Wi-Fi) |
| `nenhuma camera abriu` | Webcam conectada? No Pi: `ls /dev/video*`. Se outro programa estiver usando a câmera, o script fecha o `teste_visao.py` sozinho |
| A janela fica em "Aguardando imagem..." | No Pi: `tmux attach -t cam` mostra o servidor (para sair sem parar: Ctrl+B, depois D) |
| A rede bloqueia a porta 8080 | O `iniciar.sh` percebe e passa o vídeo por um túnel SSH sozinho |
| Distância errada sempre na mesma proporção | Confira `--tag-mm` e faça o ajuste com a tecla **c** |
| Vídeo travando ou lento | No Pi, rode o servidor com menos fps: `python camera_server.py --max-fps 12 --quality 70` |

### Sem o script (manual, dois terminais)

```bash
# Terminal 1 (Mac -> Pi)
scp ~/Proj_Robotica/visao/pi/camera_server.py puc@192.168.1.70:~/yolo/robotica/
ssh puc@192.168.1.70
cd ~/yolo/robotica && source ../.venv/bin/activate && python camera_server.py

# Terminal 2 (Mac)
cd ~/Proj_Robotica/visao/pc && .venv/bin/python detector_pc.py --pi 192.168.1.70
```

Para conferir só a câmera, sem processar nada, abra `http://192.168.1.70:8080` no navegador.

## Testar sem o Pi

```bash
./iniciar.sh local                                     # webcam do Mac
.venv/bin/python detector_pc.py --arquivo foto.jpg     # imagem ou vídeo
```

## Instalação (só se a pasta `.venv` não existir)

O `iniciar.sh` cria o ambiente sozinho com `uv`. Para fazer à mão: `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt`.
O modelo (`visao/pi/models/`) fica fora do git; ele vem do `visao_pi.tar.gz`.

## O que foi validado (10/11/2026, sem o Pi)

- Distância da tag em imagens sintéticas com pose conhecida: erro abaixo de 1% de 18 a 180 cm, nas duas famílias, com a tag inclinada até 45°.
- Fotos reais do lab (23/09): a tag25h9 ID 5 foi detectada a 47–69 cm em 4 das 6 fotos. Nas outras 2, a tag está tapada pelo dedo ou cortada pela borda. O `pupil-apriltags` não detectou nenhuma delas, e por isso a detecção usa o `cv2.aruco`.
- YOLO no Mac (MPS): 109 de 109 objetos rotulados do dataset foram detectados com a classe certa (conf. 0,4), em cerca de 12 ms por quadro.
- Fluxo completo `iniciar.sh` contra um "Pi" simulado (ssh/scp falsos rodando o script remoto): envio, servidor, vídeo direto e pelo túnel, detector. 20 fps (o limite do servidor), com cerca de 40 ms de atraso.
- Reconexão: o detector espera e volta sozinho quando o servidor reinicia.
