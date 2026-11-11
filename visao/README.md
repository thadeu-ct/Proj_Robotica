# visao — percepção do rover

| Pasta / arquivo | O que é |
|---|---|
| [`pi/`](pi/README.md) | Roda no Raspberry Pi: câmera, **AprilTag embarcada** (distância e ângulo) e vídeo H.264/MJPEG. |
| [`pc/`](pc/README.md) | Roda no Mac: **YOLO** sobre o vídeo do Pi, desenho das tags vindas do Pi, saída anotada para o dashboard e calibração da câmera. |
| `tags/` | Folha A4 para imprimir com as AprilTags `tag25h9` IDs 0–7 de **42 mm** (o tamanho do lab). Imprimir em 100%. |
| `*.py` na raiz, `WIN_*.jpg`, `hello.png` | Código e fotos originais do grupo (vindos de STimmy10/Robotica), mantidos como referência. As fotos `WIN_20260923_15_03_*` são o tabuleiro 8x5 usado no `pi/camera_lab.json`; as `15_28_*` mostram a tag do lab. **Não use esse código**: ele tem um `tag_size` errado (4 mm) e uma calibração sobreajustada. |

Arquitetura geral, portas e armadilhas: [`../AGENTS.md`](../AGENTS.md).
