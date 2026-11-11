#!/usr/bin/env python3
"""
Telemetria e comandos da ground station (Mac), contrato secao 3:

  - pagina de controle do operador em http://localhost:8001 (teclado WASD, PARAR, modo, estado, garra, braco)
    -> publica rover/<id>/cmd/{vel,stop,mode,state,arm,gripper}
  - cmd/heartbeat a 10 Hz: sinal de vida da GS; sem ele o robo para no AUTONOMO (homem-morto, homologacao 8)
  - detection: le o YOLO (detector_pc.py, http://127.0.0.1:8091/deteccoes) e publica UMA deteccao por
    parada diante do artefato (estado ARTEFATO_ENCONTRADO), so quando a classe fica estavel. Botao manual tambem.
  - ponte para o dashboard (http://localhost:8000): modo, estado, bateria e o tempo oficial (arena/state)

Uso:
    python gs_mqtt.py                    # le config_local.json (servidor, id, senha)
    python gs_mqtt.py --servidor 192.168.0.10 --id rover03 --senha SENHA
"""
import argparse
import json
import os
import signal
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

AQUI = os.path.dirname(os.path.abspath(__file__))
HISTORICO = os.path.join(AQUI, "logs", "deteccoes_publicadas.json")   # sobrevive a um reinicio da GS


def ler_json(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    return {}


cfg = ler_json(os.path.join(AQUI, "config_gs.json"))
cfg.update(ler_json(os.path.join(AQUI, "config_local.json")))

p = argparse.ArgumentParser(description="MQTT da ground station (contrato secao 3)")
p.add_argument("--servidor", default=cfg.get("servidor"))
p.add_argument("--porta", type=int, default=cfg.get("porta_mqtt", 1883))
p.add_argument("--id", default=cfg.get("id"))
p.add_argument("--senha", default=os.environ.get("GS_SENHA", cfg.get("senha")))
p.add_argument("--http", type=int, default=cfg.get("porta_controle", 8001), help="porta da pagina de controle")
p.add_argument("--dashboard", default=cfg.get("dashboard", "http://127.0.0.1:8000"))
p.add_argument("--detector", default=cfg.get("detector", "http://127.0.0.1:8091/deteccoes"))
args = p.parse_args()

DET = cfg.get("deteccao", {})
DET_CONF_MIN = DET.get("conf_min", 0.6)
DET_ESTAVEL = DET.get("leituras_estaveis", 5)                 # leituras seguidas (5 Hz) com a mesma classe
DET_ESTADOS = DET.get("publicar_em_estados", ["ARTEFATO_ENCONTRADO"])
DET_REPETIR_S = DET.get("repetir_mesmo_artefato_apos_s", 30)   # evita repetir a mesma classe (contrato secao 5)
BRACO = cfg.get("braco", {})
ESTADOS = ("IDLE", "BUSCA", "NAVEGACAO", "BUSCA_ARTEFATO", "ARTEFATO_ENCONTRADO", "COLETA")
ARTEFATOS = ("hiperboloide", "argola_pequena", "argola_grande", "cilindro", "exotico")


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def ts():
    return int(time.time() * 1000)


def area(o):
    b = o.get("box") or [0, 0, 0, 0]
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def post_json(url, dados, timeout=0.5):
    req = urllib.request.Request(url, json.dumps(dados).encode(), {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=timeout).close()


class GS:
    def __init__(self):
        self.lock = threading.Lock()
        self.base = "rover/%s/" % args.id
        self.conectado = False
        self.robo = {"status": "?", "state": None, "mode": None, "battery": None}
        self.arena = None
        self.joy = (0.0, 0.0, 0.0)                     # linear, angular, hora da ultima atualizacao da pagina
        self.zeros = 0
        self.det_auto = DET.get("automatica", True)
        self.publicadas = []                           # ultimas deteccoes publicadas (para a pagina)
        self.det_ultima = {"objetos": []}
        self.det_seq = (None, 0)                       # classe estavel atual, leituras seguidas
        self.det_feita_neste_estado = False
        self.det_hora = {}                             # artefato -> hora da ultima publicacao
        try:
            with open(HISTORICO, encoding="utf-8") as f:
                self.det_hora = {k: float(v) for k, v in json.load(f).items()}
        except (OSError, ValueError):
            pass
        self.dash_ok = True

        self.c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=args.id + "-gs", protocol=mqtt.MQTTv311)
        self.c.username_pw_set(args.id, args.senha)
        self.c.reconnect_delay_set(min_delay=1, max_delay=5)
        self.c.on_connect = self.ao_conectar
        self.c.on_disconnect = self.ao_desconectar
        self.c.on_message = self.ao_receber

    # ---- MQTT
    def ao_conectar(self, c, u, flags, rc, props=None):
        if rc != 0:
            log("broker recusou a conexao: %s" % rc)
            return
        self.conectado = True
        log("conectado ao broker %s:%d como %s (client_id %s-gs)" % (args.servidor, args.porta, args.id, args.id))
        for sub in ("status", "state", "mode", "battery"):
            c.subscribe(self.base + sub, qos=1)
        c.subscribe("arena/state", qos=1)

    def ao_desconectar(self, c, u, flags, rc, props=None):
        if self.conectado:
            log("perdeu o broker (%s)" % rc)
        self.conectado = False

    def ao_receber(self, c, u, msg):
        try:
            texto = msg.payload.decode()
            dado = texto if msg.topic.endswith("/status") else json.loads(texto)
        except ValueError:
            return
        with self.lock:
            if msg.topic == "arena/state":
                self.arena = (dado, time.time())
            else:
                sub = msg.topic[len(self.base):]
                anterior = self.robo.get(sub)
                self.robo[sub] = dado
                if sub == "state" and (not anterior or anterior.get("state") != dado.get("state")):
                    self.det_feita_neste_estado = False
                    self.det_seq = (None, 0)
                if sub in ("status", "state", "mode") and anterior != dado:
                    log("robo %s: %s" % (sub, dado if sub == "status" else dado.get(sub)))
        self.atualizar_dashboard()

    def publicar_cmd(self, tipo, carga, qos=1):
        carga = dict(carga)
        carga["ts"] = ts()
        self.c.publish(self.base + "cmd/" + tipo, json.dumps(carga), qos=qos)

    # ---- laco de 10 Hz: cmd/vel enquanto ha comando, zeros ao soltar, heartbeat sempre
    def laco_10hz(self, parar):
        while not parar.wait(0.1):
            agora = time.time()
            with self.lock:
                lin, ang, t = self.joy
            if self.conectado:
                self.publicar_cmd("heartbeat", {}, qos=0)
                if agora - t < 0.3 and (lin or ang):
                    self.publicar_cmd("vel", {"linear": round(lin, 3), "angular": round(ang, 3)}, qos=0)
                    self.zeros = 3
                elif self.zeros:
                    self.publicar_cmd("vel", {"linear": 0.0, "angular": 0.0}, qos=0)
                    self.zeros -= 1

    # ---- deteccao (YOLO no detector_pc.py)
    def laco_deteccao(self, parar):
        erro_avisado = False
        while not parar.wait(0.2):
            try:
                with urllib.request.urlopen(args.detector, timeout=0.5) as r:
                    d = json.loads(r.read().decode())
                erro_avisado = False
            except (OSError, ValueError):
                if not erro_avisado:
                    log("detector fora do ar (%s); sem deteccao automatica" % args.detector)
                    erro_avisado = True
                d = {"objetos": [], "ts": 0}
            fresco = time.time() - d.get("ts", 0) < 1.0
            objetos = [o for o in d.get("objetos", []) if fresco and o.get("artifact") in ARTEFATOS]
            with self.lock:
                self.det_ultima = {"objetos": objetos, "tags": d.get("tags", []) if fresco else []}
                # a peca diante do robo e a de maior caixa na imagem (a mais proxima), nao a de maior confianca
                melhor = max((o for o in objetos if o["conf"] >= DET_CONF_MIN), key=area, default=None)
                classe = melhor["artifact"] if melhor else None
                ant, n = self.det_seq
                self.det_seq = (classe, n + 1 if classe and classe == ant else (1 if classe else 0))
                estado = (self.robo.get("state") or {}).get("state")
                pronto = (self.det_auto and classe and self.det_seq[1] >= DET_ESTAVEL
                          and estado in DET_ESTADOS and not self.det_feita_neste_estado)
                if pronto and time.time() - self.det_hora.get(classe, 0) < DET_REPETIR_S:
                    if not self.det_feita_neste_estado:
                        log("detection de %s nao repetida (ja publicada ha menos de %d s)" % (classe, DET_REPETIR_S))
                    self.det_feita_neste_estado = True
                    pronto = False
            if pronto:
                self.publicar_deteccao(classe, melhor["conf"], "automatica")
            self.atualizar_dashboard()

    def publicar_deteccao(self, artefato, conf, origem):
        carga = {"artifact": artefato, "conf": round(float(conf), 2)}
        self.publicar_cmd_raiz("detection", carga)
        with self.lock:
            self.det_feita_neste_estado = True
            self.det_hora[artefato] = time.time()
            try:
                os.makedirs(os.path.dirname(HISTORICO), exist_ok=True)
                with open(HISTORICO, "w", encoding="utf-8") as f:
                    json.dump(self.det_hora, f)
            except OSError as e:
                log("nao gravei o historico de deteccoes: %s" % e)
            self.publicadas = ([{"hora": time.strftime("%H:%M:%S"), "origem": origem, **carga}] + self.publicadas)[:10]
        log("detection publicada (%s): %s %.2f" % (origem, artefato, conf))

    def publicar_cmd_raiz(self, sub, carga):
        carga = dict(carga)
        carga["ts"] = ts()
        self.c.publish(self.base + sub, json.dumps(carga), qos=1, retain=False)

    # ---- dashboard
    def atualizar_dashboard(self):
        if not args.dashboard:
            return
        with self.lock:
            r, arena = dict(self.robo), self.arena
        dados = {}   # os itens vistos vao direto do detector_pc.py (--dashboard) para o dashboard
        if r.get("mode"):
            dados["modo"] = r["mode"].get("mode")
        if r.get("state"):
            dados["estado"] = r["state"].get("state")
        b = r.get("battery")
        if b:
            dados["bateria"] = b.get("pct")
            dados["tensao"] = b.get("v")
        partes = []
        if r.get("status") != "online":
            partes.append("ROBÔ OFFLINE" if r.get("status") == "offline" else "robô sem status")
        if not self.conectado:
            partes.append("GS sem broker")
        if arena:
            a, recebido = arena
            partes.append("rodada %s · %s pts" % (a.get("round", "?"), a.get("points", 0)))
            limite = a.get("time_limit_ms")
            if limite:
                decorrido = a.get("elapsed_ms", 0)
                if a.get("run") == "RUNNING":
                    decorrido += a.get("ts", 0) - a.get("t_ref", a.get("ts", 0))
                    decorrido += (time.time() - recebido) * 1000
                dados["tempo"] = {"duracao": limite / 1000, "restante": max(0, (limite - decorrido) / 1000),
                                  "acao": "continuar" if a.get("run") == "RUNNING" else "pausar"}
        dados["mensagem"] = " · ".join(partes)
        try:
            post_json(args.dashboard.rstrip("/") + "/api/estado", dados)
            self.dash_ok = True
        except OSError:
            if self.dash_ok:
                log("dashboard fora do ar (%s)" % args.dashboard)
            self.dash_ok = False

    # ---- pagina de controle
    def comando_da_pagina(self, tipo, d):
        if tipo == "joy":
            with self.lock:
                self.joy = (max(-1.0, min(1.0, float(d.get("linear", 0)))),
                            max(-1.0, min(1.0, float(d.get("angular", 0)))), time.time())
        elif tipo == "stop":
            with self.lock:
                self.joy = (0.0, 0.0, 0.0)
            self.publicar_cmd("stop", {})
            log("PARAR")
        elif tipo == "mode":
            if d.get("mode") not in ("MANUAL", "AUTONOMO"):
                raise ValueError("modo invalido")
            self.publicar_cmd("mode", {"mode": d["mode"]})
        elif tipo == "state":
            if d.get("state") not in ESTADOS:
                raise ValueError("estado invalido")
            self.publicar_cmd("state", {"state": d["state"]})
        elif tipo == "gripper":
            if d.get("action") not in ("open", "close"):
                raise ValueError("acao invalida")
            self.publicar_cmd("gripper", {"action": d["action"]})
        elif tipo == "arm":
            preset = d.get("preset")
            juntas = (BRACO.get("presets") or {}).get(preset)
            if juntas is None:
                raise ValueError("preset desconhecido")
            self.publicar_cmd("arm", {"joints": juntas, "preset": preset})
        elif tipo == "detection":
            if d.get("artifact") not in ARTEFATOS:
                raise ValueError("artefato invalido")
            with self.lock:
                conf = max((o["conf"] for o in self.det_ultima["objetos"] if o["artifact"] == d["artifact"]), default=1.0)
            self.publicar_deteccao(d["artifact"], conf, "manual")
        elif tipo == "det_auto":
            with self.lock:
                self.det_auto = bool(d.get("ligada"))
            log("deteccao automatica %s" % ("ligada" if self.det_auto else "desligada"))
        else:
            raise ValueError("comando desconhecido")

    def resumo(self):
        with self.lock:
            return {"conectado": self.conectado, "robo": self.robo, "det_auto": self.det_auto,
                    "publicadas": self.publicadas, "vendo": self.det_ultima["objetos"],
                    "arena": self.arena[0] if self.arena else None,
                    "presets": list((BRACO.get("presets") or {}).keys()), "id": args.id}


gs = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def responder(self, codigo, corpo, tipo="application/json; charset=utf-8"):
        corpo = corpo if isinstance(corpo, bytes) else json.dumps(corpo, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if self.path in ("/", "/controle.html"):
            with open(os.path.join(AQUI, "controle.html"), "rb") as f:
                self.responder(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/api/resumo":
            self.responder(200, gs.resumo())
        else:
            self.responder(404, {"erro": "rota inexistente"})

    def do_POST(self):
        if not self.path.startswith("/api/cmd/"):
            return self.responder(404, {"erro": "rota inexistente"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            gs.comando_da_pagina(self.path[len("/api/cmd/"):], json.loads(self.rfile.read(n) or b"{}"))
        except (ValueError, TypeError) as e:
            return self.responder(400, {"erro": str(e)})
        self.responder(200, {"ok": True})


if __name__ == "__main__":
    if not (args.servidor and args.id):
        raise SystemExit("faltam --servidor e --id (ou config_local.json)")
    gs = GS()
    parar = threading.Event()
    gs.c.connect_async(args.servidor, args.porta, keepalive=5)
    gs.c.loop_start()
    for f in (gs.laco_10hz, gs.laco_deteccao):
        threading.Thread(target=f, args=(parar,), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.http), Handler)
    srv.daemon_threads = True
    log("controle do operador em http://localhost:%d" % args.http)
    for sig in (signal.SIGINT, signal.SIGTERM):   # em segundo plano o SIGINT vem ignorado: trata os dois
        signal.signal(sig, lambda *a: threading.Thread(target=srv.shutdown, daemon=True).start())
    srv.serve_forever()
    log("encerrando: zera a velocidade e para o heartbeat")
    parar.set()
    gs.publicar_cmd("vel", {"linear": 0.0, "angular": 0.0}, qos=0)
    gs.c.disconnect()
    gs.c.loop_stop()
