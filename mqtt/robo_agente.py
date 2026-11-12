#!/usr/bin/env python3
"""
Agente MQTT do robo (roda NO Pi). Cumpre o contrato da competicao (docs/contrato_interface.md, secao 3):

  publica   rover/<id>/status   online/offline (offline como Last Will)          QoS 1, retain
            rover/<id>/state    {"ts", "state"}                                   QoS 1, retain, a cada mudanca
            rover/<id>/mode     {"ts", "mode"}                                    QoS 1, retain, a cada mudanca
            rover/<id>/battery  {"ts", "v", "pct", "i"}                           QoS 0, 1 Hz (so quando houver leitura)
  assina    rover/<id>/cmd/#    vel, stop, mode, arm, gripper (+ state: nosso, para o operador declarar a fase)

E o DONO da serial do ESP32: manda CMD_VEL a 20 Hz (zeros quando parado), o que alimenta o watchdog
de 250 ms do ESP32. Quem manda a velocidade depende do modo:
  MANUAL    cmd/vel da ground station (normalizado -1..1, vezes v_max / w_max)
  AUTONOMO  o controle autonomo (controle/seguir_tag.py --agente), por UDP em 127.0.0.1:8095

Homem-morto (contrato 3.2 e homologacao 8): para os motores se ficar sem
  - cmd/vel por mais de homem_morto_s (0,35 s), no MANUAL (a GS manda a 10 Hz enquanto ha comando);
  - cmd/heartbeat da GS por mais de homem_morto_autonomo_s (1 s), no AUTONOMO (nosso topico; o gs_mqtt.py
    manda a 10 Hz): GS desligada ou rede caida -> para;
  - o controle autonomo (UDP local) por mais de homem_morto_s, no AUTONOMO;
  - conexao com o broker, nos dois modos.
0,35 s e nao 0,5 s no MANUAL: a homologacao mede da GS desligada ate o robo parar (<= 0,5 s), e o ultimo
cmd/vel chega ate 100 ms antes disso. 1 s no AUTONOMO: no Wi-Fi do Pi 3 o MQTT as vezes atrasa ate 0,5 s
(medido em 11/11), e 0,35 s faria o robo dar trancos sozinho.

Uso:
    python3 robo_agente.py                       # le config_local.json (servidor, id, senha); motores SIMULADOS
    python3 robo_agente.py --motores             # manda de verdade pela serial do ESP32
    python3 robo_agente.py --servidor 192.168.1.217 --id rover03 --senha SENHA

Entrada UDP do controle autonomo (um JSON por pacote, a cada passo do controle):
    {"v": 0.10, "w": -0.2, "estado": "APROXIMANDO"}    # v em m/s, w em rad/s (+ = esquerda)
    {"bateria": {"v": 7.8, "pct": 70, "i": 1.2}}        # leitura de bateria vinda de outro processo
"""
import argparse
import json
import os
import signal
import socket
import sys
import threading
import time

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

AQUI = os.path.dirname(os.path.abspath(__file__))
for d in (os.path.join(AQUI, "..", "controle"), os.path.join(AQUI, "controle"), AQUI):
    if os.path.exists(os.path.join(d, "protocolo_esp32.py")):
        sys.path.insert(0, d)
        break
import protocolo_esp32 as proto  # noqa: E402

def ler_json(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    return {}


cfg = ler_json(os.path.join(AQUI, "config_robo.json"))         # parametros (no git)
cfg.update(ler_json(os.path.join(AQUI, "config_local.json")))  # servidor, id e senha (fora do git)
# serial e baud: fonte unica no controle/config_seguir.json
CFG_SEGUIR = ler_json(os.path.join(os.path.dirname(proto.__file__), "config_seguir.json"))

p = argparse.ArgumentParser(description="Agente MQTT do robo (contrato secao 3)")
p.add_argument("--servidor", default=cfg.get("servidor"), help="IP do broker (servidor da competicao)")
p.add_argument("--porta", type=int, default=cfg.get("porta", 1883))
p.add_argument("--id", default=cfg.get("id"), help="ID da equipe (roverNN)")
p.add_argument("--senha", default=os.environ.get("ROVER_SENHA", cfg.get("senha")))
p.add_argument("--motores", action="store_true", help="manda pela serial (sem isso: simulado)")
p.add_argument("--serial", default=CFG_SEGUIR.get("serial", "auto"),
               help="padrao: controle/config_seguir.json; 'auto' (ou porta inexistente) procura o ESP32")
p.add_argument("--baud", type=int, default=CFG_SEGUIR.get("baud", 460800))
p.add_argument("--v-max", type=float, default=cfg.get("v_max_m_s", 0.20), help="m/s quando cmd/vel linear = 1")
p.add_argument("--w-max", type=float, default=cfg.get("w_max_rad_s", 1.5), help="rad/s quando cmd/vel angular = 1")
p.add_argument("--homem-morto", type=float, default=cfg.get("homem_morto_s", 0.35), help="s sem comando -> para")
p.add_argument("--homem-morto-autonomo", type=float, default=cfg.get("homem_morto_autonomo_s", 1.0),
               help="s sem cmd/heartbeat da GS no AUTONOMO -> para")
p.add_argument("--autonomo-sem-gs", action="store_true", default=cfg.get("autonomo_sem_gs", False),
               help="no AUTONOMO, nao exige o cmd/heartbeat da GS (so para teste)")
p.add_argument("--udp", type=int, default=cfg.get("udp_autonomo", 8095), help="porta UDP do controle autonomo")
p.add_argument("--modo-inicial", choices=("MANUAL", "AUTONOMO"), default="MANUAL")
p.add_argument("--andar-sem-broker", action="store_true", default=cfg.get("autonomo_sem_broker", False),
               help="no AUTONOMO, continua andando se o broker cair (o contrato pede parar; so para teste)")
args = p.parse_args()

ESTADOS = ("IDLE", "BUSCA", "NAVEGACAO", "BUSCA_ARTEFATO", "ARTEFATO_ENCONTRADO", "COLETA")
MODOS = ("MANUAL", "AUTONOMO")
# estados do controle autonomo (seguir_tag.py) -> estados do contrato; ajustavel no config_robo.json
DO_SEGUIDOR = cfg.get("estados_do_seguidor") or {
    "PROCURANDO": "BUSCA", "PERDIDA": "BUSCA", "ALINHANDO": "BUSCA_ARTEFATO",
    "APROXIMANDO": "BUSCA_ARTEFATO", "CHEGOU": "ARTEFATO_ENCONTRADO"}
BAT_VAZIA = cfg.get("bateria_v_vazia")    # para calcular a % a partir da tensao do ESP32
BAT_CHEIA = cfg.get("bateria_v_cheia")
TAXA_HZ = 20


def log(msg):
    t = time.time()
    print("%s.%03d %s" % (time.strftime("%H:%M:%S", time.localtime(t)), int(t * 1000) % 1000, msg), flush=True)


def ts():
    return int(time.time() * 1000)


def limitar(x, lim=1.0):
    return max(-lim, min(lim, x))


# --------------------------------------------------------------------------- saida para o ESP32
class SaidaSimulada:
    def __init__(self):
        self.ultimo = None

    def enviar(self, quadro, descricao):
        if descricao != self.ultimo:          # so imprime quando muda, para nao inundar o log
            log("[motores simulados] " + descricao)
            self.ultimo = descricao

    def ler(self):
        return b""


class SaidaSerial:
    """Serial do ESP32. "auto" (ou porta que nao existe) procura /dev/serial/by-id, ttyUSB e ttyACM.
    Se o cabo cair, tenta reabrir a cada 1 s; enquanto isso os quadros sao descartados."""

    def __init__(self, porta, baud):
        import serial  # pyserial: so precisa com --motores
        self.serial = serial
        self.porta_pedida, self.baud = porta, baud
        self.s = None
        self.t_tentativa = 0.0
        self.avisou = False
        self.abrir()

    def candidatas(self):
        import glob
        if self.porta_pedida != "auto" and os.path.exists(self.porta_pedida):
            return [self.porta_pedida]
        return sorted(glob.glob("/dev/serial/by-id/*")) + sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))

    def abrir(self):
        self.t_tentativa = time.monotonic()
        erro = "nenhuma porta serial"
        for porta in self.candidatas():
            try:
                self.s = self.serial.Serial(porta, self.baud, timeout=0, write_timeout=0)
                log("serial %s a %d baud" % (porta, self.baud))
                self.avisou = False
                return
            except (OSError, self.serial.SerialException) as e:
                erro = "%s: %s" % (porta, e)
        if not self.avisou:
            log("ESP32 nao encontrado (%s); tentando de novo a cada 1 s" % erro)
            self.avisou = True

    def pronta(self):
        if self.s is None and time.monotonic() - self.t_tentativa > 1.0:
            self.abrir()
        return self.s is not None

    def perdeu(self, e):
        log("serial caiu (%s)" % e)
        try:
            self.s.close()
        except (OSError, self.serial.SerialException):
            pass
        self.s = None

    def enviar(self, quadro, descricao):
        if not self.pronta():
            return
        try:
            self.s.write(quadro)
        except (OSError, self.serial.SerialException) as e:
            self.perdeu(e)

    def ler(self):
        if not self.pronta():
            return b""
        try:
            return self.s.read(256)
        except (OSError, self.serial.SerialException) as e:
            self.perdeu(e)
            return b""


# --------------------------------------------------------------------------- agente
class Agente:
    def __init__(self):
        self.lock = threading.Lock()
        self.modo = args.modo_inicial
        self.estado = "IDLE"
        self.conectado = False
        self.manual = (0.0, 0.0, 0.0)      # linear, angular (normalizados), hora
        self.t_gs = 0.0                    # ultimo sinal de vida da GS (cmd/heartbeat ou qualquer cmd)
        self.auto = (0.0, 0.0, 0.0)        # v, w (m/s, rad/s), hora
        self.bateria = None                # (dict, hora)
        self.falhas = set()
        self.motivo_parado = None
        self.esp_modo = None               # ultimo MODE mandado ao ESP32
        self.t_esp_modo = 0.0
        self.seq = 0
        self.saida = SaidaSerial(args.serial, args.baud) if args.motores else SaidaSimulada()
        self.dec = proto.Decodificador()
        self.base = "rover/%s/" % args.id

        self.c = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=args.id, protocol=mqtt.MQTTv311)
        self.c.username_pw_set(args.id, args.senha)
        self.c.will_set(self.base + "status", "offline", qos=1, retain=True)
        self.c.reconnect_delay_set(min_delay=1, max_delay=5)
        self.c.on_connect = self.ao_conectar
        self.c.on_disconnect = self.ao_desconectar
        self.c.on_message = self.ao_receber
        self.c.on_connect_fail = self.falhou_conectar
        self.avisou_falha = False

    # ---- MQTT
    def publicar(self, sub, carga, qos=1, retain=True):
        dados = carga if isinstance(carga, str) else json.dumps(carga)
        self.c.publish(self.base + sub, dados, qos=qos, retain=retain)

    def ao_conectar(self, c, u, flags, rc, props=None):
        if rc != 0:
            log("broker recusou a conexao: %s" % rc)
            return
        self.conectado = True
        self.avisou_falha = False
        log("conectado ao broker %s:%d como %s" % (args.servidor, args.porta, args.id))
        self.publicar("status", "online")
        self.publicar("mode", {"ts": ts(), "mode": self.modo})
        self.publicar("state", {"ts": ts(), "state": self.estado})
        c.subscribe(self.base + "cmd/#", qos=1)

    def falhou_conectar(self, c, u):
        if not self.avisou_falha:   # o paho tenta de novo sozinho; avisa uma vez para nao inundar o log
            log("nao conectou ao broker %s:%d (rede? IP do servidor?); tentando de novo" % (args.servidor, args.porta))
            self.avisou_falha = True

    def ao_desconectar(self, c, u, flags, rc, props=None):
        if self.conectado:
            log("perdeu o broker (%s): motores parados ate voltar" % rc)
        self.conectado = False

    def ao_receber(self, c, u, msg):
        if not msg.topic.startswith(self.base + "cmd/"):
            return
        cmd = msg.topic[len(self.base + "cmd/"):]
        try:
            d = json.loads(msg.payload.decode() or "{}")
            if not isinstance(d, dict):
                raise ValueError("payload nao e objeto")
        except ValueError as e:
            log("cmd/%s ignorado: %s" % (cmd, e))
            return
        agora = time.monotonic()
        with self.lock:
            self.t_gs = agora
            if cmd == "heartbeat":
                return
            if cmd == "vel":
                self.manual = (limitar(float(d.get("linear", 0))), limitar(float(d.get("angular", 0))), agora)
            elif cmd == "stop":
                self.manual = (0.0, 0.0, 0.0)
                self.auto = (0.0, 0.0, 0.0)
                log("PARAR recebido: motores zerados, modo MANUAL")
                self.trocar_modo("MANUAL")
                self.trocar_estado("IDLE")
            elif cmd == "mode":
                m = str(d.get("mode", "")).upper()
                if m in MODOS:
                    self.trocar_modo(m)
                else:
                    log("cmd/mode invalido: %r" % d.get("mode"))
            elif cmd == "state":
                s = str(d.get("state", "")).upper()
                if s in ESTADOS:
                    self.trocar_estado(s)
                else:
                    log("cmd/state invalido: %r" % d.get("state"))
            elif cmd in ("arm", "gripper"):
                log("cmd/%s %s (o ESP32 ainda nao tem quadro para isso)" % (cmd, json.dumps(d)))
            else:
                log("cmd/%s desconhecido" % cmd)

    def trocar_modo(self, m):
        """Chamar com o lock. Confirma em rover/<id>/mode (o contrato pede a confirmacao)."""
        if m != self.modo:
            log("modo: %s -> %s" % (self.modo, m))
        self.modo = m
        self.manual = (0.0, 0.0, 0.0)
        self.t_esp_modo = 0.0              # o laco dos motores manda o MODE novo no proximo passo
        self.publicar("mode", {"ts": ts(), "mode": m})

    def trocar_estado(self, s):
        if s != self.estado:
            log("estado: %s -> %s" % (self.estado, s))
            self.estado = s
            self.publicar("state", {"ts": ts(), "state": s})

    # ---- ESP32
    def prox_seq(self):
        self.seq = (self.seq + 1) & 0xFF
        return self.seq

    def enviar(self, quadro, descricao):
        try:
            self.saida.enviar(quadro, descricao)
        except OSError as e:
            log("erro na serial: %s" % e)

    def velocidade(self, agora):
        """(v, w, motivo_de_parada). Chamar com o lock."""
        if not self.conectado and not (self.modo == "AUTONOMO" and args.andar_sem_broker):
            return 0.0, 0.0, "sem broker"
        if self.modo == "MANUAL":
            lin, ang, t = self.manual
            if agora - t > args.homem_morto:
                return 0.0, 0.0, "sem cmd/vel ha mais de %.2f s" % args.homem_morto
            return lin * args.v_max, ang * args.w_max, None
        if not args.autonomo_sem_gs and agora - self.t_gs > args.homem_morto_autonomo:
            return 0.0, 0.0, "sem heartbeat da GS ha mais de %.2f s" % args.homem_morto_autonomo
        v, w, t = self.auto
        if agora - t > args.homem_morto:
            return 0.0, 0.0, "controle autonomo calado ha mais de %.2f s" % args.homem_morto
        return v, w, None

    def modo_esp32(self, agora):
        """MODE para o ESP32. Chamar com o lock. ESPERA (ponte H desligada) sem broker ou sem a GS."""
        sem_broker = not self.conectado and not (self.modo == "AUTONOMO" and args.andar_sem_broker)
        sem_gs = agora - self.t_gs > args.homem_morto_autonomo and not (self.modo == "AUTONOMO" and args.autonomo_sem_gs)
        if sem_broker or sem_gs:
            return proto.MODO_ESPERA, "ESPERA"
        return (proto.MODO_AUTONOMO, "AUTONOMO") if self.modo == "AUTONOMO" else (proto.MODO_MANUAL, "MANUAL")

    def laco_motores(self, parar):
        periodo = 1.0 / TAXA_HZ
        prox = time.monotonic()
        while not parar.is_set():
            agora = time.monotonic()
            with self.lock:
                # O ESP32 comeca em ESPERA, ignora CMD_VEL em ESPERA e cai para ESPERA no watchdog (250 ms):
                # o MODE vai na hora em que muda e de novo a cada 1 s.
                modo_esp, nome_esp = self.modo_esp32(agora)
                mandar_modo = modo_esp != self.esp_modo or agora - self.t_esp_modo >= 1.0
                if mandar_modo:
                    if modo_esp != self.esp_modo:
                        log("ESP32: MODE %s" % nome_esp)
                    self.esp_modo, self.t_esp_modo = modo_esp, agora
                v, w, motivo = self.velocidade(agora)
                if motivo != self.motivo_parado:
                    if motivo and (self.motivo_parado is None):
                        log("homem-morto: parado (%s)" % motivo)
                    elif motivo is None:
                        log("comando ativo (%s)" % self.modo)
                    self.motivo_parado = motivo
            if mandar_modo:
                self.enviar(proto.montar_modo(modo_esp, self.prox_seq()), "MODE %s" % nome_esp)
            self.enviar(proto.montar_cmd_vel(v, w, self.prox_seq()), "CMD_VEL v=%.2f m/s w=%.2f rad/s" % (v, w))
            for tipo, _seq, carga in self.dec.alimentar(self.ler()):
                if tipo == proto.TELEMETRIA:
                    self.telemetria_esp32(carga)
            prox += periodo
            time.sleep(max(0.0, prox - time.monotonic()))
        self.enviar(proto.montar_cmd_vel(0, 0, self.prox_seq()), "CMD_VEL v=0.00 m/s w=0.00 rad/s")
        self.enviar(proto.montar_modo(proto.MODO_ESPERA, self.prox_seq()), "MODE ESPERA")

    def ler(self):
        try:
            return self.saida.ler()
        except OSError:
            return b""

    def telemetria_esp32(self, carga):
        try:
            t = proto.decodificar_telemetria(carga)
        except ValueError as e:
            log("telemetria do ESP32 invalida: %s" % e)
            return
        if t["bateria_v"] <= 0:            # 0 mV = sem leitura (INA219 ainda nao integrado): nao publica
            bat = None
        else:
            bat = {"v": t["bateria_v"]}
        if bat and BAT_VAZIA is not None and BAT_CHEIA is not None and BAT_CHEIA > BAT_VAZIA:
            bat["pct"] = max(0.0, min(100.0, 100 * (t["bateria_v"] - BAT_VAZIA) / (BAT_CHEIA - BAT_VAZIA)))
        with self.lock:
            if bat:
                self.bateria = (bat, time.monotonic())
            novas = set(t["falhas"]) - self.falhas
            self.falhas = set(t["falhas"])
        if novas:
            log("ESP32 reportou falha: %s" % ", ".join(sorted(novas)))

    # ---- controle autonomo e bateria por UDP
    def laco_udp(self, parar):
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.bind(("127.0.0.1", args.udp))
        sk.settimeout(0.5)
        log("controle autonomo: UDP 127.0.0.1:%d" % args.udp)
        while not parar.is_set():
            try:
                dados, _ = sk.recvfrom(2048)
                d = json.loads(dados.decode())
            except socket.timeout:
                continue
            except ValueError:
                continue
            with self.lock:
                est = str(d.get("estado", "")).upper()
                if est == "PARADO":                  # o seguir_tag encerrou: para ja, sem esperar o homem-morto
                    self.auto = (0.0, 0.0, 0.0)
                elif "v" in d and "w" in d:
                    self.auto = (float(d["v"]), float(d["w"]), time.monotonic())
                    est = DO_SEGUIDOR.get(est, est)
                    if self.modo == "AUTONOMO" and est in ESTADOS:
                        self.trocar_estado(est)
                if isinstance(d.get("bateria"), dict):
                    self.bateria = (d["bateria"], time.monotonic())

    def laco_bateria(self, parar):
        while not parar.wait(1.0):
            with self.lock:
                b = self.bateria
            if b and time.monotonic() - b[1] < 3 and self.conectado:
                carga = {"ts": ts()}
                for k in ("v", "pct", "i"):
                    if b[0].get(k) is not None:
                        carga[k] = round(float(b[0][k]), 2)
                self.publicar("battery", carga, qos=0, retain=False)

    def rodar(self):
        parar = threading.Event()
        signal.signal(signal.SIGINT, lambda *a: parar.set())
        signal.signal(signal.SIGTERM, lambda *a: parar.set())
        log("modo inicial %s, motores %s" % (self.modo, "REAIS" if args.motores else "simulados"))
        self.c.connect_async(args.servidor, args.porta, keepalive=2)   # perda do broker notada em ~2-3 s
        self.c.loop_start()
        lacos = [threading.Thread(target=f, args=(parar,), daemon=True)
                 for f in (self.laco_motores, self.laco_udp, self.laco_bateria)]
        for t in lacos:
            t.start()
        parar.wait()
        log("encerrando: motores parados, status offline")
        lacos[0].join(timeout=1)
        if self.conectado:   # saida limpa nao dispara o Last Will: publica o offline na mao
            self.c.publish(self.base + "status", "offline", qos=1, retain=True).wait_for_publish(2)
        self.c.disconnect()
        self.c.loop_stop()


if __name__ == "__main__":
    if not (args.servidor and args.id):
        raise SystemExit("faltam --servidor e --id (ou config_local.json)")
    Agente().rodar()
