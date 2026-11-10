#!/usr/bin/env python3
"""
Manda dados falsos para o dashboard, para testar a tela sem o robo.

    python servidor.py          # em um terminal
    python simulador.py         # em outro
    python simulador.py --vc http://192.168.1.70:8080/stream   # usa o video real do Pi
"""
import argparse
import math
import random
import time

from cliente import Dashboard

p = argparse.ArgumentParser()
p.add_argument("--url", default="http://localhost:8000")
p.add_argument("--vc", default="", help="URL do video da camera do robo")
p.add_argument("--vs", default="", help="URL do video da vista superior")
args = p.parse_args()

CLASSES = ["ampulheta", "anel_base", "anel_haste", "cilindro", "icosaedro", "peca_chata"]
MODOS = ["MANUAL", "AUTONOMO", "RETORNO"]

dash = Dashboard(args.url)
dash.enviar(video={"vc": args.vc, "vs": args.vs}, tempo="iniciar", mensagem="simulador ligado")
bateria, t0 = 100.0, time.time()
try:
    while True:
        t = time.time() - t0
        bateria = max(0.0, bateria - 0.15)
        dets = []
        for c in random.sample(CLASSES, random.choice([0, 1, 1, 2])):
            x, y = random.uniform(0.05, 0.7), random.uniform(0.2, 0.7)
            dets.append({"classe": c, "conf": round(random.uniform(0.55, 0.98), 2),
                         "caixa": [x, y, x + random.uniform(0.1, 0.25), y + random.uniform(0.1, 0.25)]})
        dash.enviar(
            bateria=round(bateria, 1),
            tensao=round(6.4 + 2.0 * bateria / 100, 2),
            modo=MODOS[int(t // 10) % len(MODOS)],
            deteccoes=dets,
            pose={"x": 1 + 0.6 * math.cos(t / 5), "y": 1 + 0.6 * math.sin(t / 5), "rumo": math.degrees(t / 5) + 90},
            mensagem="vendo: " + ", ".join(d["classe"] for d in dets) if dets else "procurando alvo",
        )
        time.sleep(0.5)
except KeyboardInterrupt:
    pass
