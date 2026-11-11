#!/usr/bin/env python3
"""
Ground station (Mac): recebe o video H.264 do robo e repassa, sem recodificar:
  - para o servidor da competicao por WebRTC/WHIP  (http://SERVIDOR:8889/rover/<id>/whip)
  - para copias locais em UDP (MPEG-TS), usadas pelo detector e pelo operador

    robo --H.264 MPEG-TS/UDP--> :5000 --> [gs_video.py] --WHIP--> servidor
                                                 '--> udp://127.0.0.1:5001 (detector_pc / ffplay)

Tambem confere o video contra o padrao do contrato (secao 4.1) e avisa se algo sair dele.

Uso:
    python gs_video.py                       # le config_local.json (servidor, id, senha)
    python gs_video.py --servidor 192.168.0.10 --id rover03 --senha SENHA
    python gs_video.py --sem-servidor        # so as copias locais (teste sem o servidor)
    python gs_video.py --local udp://127.0.0.1:5001 --local udp://127.0.0.1:5002

Ver o video localmente:  python ver_video.py udp://127.0.0.1:5002
"""
import argparse
import asyncio
import base64
import fractions
import json
import os
import signal
import threading
import time

import aiohttp
import av
from aiortc import (RTCConfiguration, RTCPeerConnection, RTCRtpSender,
                    RTCSessionDescription)
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack

AQUI = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(AQUI, "config_local.json")

cfg = {}
if os.path.exists(CONFIG):
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)

p = argparse.ArgumentParser(description="Relay de video da ground station (UDP -> WHIP + copia local)")
p.add_argument("--servidor", default=cfg.get("servidor"), help="IP do servidor da competicao")
p.add_argument("--id", default=cfg.get("id"), help="ID da equipe (roverNN)")
p.add_argument("--senha", default=os.environ.get("GS_SENHA", cfg.get("senha")), help="senha da equipe (ou GS_SENHA)")
p.add_argument("--auth", choices=("bearer", "basic"), default=cfg.get("auth", "bearer"),
               help="Authorization: Bearer <id>:<senha> (padrao do contrato) ou HTTP Basic")
p.add_argument("--whip-url", default=cfg.get("whip_url"), help="URL WHIP completa (padrao: http://SERVIDOR:8889/rover/<id>/whip)")
p.add_argument("--sem-servidor", action="store_true", help="nao publica no servidor, so as copias locais")
p.add_argument("--entrada", default=cfg.get("entrada", "udp://0.0.0.0:5000"),
               help="onde o robo entrega o video (UDP MPEG-TS; aceita rtsp://...)")
p.add_argument("--local", action="append", default=None,
               help="copia local em MPEG-TS/UDP (pode repetir). Padrao: udp://127.0.0.1:5001")
p.add_argument("--dashboard", default=cfg.get("dashboard", "http://127.0.0.1:8000"),
               help="dashboard para receber a URL da camera da arena ('' desliga)")
args = p.parse_args()
LOCAIS = args.local or cfg.get("local") or ["udp://127.0.0.1:5001"]

# padrao do contrato, secao 4.1
PERFIS_OK = ("Baseline", "Constrained Baseline")
MAX_KBPS = 1500
TAMANHO = (640, 480)
MAX_FPS = 30

av.logging.set_level(av.logging.ERROR)


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def nal_tipos(dados):
    """Tipos das NAL units de um pacote H.264 Annex B (codigos de inicio 00 00 01)."""
    tipos, i, n = [], 0, len(dados)
    while True:
        i = dados.find(b"\x00\x00\x01", i)
        if i < 0 or i + 3 >= n:
            return tipos
        tipos.append(dados[i + 3] & 0x1F)
        i += 3


# --------------------------------------------------------------------------- conferencia do padrao 4.1
class Conferencia:
    """Junta estatisticas a cada 5 s e avisa quando o video foge do padrao."""

    def __init__(self):
        self.reset_janela()
        self.avisos_dados = set()
        self.ultimo_kf = None
        self.gops = []

    def descontinuidade(self):
        self.ultimo_kf = None

    def reset_janela(self):
        self.t0, self.quadros, self.bytes = time.time(), 0, 0
        self.multi_fatia = self.kf_sem_sps = 0

    def avisar(self, chave, msg):
        if chave not in self.avisos_dados:
            self.avisos_dados.add(chave)
            log("AVISO (contrato 4.1): " + msg)

    def stream(self, s):
        cc = s.codec_context
        perfil = cc.profile or "?"
        log("video do robo: %s %s %dx%d, perfil %s" % (s.codec_context.name, s.average_rate or "", cc.width, cc.height, perfil))
        if cc.name != "h264":
            self.avisar("codec", "codec %s; o contrato exige H.264" % cc.name)
        if perfil not in PERFIS_OK:
            self.avisar("perfil", "perfil %s; use baseline ou constrained baseline" % perfil)
        if (cc.width, cc.height) != TAMANHO:
            self.avisar("tam", "resolucao %dx%d; o contrato pede 640x480" % (cc.width, cc.height))
        if getattr(cc, "has_b_frames", 0):
            self.avisar("bframes", "o stream declara B-frames; o contrato proibe")

    def pacote(self, pkt, dados, kf):
        self.quadros += 1
        self.bytes += len(dados)
        tipos = nal_tipos(dados)
        if sum(1 for t in tipos if t in (1, 5)) > 1:
            self.multi_fatia += 1
        if kf:
            if not (7 in tipos and 8 in tipos):
                self.kf_sem_sps += 1
            if pkt.pts is not None and pkt.time_base is not None:
                agora = float(pkt.pts * pkt.time_base)
                if self.ultimo_kf is not None and agora > self.ultimo_kf:
                    self.gops.append(agora - self.ultimo_kf)
                self.ultimo_kf = agora
        dt = time.time() - self.t0
        if dt >= 5:
            fps, kbps = self.quadros / dt, self.bytes * 8 / dt / 1000
            gop = max(self.gops) if self.gops else None
            log("robo -> GS: %.1f fps, %.0f kbps, keyframe a cada %s" % (fps, kbps, "%.2f s" % gop if gop else "?"))
            if kbps > MAX_KBPS:
                self.avisar("kbps", "%.0f kbps; o limite e %d kbps" % (kbps, MAX_KBPS))
            if fps > MAX_FPS + 1:
                self.avisar("fps", "%.1f fps; o limite e %d" % (fps, MAX_FPS))
            if gop and gop > 1.1:
                self.avisar("gop", "keyframe a cada %.2f s; o contrato pede 1 s (GOP = fps)" % gop)
            if self.multi_fatia:
                self.avisar("fatias", "quadros com mais de 1 fatia; o contrato pede 1 fatia por quadro")
            if self.kf_sem_sps:
                self.avisar("sps", "keyframe sem SPS/PPS; repita SPS/PPS em todo keyframe")
            self.gops = []
            self.reset_janela()


# --------------------------------------------------------------------------- WHIP
class TrackH264(MediaStreamTrack):
    """Track de video que entrega pacotes H.264 prontos (o aiortc so empacota em RTP)."""
    kind = "video"

    def __init__(self):
        super().__init__()
        self.fila = asyncio.Queue(maxsize=60)
        self.esperando_kf = True

    def entregar(self, item):
        if self.readyState != "live":
            return
        if self.fila.full():                 # rede lenta: descarta e recomeca no proximo keyframe
            while not self.fila.empty():
                self.fila.get_nowait()
            self.esperando_kf = True
        self.fila.put_nowait(item)

    async def recv(self):
        while True:
            item = await self.fila.get()
            if item is None:
                raise MediaStreamError
            dados, pts, kf = item
            if self.esperando_kf:
                if not kf:
                    continue
                self.esperando_kf = False
            pkt = av.Packet(dados)
            pkt.pts = pts
            pkt.time_base = fractions.Fraction(1, 90000)
            return pkt


class PublicadorWHIP:
    def __init__(self, url, autorizacao):
        self.url, self.autorizacao = url, autorizacao
        self.track = None
        self.recurso = None

    def entregar(self, item):
        if self.track is not None:
            self.track.entregar(item)

    async def rodar(self, parar):
        espera = 1
        while not parar.is_set():
            pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            caiu = asyncio.Event()

            @pc.on("connectionstatechange")
            async def _estado(pc=pc, caiu=caiu):
                log("WHIP: conexao %s" % pc.connectionState)
                if pc.connectionState in ("failed", "closed"):
                    caiu.set()

            try:
                track = TrackH264()
                tr = pc.addTransceiver(track, direction="sendonly")
                codecs = RTCRtpSender.getCapabilities("video").codecs
                tr.setCodecPreferences([c for c in codecs if c.mimeType in ("video/H264", "video/rtx")])
                await pc.setLocalDescription(await pc.createOffer())
                cab = {"Content-Type": "application/sdp"}
                if self.autorizacao:
                    cab["Authorization"] = self.autorizacao
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                    async with s.post(self.url, data=pc.localDescription.sdp, headers=cab) as r:
                        corpo = await r.text()
                        if r.status not in (200, 201):
                            raise RuntimeError("servidor respondeu %d: %s" % (r.status, corpo.strip()[:200]))
                        loc = r.headers.get("Location")
                        self.recurso = aiohttp.client.URL(self.url).join(aiohttp.client.URL(loc)) if loc else None
                await pc.setRemoteDescription(RTCSessionDescription(corpo, "answer"))
                self.track = track
                log("WHIP: publicando em %s" % self.url)
                espera = 1
                fim = asyncio.ensure_future(parar.wait())
                await asyncio.wait([fim, asyncio.ensure_future(caiu.wait())], return_when=asyncio.FIRST_COMPLETED)
                fim.cancel()
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, OSError, ValueError) as e:
                log("WHIP: falhou (%s); tentando de novo em %d s" % (e or type(e).__name__, espera))
            finally:
                self.track = None
                await self.encerrar_recurso()
                await pc.close()
            if not parar.is_set():
                try:
                    await asyncio.wait_for(parar.wait(), timeout=espera)
                except asyncio.TimeoutError:
                    pass
                espera = min(espera * 2, 10)

    async def encerrar_recurso(self):
        if not self.recurso:
            return
        cab = {"Authorization": self.autorizacao} if self.autorizacao else {}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as s:
                await s.delete(str(self.recurso), headers=cab)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            pass
        self.recurso = None


# --------------------------------------------------------------------------- entrada UDP -> saidas
def abrir_saidas(s_in):
    saidas = []
    for url in LOCAIS:
        alvo = url + ("&" if "?" in url else "?") + "pkt_size=1316" if url.startswith("udp://") and "pkt_size" not in url else url
        try:
            out = av.open(alvo, mode="w", format="mpegts")
            s_out = out.add_stream_from_template(s_in)
            saidas.append((url, out, s_out))
        except av.FFmpegError as e:
            log("copia local %s nao abriu: %s" % (url, e))
    return saidas


def fechar(saidas):
    for _, out, _ in saidas:
        try:
            out.close()
        except av.FFmpegError:
            pass


def receber(parar, entregar):
    """Thread: le o video do robo e repassa cada pacote."""
    url = args.entrada
    if url.startswith("udp://") and "?" not in url:
        url += "?fifo_size=1000000&overrun_nonfatal=1"
    opcoes = {"fflags": "nobuffer", "probesize": "500000", "analyzeduration": "1000000"}
    if url.startswith("rtsp://"):
        opcoes["rtsp_transport"] = "tcp"
    aguardando = False
    linha = {"pts": None, "wall": None}              # continua entre reaberturas da entrada
    while not parar.is_set():
        try:
            entrada = av.open(url, format=None if url.startswith("rtsp://") else "mpegts", options=opcoes, timeout=(5, 3))
        except av.FFmpegError:
            if not aguardando:
                log("aguardando video do robo em %s ..." % args.entrada)
                aguardando = True
            continue
        aguardando = False
        if not entrada.streams.video:
            entrada.close()
            continue
        s_in = entrada.streams.video[0]
        conf = Conferencia()
        conf.stream(s_in)
        saidas = abrir_saidas(s_in)
        log("copias locais: %s" % ", ".join(u for u, _, _ in saidas) if saidas else "sem copias locais")
        tb = s_in.time_base
        fps = float(s_in.average_rate or 10)
        dur = max(1, round(1 / (fps * tb)))          # 1 quadro, nas unidades do stream
        ult_in = None
        saidas_no_kf = False                         # copia local comeca num keyframe (decodifica limpo)
        try:
            for pkt in entrada.demux(s_in):
                if parar.is_set():
                    break
                if pkt.size == 0 or pkt.pts is None:
                    continue
                # Linha do tempo propria e sempre crescente: se o Pi reiniciar o encoder ou o
                # relogio pular, o muxer local e o RTP do WHIP nao recebem tempo voltando.
                d = None if ult_in is None else pkt.pts - ult_in
                if d is None or d <= 0 or d * tb > 2:
                    if d is not None:
                        log("salto no tempo do video do robo (%.2f s); emendando" % float(d * tb))
                        conf.descontinuidade()
                    parado = time.time() - linha["wall"] if linha["wall"] else 0
                    d = max(dur, round(parado / tb)) if linha["pts"] is not None else 0
                novo = (linha["pts"] if linha["pts"] is not None else 0) + d
                ult_in = pkt.pts
                linha["pts"], linha["wall"] = novo, time.time()
                pkt.pts = pkt.dts = novo                 # sem B-frames: dts = pts
                dados = bytes(pkt)
                kf = pkt.is_keyframe
                conf.pacote(pkt, dados, kf)
                entregar((dados, int(novo * tb * 90000), kf))
                saidas_no_kf = saidas_no_kf or kf
                for url_s, out, s_out in (saidas if saidas_no_kf else ()):
                    pkt.stream = s_out
                    try:
                        out.mux(pkt)
                    except av.FFmpegError:
                        pass
        except av.FFmpegError:
            log("video do robo parou (3 s sem dados); aguardando de novo")
        finally:
            fechar(saidas)
            entrada.close()


# --------------------------------------------------------------------------- principal
def avisar_dashboard(url_arena):
    if not args.dashboard:
        return
    import urllib.request
    dados = json.dumps({"video": {"vs": url_arena}}).encode()
    req = urllib.request.Request(args.dashboard.rstrip("/") + "/api/estado", dados, {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=1).close()
        log("dashboard: camera da arena em %s" % url_arena)
    except OSError:
        log("dashboard fora do ar (%s); a camera da arena nao foi configurada nele" % args.dashboard)


async def principal():
    loop = asyncio.get_running_loop()
    parar_async = asyncio.Event()
    parar_thread = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, parar_async.set)

    pub = None
    if not args.sem_servidor:
        if not args.whip_url and not (args.servidor and args.id):
            raise SystemExit("faltam --servidor e --id (ou config_local.json); use --sem-servidor para testar so a copia local")
        url = args.whip_url or "http://%s:8889/rover/%s/whip" % (args.servidor, args.id)
        aut = None
        if args.senha:
            par = "%s:%s" % (args.id, args.senha)
            aut = "Bearer " + par if args.auth == "bearer" else "Basic " + base64.b64encode(par.encode()).decode()
        else:
            log("AVISO: sem senha; o servidor deve recusar a publicacao")
        pub = PublicadorWHIP(url, aut)
        if args.servidor:
            avisar_dashboard("http://%s:8889/arena/whep" % args.servidor)

    def entregar(item):
        if pub is not None:
            loop.call_soon_threadsafe(pub.entregar, item)

    t = threading.Thread(target=receber, args=(parar_thread, entregar), daemon=True)
    t.start()
    tarefas = [asyncio.ensure_future(pub.rodar(parar_async))] if pub else []
    await parar_async.wait()
    log("encerrando")
    parar_thread.set()
    if tarefas:
        await asyncio.wait(tarefas, timeout=5)


if __name__ == "__main__":
    asyncio.run(principal())
