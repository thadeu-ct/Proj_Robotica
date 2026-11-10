# Dashboard do robô

Tela para todo mundo acompanhar o robô: vista superior, câmera, modo, itens vistos, tempo e bateria.
Só usa Python 3 (biblioteca padrão), sem instalar nada.

```
python servidor.py        # abre em http://localhost:8000  (F = tela cheia)
python simulador.py       # dados falsos para testar a tela
```

## Mandando dados

`POST /api/estado` com JSON. Mande só os campos que mudaram; o resto continua como está.

```
curl -X POST localhost:8000/api/estado -d '{"bateria": 80, "modo": "AUTONOMO"}'
```

Do Python, sem travar o laço de visão:

```python
from cliente import Dashboard
dash = Dashboard("http://IP_DO_PC:8000")
dash.enviar(bateria=80, deteccoes=[{"classe": "cilindro", "conf": 0.91}])
```

| Campo | Exemplo | Na tela |
|---|---|---|
| `bateria` | `80` | % de 0 a 100; a bateria esvazia e muda de cor (verde, amarelo, vermelho) |
| `tensao` | `7.9` | volts embaixo da % (opcional) |
| `modo` | `"MANUAL"` | texto livre; MANUAL, AUTONOMO, RETORNO e PARADO ganham cor |
| `tempo` | `"iniciar"` | `iniciar`, `pausar`, `continuar` ou `zerar`; ou `{"duracao": 240}` (padrão 4:00) |
| `deteccoes` | `[{"classe": "cilindro", "conf": 0.91, "caixa": [x1, y1, x2, y2]}]` | acende o ícone da classe; apaga sozinho 1,5 s depois do último envio |
| `video` | `{"vc": "http://IP:8080/stream", "vs": ""}` | URL de MJPEG ou imagem; `vc` = câmera do robô, `vs` = vista superior |
| `pose` | `{"x": 1.2, "y": 0.4, "rumo": 90}` | robô no mapa da arena (metros, origem no canto inferior esquerdo, rumo em graus a partir do eixo x); aparece quando `vs` está vazio |
| `arena` | `{"resgate": "SO"}` | canto da área de resgate no mapa: `NO`, `NE`, `SO` ou `SE` |
| `mensagem` | `"capturando cilindro"` | texto verde no rodapé |

Classes: `ampulheta`, `anel_base`, `anel_haste`, `cilindro`, `icosaedro`, `peca_chata`.
A `caixa` é opcional: em pixels do vídeo ou normalizada (0 a 1). Se o vídeo já vier anotado, não mande `caixa`, senão aparecem caixas duplicadas.

`GET /api/estado` devolve o estado atual.
