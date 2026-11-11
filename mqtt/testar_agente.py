#!/usr/bin/env python3
"""
Autoteste do robo_agente.py, no estilo da homologacao (contrato secao 6, itens 1, 2, 7 e 8).
Sobe o agente (motores simulados), conversa com ele pelo broker e mede o homem-morto pelo log.

    python testar_agente.py --servidor 127.0.0.1 --id rover03 --senha SENHA

Precisa de um broker MQTT no ar (o da competicao ou um local de teste).
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

AQUI = os.path.dirname(os.path.abspath(__file__))
p = argparse.ArgumentParser()
p.add_argument("--servidor", required=True)
p.add_argument("--porta", type=int, default=1883)
p.add_argument("--id", required=True)
p.add_argument("--senha", default="")
p.add_argument("--udp", type=int, default=8095)
args = p.parse_args()

base = "rover/%s/" % args.id
recebido = {}          # subtopico -> lista de (hora, payload)
cond = threading.Condition()
logs = []              # (hora, linha) do agente
resultados = []


def ao_receber(c, u, m):
    with cond:
        recebido.setdefault(m.topic[len(base):], []).append((time.time(), m.payload.decode()))
        cond.notify_all()


def esperar(sub, teste, timeout):
    """Espera uma mensagem em rover/<id>/<sub> que satisfaca teste(payload). Devolve (hora, payload) ou None."""
    fim = time.time() + timeout
    with cond:
        while True:
            for t, pl in recebido.get(sub, []):
                try:
                    if teste(pl):
                        return t, pl
                except ValueError:
                    pass
            if time.time() >= fim:
                return None
            cond.wait(timeout=fim - time.time())


def limpar(sub):
    with cond:
        recebido.pop(sub, None)


def esperar_log(padrao, depois, timeout):
    fim = time.time() + timeout
    while time.time() < fim:
        for t, linha in list(logs):
            if t >= depois and re.search(padrao, linha):
                return t, linha
        time.sleep(0.02)
    return None


def checar(nome, ok, detalhe=""):
    resultados.append(ok)
    print("%s  %s%s" % ("OK  " if ok else "FALHOU", nome, ("  (" + detalhe + ")") if detalhe else ""), flush=True)


def ler_agente(proc):
    for linha in proc.stdout:
        linha = linha.rstrip()
        m = re.match(r"(\d\d):(\d\d):(\d\d)\.(\d\d\d) ", linha)
        if m:   # hora do log do agente -> epoch (mesmo relogio desta maquina)
            h, mi, s, ms = map(int, m.groups())
            lt = time.localtime()
            t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, mi, s, 0, 0, -1)) + ms / 1000
            logs.append((t, linha))


def publicar(c, sub, carga, qos=1):
    carga["ts"] = int(time.time() * 1000)
    c.publish(base + "cmd/" + sub, json.dumps(carga), qos=qos)


obj = lambda campo, valor: (lambda pl: json.loads(pl).get(campo) == valor)  # noqa: E731

c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=args.id + "-teste")
c.username_pw_set(args.id, args.senha)
c.on_message = ao_receber
c.connect(args.servidor, args.porta)
c.subscribe(base + "#", qos=1)
c.loop_start()
time.sleep(0.5)

agente = subprocess.Popen([sys.executable, "-u", os.path.join(AQUI, "robo_agente.py"), "--servidor", args.servidor,
                           "--porta", str(args.porta), "--id", args.id, "--senha", args.senha, "--udp", str(args.udp)],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
threading.Thread(target=ler_agente, args=(agente,), daemon=True).start()
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    # 1. status online + mode/state retidos
    checar("1. status = online", esperar("status", lambda pl: pl == "online", 10) is not None)
    checar("2. mode = MANUAL ao conectar", esperar("mode", obj("mode", "MANUAL"), 3) is not None)
    checar("2. state = IDLE ao conectar", esperar("state", obj("state", "IDLE"), 3) is not None)

    # 8. homem-morto no MANUAL: cmd/vel a 10 Hz por 1,5 s e depois silencio
    inicio = time.time()
    for i in range(15):
        if i:
            time.sleep(0.1)
        publicar(c, "vel", {"linear": 0.5, "angular": 0.0}, qos=0)
    ultimo = time.time()      # hora do ultimo cmd/vel (a homologacao mede da GS desligada: ate 100 ms depois)
    andou = esperar_log(r"andando \(MANUAL\)", inicio, 2)
    parou = esperar_log(r"homem-morto: parado \(sem cmd/vel", ultimo, 2)
    checar("8. anda com cmd/vel", andou is not None)
    atraso = (parou[0] - ultimo) if parou else None
    checar("8. para em <= 0,5 s depois do ultimo cmd/vel", parou is not None and atraso <= 0.5,
           "%.0f ms depois do ultimo cmd/vel" % (atraso * 1000) if atraso is not None else "nao parou")

    # 7. cmd/mode confirmado em rover/<id>/mode
    limpar("mode")
    t0 = time.time()
    publicar(c, "mode", {"mode": "AUTONOMO"})
    r = esperar("mode", obj("mode", "AUTONOMO"), 3)
    checar("7. cmd/mode AUTONOMO confirmado", r is not None, "%.0f ms" % ((r[0] - t0) * 1000) if r else "")

    # controle autonomo por UDP (com o heartbeat da GS): estados do contrato e PARADO
    def autonomo(n, estado="APROXIMANDO"):
        for i in range(n):
            udp.sendto(json.dumps({"v": 0.1, "w": 0.0, "estado": estado, "seq": i}).encode(), ("127.0.0.1", args.udp))
            if i % 2 == 0:
                publicar(c, "heartbeat", {}, qos=0)
            time.sleep(0.05)
    limpar("state")
    t0 = time.time()
    autonomo(20)
    checar("8. anda no AUTONOMO com heartbeat da GS", esperar_log(r"andando \(AUTONOMO\)", t0, 1) is not None)
    checar("2. state segue o controle (APROXIMANDO -> BUSCA_ARTEFATO)", esperar("state", obj("state", "BUSCA_ARTEFATO"), 2) is not None)
    udp.sendto(json.dumps({"v": 0.0, "w": 0.0, "estado": "CHEGOU"}).encode(), ("127.0.0.1", args.udp))
    checar("2. state CHEGOU -> ARTEFATO_ENCONTRADO", esperar("state", obj("state", "ARTEFATO_ENCONTRADO"), 2) is not None)
    autonomo(10)
    t0 = time.time()
    udp.sendto(json.dumps({"v": 0.0, "w": 0.0, "estado": "PARADO"}).encode(), ("127.0.0.1", args.udp))
    parou = esperar_log(r"homem-morto: parado \(controle autonomo", t0, 1)
    checar("8. PARADO do controle para na hora", parou is not None and parou[0] - t0 < 0.15,
           "%.0f ms" % ((parou[0] - t0) * 1000) if parou else "nao parou")

    # 8. GS desligada no AUTONOMO: o controle continua mandando, mas o heartbeat some
    autonomo(10)
    t_gs = time.time()
    for i in range(20):
        udp.sendto(json.dumps({"v": 0.1, "w": 0.0, "estado": "APROXIMANDO"}).encode(), ("127.0.0.1", args.udp))
        time.sleep(0.05)
    parou = esperar_log(r"homem-morto: parado \(sem heartbeat da GS", t_gs, 1)
    checar("8. GS calada no AUTONOMO -> para em <= 0,5 s", parou is not None and parou[0] - t_gs <= 0.5,
           "%.0f ms depois do ultimo heartbeat" % ((parou[0] - t_gs) * 1000) if parou else "nao parou")

    # 2. battery
    udp.sendto(json.dumps({"bateria": {"v": 11.8, "pct": 71.0, "i": 1.3}}).encode(), ("127.0.0.1", args.udp))
    r = esperar("battery", lambda pl: json.loads(pl).get("v") == 11.8 and "ts" in json.loads(pl), 3)
    checar("2. battery publicada", r is not None, r[1] if r else "")

    # cmd/stop -> MANUAL + IDLE ; cmd/state (fase declarada pelo operador)
    limpar("mode")
    limpar("state")
    publicar(c, "stop", {})
    checar("cmd/stop -> modo MANUAL", esperar("mode", obj("mode", "MANUAL"), 2) is not None)
    checar("cmd/stop -> state IDLE", esperar("state", obj("state", "IDLE"), 2) is not None)
    publicar(c, "state", {"state": "COLETA"})
    checar("cmd/state COLETA", esperar("state", obj("state", "COLETA"), 2) is not None)

    # 1. Last Will: matar o agente sem aviso -> o broker publica offline
    limpar("status")
    t0 = time.time()
    agente.kill()
    r = esperar("status", lambda pl: pl == "offline", 15)
    checar("1. Last Will (agente morto -> offline)", r is not None, "%.1f s" % (r[0] - t0) if r else "")
finally:
    if agente.poll() is None:
        agente.kill()
    c.loop_stop()

print("\n%d de %d verificacoes OK" % (sum(resultados), len(resultados)))
sys.exit(0 if all(resultados) else 1)
