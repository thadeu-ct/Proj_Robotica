#!/usr/bin/env python3
"""
Seguir a AprilTag: roda NO Pi, le a tag calculada pelo camera_server.py (/tags) e manda velocidade
(v, w) para o ESP32 pelo protocolo de protocolo_esp32.py. O ESP32 faz o PI de cada roda.

    python3 seguir_tag.py                 # SIMULADO: so mostra o que mandaria (padrao, seguro)
    python3 seguir_tag.py --motores       # manda direto pela serial (config_seguir.json: serial/baud)
    python3 seguir_tag.py --agente        # manda ao agente MQTT (dono da serial), UDP 127.0.0.1:8095
    python3 seguir_tag.py --tag 3 --parar-a 25     # sobrescreve a config so nesta execucao

Parametros (tag alvo, distancia de parada, ganhos, limites): config_seguir.json, na mesma pasta.
Precisa do camera_server.py rodando (ele e o dono da camera). Usa so a biblioteca padrao + pyserial
(este so com --motores; no Pi ja existe no python3 do sistema).

Estados:
    PROCURANDO  tag alvo nao vista ha mais de procurar_apos_s: gira a procurar_w_rad_s (0 = fica parado)
    PERDIDA     tag sumiu ha mais de parar_se_perder_tag_s: para
    ALINHANDO   |angulo| > alinhar_antes_se_maior_que_graus: gira no lugar
    APROXIMANDO anda e corrige o rumo; v proporcional ao erro de distancia, w ao angulo
    CHEGOU      dentro da tolerancia de distancia e angulo: para (com histerese para nao ficar tremendo)
Convencao: v > 0 = frente; w > 0 = gira para a ESQUERDA. Na tag, angulo > 0 = tag a DIREITA -> w negativo.
"""
import argparse
import json
import math
import os
import signal
import sys
import threading
import time
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import protocolo_esp32 as proto  # noqa: E402


def mostrar(msg):
    """print que nunca derruba o controle (ex.: terminal ssh fechado com o robo andando)."""
    try:
        print(msg, flush=True)
    except (BrokenPipeError, OSError):
        pass


def carregar_config(path):
    with open(path) as f:
        cfg = json.load(f)
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


# --------------------------------------------------------------------------- leitura das tags do Pi
class LeitorTags(threading.Thread):
    """Long-poll em /tags: recebe cada resultado novo da AprilTag assim que o Pi calcula."""

    def __init__(self, url):
        super().__init__(daemon=True)
        self.url = url
        self.snap = None
        self.t_recv = 0.0
        self.erro = "aguardando o camera_server"
        self.rodando = True

    def run(self):
        seq = 0
        while self.rodando:
            try:
                with urllib.request.urlopen(f"{self.url}?depois={seq}", timeout=3) as r:
                    snap = json.loads(r.read().decode())
                seq = snap.get("seq", 0)
                self.snap, self.t_recv, self.erro = snap, time.time(), ""
            except Exception as e:
                self.erro = f"sem /tags ({e.__class__.__name__}: {e}). O camera_server.py esta rodando?"
                time.sleep(0.5)


# --------------------------------------------------------------------------- saida para os motores
# Toda saida tem a mesma interface: iniciar(), comando(v, w, estado, tag), fechar().
# Assim o controle nao sabe (nem precisa saber) se esta falando com a serial, com o agente MQTT ou com ninguem.
class SaidaSimulada:
    nome = "SIMULADO (nada vai para os motores)"

    def iniciar(self):
        pass

    def comando(self, v, w, estado, tag):
        pass

    def fechar(self):
        pass


class SaidaSerial:
    """Dono da serial: manda CMD_VEL direto ao ESP32. Use so quando NAO houver o agente MQTT rodando."""

    def __init__(self, porta, baud):
        import serial  # pyserial; no Pi: python3 do sistema ja tem
        self.s = serial.Serial(porta, baud, timeout=0)
        self.dec = proto.Decodificador()
        self.nome = f"serial {porta} @ {baud}"
        self.seq = 0
        self.telemetria = None        # ultimo dict de proto.decodificar_telemetria

    def _env(self, quadro):
        self.s.write(quadro)
        self.seq = (self.seq + 1) & 0xFF

    def iniciar(self):
        self._env(proto.montar_modo(proto.MODO_AUTONOMO, self.seq))

    def comando(self, v, w, estado, tag):
        self._env(proto.montar_cmd_vel(v, w, self.seq))
        for tipo, _, carga in self.dec.alimentar(self.s.read(4096)):
            if tipo == proto.TELEMETRIA:
                self.telemetria = proto.decodificar_telemetria(carga)

    def fechar(self):
        for _ in range(3):
            self._env(proto.montar_cmd_vel(0.0, 0.0, self.seq))
        self._env(proto.montar_modo(proto.MODO_ESPERA, self.seq))
        self.s.close()


class SaidaAgente:
    """Manda (v, w, estado) por UDP para o agente MQTT (mqtt/robo_agente.py), que e o dono da serial e
    decide o modo (MANUAL/AUTONOMO) e o homem-morto do contrato. Um datagrama JSON por passo (20 Hz):
        {"v": m/s, "w": rad/s, "estado": "APROXIMANDO", "seq": n, "ts": epoch_s,
         "tag": {"id": 0, "dist_cm": 52.3, "angulo_deg": 4.1} ou null}
    O agente para o robo se ficar mais de 0,5 s sem datagrama."""

    def __init__(self, destino):
        import socket
        host, porta = destino.rsplit(":", 1)
        self.end = (host, int(porta))
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.nome = f"agente MQTT udp://{destino}"
        self.seq = 0

    def iniciar(self):
        pass

    def comando(self, v, w, estado, tag):
        msg = {"v": round(v, 4), "w": round(w, 4), "estado": estado, "seq": self.seq, "ts": round(time.time(), 3),
               "tag": ({"id": tag["id"], "dist_cm": tag["dist_cm"], "angulo_deg": tag["angulo_deg"]} if tag else None)}
        self.sock.sendto(json.dumps(msg).encode(), self.end)
        self.seq += 1

    def fechar(self):
        for _ in range(3):
            self.comando(0.0, 0.0, "PARADO", None)
        self.sock.close()


# --------------------------------------------------------------------------- controle
def limitar(x, lim):
    return max(-lim, min(lim, x))


def rampa(atual, alvo, taxa, dt):
    passo = taxa * dt
    return atual + limitar(alvo - atual, passo)


class Seguidor:
    def __init__(self, cfg):
        self.c = cfg
        self.estado = "PROCURANDO"
        self.v = self.w = 0.0          # comando ja com rampa
        self.t_visto = 0.0             # ultima vez que a tag alvo apareceu

    def escolher(self, tags):
        validas = [t for t in tags if "dist_cm" in t]
        if self.c["tag_alvo"] is not None:
            validas = [t for t in validas if t["id"] == self.c["tag_alvo"]]
        return min(validas, key=lambda t: t["dist_cm"]) if validas else None

    def passo(self, snap, agora, dt):
        """Devolve (v, w, tag) ja com rampa. snap = ultimo JSON do /tags (ou None)."""
        c = self.c
        tag = self.escolher(snap["tags"]) if snap else None
        # tag de um quadro velho (camera_server travou ou tag sumiu) nao vale
        if tag is not None and agora - snap.get("ts_frame", agora) > 0.5:
            tag = None

        v_alvo = w_alvo = 0.0
        if tag is None:
            sumida = agora - self.t_visto
            if sumida > c["procurar_apos_s"]:
                self.estado = "PROCURANDO"
                w_alvo = c["procurar_w_rad_s"]
            elif sumida > c["parar_se_perder_tag_s"]:
                self.estado = "PERDIDA"
            # entre 0 e parar_se_perder_tag_s: mantem o comando anterior (tag piscou um quadro)
            else:
                v_alvo, w_alvo = self.v, self.w
        else:
            self.t_visto = agora
            erro_d = tag["dist_cm"] - c["parar_a_cm"]
            ang = tag["angulo_deg"]
            dentro = abs(erro_d) <= c["tolerancia_cm"] and abs(ang) <= c["tolerancia_graus"]
            saiu = abs(erro_d) > 2 * c["tolerancia_cm"] or abs(ang) > 2 * c["tolerancia_graus"]
            if dentro or (self.estado == "CHEGOU" and not saiu):
                self.estado = "CHEGOU"
            else:
                w_alvo = limitar(-c["ganho_w_por_grau"] * ang, c["w_max_rad_s"])
                if abs(ang) > c["alinhar_antes_se_maior_que_graus"]:
                    self.estado = "ALINHANDO"              # gira no lugar
                else:
                    self.estado = "APROXIMANDO"
                    fator = 1.0 - abs(ang) / c["alinhar_antes_se_maior_que_graus"]   # anda menos quando torto
                    v_alvo = limitar(c["ganho_v_por_cm"] * erro_d, c["v_max_m_s"]) * fator
                    if abs(erro_d) > c["tolerancia_cm"] and abs(v_alvo) < c["v_min_m_s"]:
                        v_alvo = math.copysign(c["v_min_m_s"], erro_d)   # vence o atrito no final da aproximacao
                    v_alvo = max(v_alvo, -c["v_max_m_s"] / 2)   # se passou do ponto, recua devagar

        self.v = rampa(self.v, v_alvo, c["rampa_v_m_s2"], dt)
        self.w = rampa(self.w, w_alvo, c["rampa_w_rad_s2"], dt)
        if self.estado in ("CHEGOU", "PERDIDA") and v_alvo == 0 and w_alvo == 0:
            # parada: zera direto abaixo de um limiar para nao ficar "arrastando" um resto de rampa
            if abs(self.v) < 0.01:
                self.v = 0.0
            if abs(self.w) < 0.05:
                self.w = 0.0
        return self.v, self.w, tag


def main():
    p = argparse.ArgumentParser(description="Robo vai ate a AprilTag e para (ver config_seguir.json)")
    p.add_argument("--config", default=os.path.join(AQUI, "config_seguir.json"))
    p.add_argument("--motores", action="store_true", help="manda direto pela serial ao ESP32 (sem agente MQTT)")
    p.add_argument("--agente", nargs="?", const="127.0.0.1:8095", default=None,
                   help="manda (v, w, estado) por UDP ao agente MQTT, que e o dono da serial (padrao 127.0.0.1:8095)")
    p.add_argument("--tag", type=int, default=None, help="sobrescreve tag_alvo (-1 = qualquer uma)")
    p.add_argument("--parar-a", type=float, default=None, help="sobrescreve parar_a_cm")
    p.add_argument("--segundos", type=float, default=0, help="encerra depois de N s (0 = ate Ctrl+C)")
    args = p.parse_args()

    cfg = carregar_config(args.config)
    if args.tag is not None:
        cfg["tag_alvo"] = None if args.tag < 0 else args.tag
    if args.parar_a is not None:
        cfg["parar_a_cm"] = args.parar_a

    if args.motores and args.agente:
        sys.exit("use --motores (serial direta) OU --agente (via agente MQTT), nao os dois")
    if args.motores:
        saida = SaidaSerial(cfg["serial"], cfg["baud"])
    elif args.agente:
        saida = SaidaAgente(args.agente)
    else:
        saida = SaidaSimulada()
    leitor = LeitorTags(cfg["url_tags"])
    leitor.start()
    seg = Seguidor(cfg)
    alvo = "qualquer tag" if cfg["tag_alvo"] is None else f"tag ID {cfg['tag_alvo']}"
    mostrar(f"[seguir] {alvo}, parar a {cfg['parar_a_cm']:.0f} cm | saida: {saida.nome} | Ctrl+C para")

    parar = threading.Event()
    signal.signal(signal.SIGINT, lambda *a: parar.set())
    signal.signal(signal.SIGTERM, lambda *a: parar.set())

    dt = 1.0 / cfg["taxa_comando_hz"]
    t0 = t_ant = time.monotonic()
    t_print = 0.0
    saida.iniciar()
    try:
        while not parar.is_set():
            agora_m = time.monotonic()
            v, w, tag = seg.passo(leitor.snap, time.time(), agora_m - t_ant)
            t_ant = agora_m
            saida.comando(v, w, seg.estado, tag)
            if agora_m - t_print >= 0.2:
                t_print = agora_m
                info = (f"ID {tag['id']} {tag['dist_cm']:5.1f} cm {tag['angulo_deg']:+5.1f} graus" if tag
                        else (leitor.erro or "sem tag"))
                mostrar(f"{time.strftime('%H:%M:%S')} {seg.estado:11s} v {v:+.3f} m/s  w {w:+.2f} rad/s  | {info}")
            if args.segundos and agora_m - t0 >= args.segundos:
                break
            parar.wait(max(0.0, dt - (time.monotonic() - agora_m)))
    finally:
        # para os motores (o ESP32 tambem para sozinho em 250 ms sem quadro; o agente, em 0,5 s)
        saida.fechar()
        leitor.rodando = False
        mostrar("[seguir] parado (mandou v=0, w=0)")


if __name__ == "__main__":
    main()
