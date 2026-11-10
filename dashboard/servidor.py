#!/usr/bin/env python3
"""
Servidor do dashboard do robo. Guarda o estado atual e repassa cada mudanca
para as telas abertas (Server-Sent Events). So usa a biblioteca padrao.

Uso:
    python servidor.py                  # http://localhost:8000
    python servidor.py --porta 8001

Endpoints:
    GET  /               dashboard
    GET  /api/estado     estado atual (JSON)
    POST /api/estado     atualiza o estado; manda so os campos que mudaram
    GET  /api/eventos    stream SSE usado pela pagina

Exemplo:
    curl -X POST localhost:8000/api/estado -d '{"bateria": 80, "modo": "AUTONOMO"}'
Formato completo no README.md desta pasta.
"""
import argparse
import copy
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))

p = argparse.ArgumentParser(description="Dashboard do robo")
p.add_argument("--porta", type=int, default=8000)
p.add_argument("--bind", default="0.0.0.0")
p.add_argument("--duracao", type=float, default=240, help="duracao da tentativa em segundos (padrao 4 min)")
args = p.parse_args()

ACOES_TEMPO = ("iniciar", "pausar", "continuar", "zerar")

estado = {
    "bateria": None,                 # 0 a 100 (%)
    "tensao": None,                  # volts, opcional
    "modo": "AGUARDANDO",
    "mensagem": "",
    "deteccoes": [],                 # [{"classe": "cilindro", "conf": 0.91, "caixa": [x1, y1, x2, y2]}]
    "pose": None,                    # {"x": m, "y": m, "rumo": graus}
    "video": {"vc": "", "vs": ""},   # URLs de MJPEG ou imagem
    "tempo": {"duracao": args.duracao, "restante": args.duracao, "rodando": False, "em": time.time()},
}
versao = 0
atualizado_em = {}                   # campo -> hora da ultima atualizacao
cond = threading.Condition()


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def mesclar(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            mesclar(dst[k], v)
        else:
            dst[k] = v


def restante_agora(t, agora):
    if not t["rodando"]:
        return t["restante"]
    return max(0.0, t["restante"] - (agora - t["em"]))


def aplicar_tempo(cmd, agora):
    """cmd: "iniciar" | "pausar" | ... ou {"acao": ..., "duracao": s, "restante": s}"""
    t = estado["tempo"]
    if isinstance(cmd, str):
        cmd = {"acao": cmd}
    if not isinstance(cmd, dict):
        raise ValueError("tempo deve ser texto ou objeto")
    acao = cmd.get("acao")
    if acao is not None and acao not in ACOES_TEMPO:
        raise ValueError("acao de tempo invalida: %r (use %s)" % (acao, ", ".join(ACOES_TEMPO)))
    duracao = float(cmd.get("duracao", t["duracao"]))
    restante = float(cmd["restante"]) if "restante" in cmd else None
    t["restante"], t["duracao"] = restante_agora(t, agora), duracao
    if acao == "iniciar":
        t["restante"], t["rodando"] = t["duracao"], True
    elif acao == "pausar":
        t["rodando"] = False
    elif acao == "continuar":
        t["rodando"] = t["restante"] > 0
    elif acao == "zerar":
        t["restante"], t["rodando"] = t["duracao"], False
    if restante is not None:
        t["restante"] = restante
    t["em"] = agora


def atualizar(dados):
    global versao
    if not isinstance(dados, dict):
        raise ValueError("o corpo deve ser um objeto JSON")
    agora = time.time()
    with cond:
        dados = dict(dados)
        if "tempo" in dados:
            aplicar_tempo(dados.pop("tempo"), agora)
            atualizado_em["tempo"] = agora
        mesclar(estado, dados)
        for k in dados:
            atualizado_em[k] = agora
        versao += 1
        cond.notify_all()


def instantaneo():
    with cond:
        agora = time.time()
        return versao, {
            "estado": copy.deepcopy(estado),
            "atualizado_em": dict(atualizado_em),
            "agora": agora,
        }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def cabecalhos(self, codigo, tipo, tamanho=None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        if tamanho is not None:
            self.send_header("Content-Length", str(tamanho))
        self.end_headers()

    def json(self, codigo, obj):
        corpo = json.dumps(obj, ensure_ascii=False).encode()
        self.cabecalhos(codigo, "application/json; charset=utf-8", len(corpo))
        self.wfile.write(corpo)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        rota = self.path.split("?")[0]
        if rota in ("/", "/index.html"):
            with open(os.path.join(AQUI, "index.html"), "rb") as f:
                corpo = f.read()
            self.cabecalhos(200, "text/html; charset=utf-8", len(corpo))
            self.wfile.write(corpo)
        elif rota == "/api/estado":
            self.json(200, instantaneo()[1])
        elif rota == "/api/eventos":
            self.eventos()
        else:
            self.json(404, {"erro": "rota inexistente"})

    def do_POST(self):
        if self.path.split("?")[0] != "/api/estado":
            return self.json(404, {"erro": "rota inexistente"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            atualizar(json.loads(self.rfile.read(n) or b"{}"))
        except (ValueError, TypeError) as e:
            return self.json(400, {"erro": str(e)})
        self.json(200, {"ok": True})

    def eventos(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.close_connection = True
        visto = -1
        try:
            while True:
                with cond:
                    if versao == visto:
                        cond.wait(timeout=15)
                v, snap = instantaneo()
                if v == visto:
                    self.wfile.write(b": ping\n\n")  # mantem a conexao viva
                else:
                    self.wfile.write(b"data: " + json.dumps(snap, ensure_ascii=False).encode() + b"\n\n")
                    visto = v
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer((args.bind, args.porta), Handler)
    srv.daemon_threads = True
    log("dashboard em http://localhost:%d  (POST JSON em /api/estado)" % args.porta)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
