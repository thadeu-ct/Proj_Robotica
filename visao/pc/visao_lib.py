"""
Pecas compartilhadas do lado do computador: leitura do video (Pi, webcam local ou arquivo),
modelo da camera (intrinsecos) e detector de AprilTag com distancia.
"""
import json
import math
import os
import sys
import threading
import time
import urllib.request

import cv2
import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))


# =========================================================================== fontes de video
class _Base(threading.Thread):
    """Guarda so o quadro mais recente. get() devolve (frame, info) ou (None, info)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.frame = None
        self.seq = 0
        self.t_capture = 0.0      # hora da captura no relogio DESTE computador
        self.status = "iniciando"
        self.fps = 0.0
        self.running = True
        self._n = 0
        self._t = time.time()

    def _put(self, frame, t_capture):
        with self.lock:
            self.frame, self.t_capture = frame, t_capture
            self.seq += 1
        self._n += 1
        now = time.time()
        if now - self._t >= 1.0:
            self.fps = self._n / (now - self._t)
            self._n, self._t = 0, now

    def get(self, last_seq=-1):
        with self.lock:
            if self.frame is None or self.seq == last_seq:
                return None, self.seq, self.t_capture
            return self.frame, self.seq, self.t_capture

    def stop(self):
        self.running = False


class StreamReader(_Base):
    """Le o MJPEG do camera_server.py (ou qualquer MJPEG). Reconecta sozinho."""

    def __init__(self, url):
        super().__init__()
        self.url = url
        self.base = url.rsplit("/", 1)[0] if url.endswith("/stream") else url.rstrip("/")
        self.offset = None        # relogio_pi - relogio_local (s)
        self.connected = False

    def _sync_clock(self):
        try:
            t0 = time.time()
            with urllib.request.urlopen(self.base + "/status", timeout=3) as r:
                st = json.loads(r.read().decode())
            t1 = time.time()
            if t1 - t0 < 0.5:
                self.offset = st["time"] - (t0 + t1) / 2
            return st
        except Exception:
            return None

    def run(self):
        while self.running:
            try:
                self.status = f"conectando em {self.url}"
                self._sync_clock()
                resp = urllib.request.urlopen(self.url, timeout=5)
                ctype = resp.headers.get("Content-Type", "")
                boundary = None
                if "boundary=" in ctype:
                    boundary = ctype.split("boundary=", 1)[1].split(";")[0].strip().strip('"')
                    if boundary.startswith("--"):
                        boundary = boundary[2:]
                self.connected = True
                self.status = "recebendo"
                t_sync = time.time()
                if boundary:
                    self._read_multipart(resp, boundary.encode(), t_sync)
                else:
                    self._read_raw(resp)
            except Exception as e:
                self.status = f"sem sinal ({e.__class__.__name__}: {e}); tentando de novo"
            self.connected = False
            time.sleep(1.0)

    def _read_multipart(self, resp, boundary, t_sync):
        marker = b"--" + boundary
        while self.running:
            line = resp.readline()
            if not line:
                raise ConnectionError("stream terminou")
            if not line.strip().startswith(marker):
                continue
            headers = {}
            while True:
                h = resp.readline()
                if not h:
                    raise ConnectionError("stream terminou")
                h = h.strip()
                if not h:
                    break
                if b":" in h:
                    k, v = h.split(b":", 1)
                    headers[k.strip().lower()] = v.strip()
            if b"content-length" in headers:
                data = resp.read(int(headers[b"content-length"]))
            else:                                   # MJPEG sem tamanho: le ate o fim do JPEG
                data = b""
                while not data.endswith(b"\xff\xd9"):
                    chunk = resp.readline()
                    if not chunk:
                        raise ConnectionError("stream terminou")
                    data += chunk
                    if len(data) > 8_000_000:
                        raise ValueError("quadro grande demais")
                data = data.rstrip(b"\r\n")
            frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue
            t_local = time.time()
            if b"x-timestamp" in headers and self.offset is not None:
                t_local = float(headers[b"x-timestamp"]) - self.offset
            self._put(frame, t_local)
            if time.time() - t_sync > 30:           # ressincroniza o relogio de vez em quando
                threading.Thread(target=self._sync_clock, daemon=True).start()
                t_sync = time.time()

    def _read_raw(self, resp):
        buf = b""
        while self.running:
            chunk = resp.read(4096)
            if not chunk:
                raise ConnectionError("stream terminou")
            buf += chunk
            a = buf.find(b"\xff\xd8")
            b = buf.find(b"\xff\xd9", a + 2)
            if a != -1 and b != -1:
                frame = cv2.imdecode(np.frombuffer(buf[a:b + 2], np.uint8), cv2.IMREAD_COLOR)
                buf = buf[b + 2:]
                if frame is not None:
                    self._put(frame, time.time())
            if len(buf) > 8_000_000:
                buf = b""


class LocalReader(_Base):
    """Webcam deste computador, arquivo de video (repete) ou imagem."""

    def __init__(self, source, width=640, height=480):
        super().__init__()
        self.source = source
        self.still = None
        self.cap = None
        if isinstance(source, str) and source.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")):
            self.still = cv2.imread(source)
            if self.still is None:
                raise SystemExit(f"nao consegui ler a imagem {source}")
        else:
            self.cap = cv2.VideoCapture(source)
            if isinstance(source, int):
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            if not self.cap.isOpened():
                raise SystemExit(f"nao consegui abrir a fonte {source}")
        self.connected = True
        self.status = f"local: {source}"

    def run(self):
        is_file = isinstance(self.source, str)
        while self.running:
            if self.still is not None:
                self._put(self.still.copy(), time.time())
                time.sleep(1 / 15)
                continue
            ok, f = self.cap.read()
            if not ok:
                if is_file:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                self.status = "falha lendo a webcam"
                time.sleep(0.2)
                continue
            self._put(f, time.time())
            if is_file:
                time.sleep(1 / 30)


class UdpReader(_Base):
    """H.264/MPEG-TS por UDP (copia local do relay da ground station, ex.: udp://127.0.0.1:5001).

    LATENCIA (medida em 11/11/2026, stream local H.264 baseline a 10 fps, atraso so do leitor):
        OpenCV VideoCapture padrao ............ 1301 ms  (decodificador com 1 thread de QUADRO por nucleo:
                                                          cada thread segura ~1 quadro; 12 nucleos = 1,3 s)
        PyAV/FFmpeg demux, 1 thread ............ 201 ms  (o demux MPEG-TS so fecha um quadro quando o proximo
                                                          comeca, e o separador de quadros H.264 segura mais um)
        + PES com tamanho (Pi) ................. 101 ms
        leitor TS proprio (este) + PES c/ tamanho  1 ms  <- padrao
    O leitor proprio le os datagramas, junta os pacotes TS de 188 bytes no PES de video e entrega o quadro ao
    decodificador (PyAV CodecContext, 1 thread) no instante em que o PES fica completo. Para isso o transmissor
    precisa gravar o tamanho do PES (ffmpeg -omit_video_pes_length 0, ja ligado no camera_server.py). Se vier
    sem tamanho, fecha o quadro quando o proximo comeca (~1 quadro de atraso), como o FFmpeg.
    motor="ffmpeg" usa o demux do PyAV (mais tolerante a TS estranho); sem PyAV cai para o OpenCV com 1 thread.
    Sem relogio do Pi: o atraso mostrado conta so a partir da chegada aqui."""

    OPCOES = {"fflags": "nobuffer", "flags": "low_delay", "probesize": "32768", "analyzeduration": "0"}

    def __init__(self, url, motor="ts"):
        super().__init__()
        self.url = url
        self.connected = False
        try:
            import av  # noqa: F401
            self.motor = motor
        except ImportError:
            self.motor = "opencv"
        self.quadros_corrompidos = 0

    def run(self):
        while self.running:
            self.status = f"esperando video H.264 em {self.url} (leitor {self.motor})"
            try:
                {"ts": self._ler_ts, "ffmpeg": self._ler_pyav}.get(self.motor, self._ler_opencv)()
            except Exception as e:                        # timeout, porta, stream cortado: reabre
                self.status = f"H.264 parou ({e.__class__.__name__}: {e}); reabrindo"
            self.connected = False
            time.sleep(0.5)

    # ------------------------------------------------------------------ leitor TS proprio (padrao)
    def _ler_ts(self):
        import socket
        import struct
        import av

        resto = self.url.split("://", 1)[-1].split("?")[0]
        host, porta = resto.rsplit(":", 1)
        host = host.strip("@") or "0.0.0.0"
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sk.bind((host, int(porta)))
        sk.settimeout(3.0)                               # 3 s sem dados -> reabre
        dec = av.CodecContext.create("h264", "r")
        dec.thread_count = 1                             # sem threads de quadro (ver tabela acima)
        dec.thread_type = "NONE"
        pes, total, pid_v, cc_ant = bytearray(), None, None, None

        def entregar(buf):
            if len(buf) < 9 or buf[6] & 0xC0 != 0x80:     # cabecalho PES invalido
                return
            try:
                quadros = dec.decode(av.Packet(bytes(buf[9 + buf[8]:])))
            except av.error.InvalidDataError:            # antes do 1o keyframe, ou dados perdidos no Wi-Fi
                self.quadros_corrompidos += 1
                return
            for fr in quadros:
                self._put(fr.to_ndarray(format="bgr24"), time.time())

        self.connected = True
        self.status = f"recebendo H.264 (leitor TS proprio) em {host}:{porta}"
        try:
            while self.running:
                dados, _ = sk.recvfrom(65536)
                for o in range(0, len(dados) - 187, 188):
                    p = dados[o:o + 188]
                    if p[0] != 0x47:
                        continue
                    pid = ((p[1] & 0x1F) << 8) | p[2]
                    pusi, afc, cc = p[1] & 0x40, (p[3] >> 4) & 3, p[3] & 0x0F
                    k = 4 + (1 + p[4] if afc & 2 else 0)
                    if not afc & 1 or k >= 188:           # pacote so com adaptation field (PCR, enchimento)
                        continue
                    carga = p[k:]
                    if pusi and carga[:3] == b"\x00\x00\x01" and 0xE0 <= carga[3] <= 0xEF:
                        if pes and total is None:        # PES anterior sem tamanho: fecha agora
                            entregar(pes)
                        pid_v, cc_ant = pid, cc
                        pes = bytearray(carga)
                        n = struct.unpack(">H", bytes(carga[4:6]))[0]
                        total = n + 6 if n else None
                    elif pid == pid_v and pes:
                        if cc_ant is not None and cc != (cc_ant + 1) & 0x0F:   # pacote TS perdido no meio do quadro
                            self.quadros_corrompidos += 1
                            pes, total = bytearray(), None
                            cc_ant = cc
                            continue
                        cc_ant = cc
                        pes += carga
                    else:
                        continue
                    if total is not None and len(pes) >= total:   # PES completo: decodifica ja
                        entregar(pes[:total])
                        pes, total = bytearray(), None
        finally:
            sk.close()

    # ------------------------------------------------------------------ alternativas
    def _ler_pyav(self):
        import av
        c = av.open(self.url, options=self.OPCOES, timeout=(10.0, 3.0))   # (abrir, sem dados) em s
        try:
            s = c.streams.video[0]
            s.codec_context.thread_count = 1
            s.codec_context.thread_type = "NONE"
            self.connected = True
            self.status = "recebendo H.264 (PyAV/FFmpeg, 1 thread)"
            for pkt in c.demux(s):
                if not self.running:
                    break
                try:
                    quadros = pkt.decode()
                except av.error.InvalidDataError:
                    self.quadros_corrompidos += 1
                    continue
                for fr in quadros:
                    self._put(fr.to_ndarray(format="bgr24"), time.time())
        finally:
            c.close()

    def _ler_opencv(self):
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "fflags;nobuffer|flags;low_delay")
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 1])
        if not cap.isOpened():
            raise ConnectionError("nao abriu")
        self.connected = True
        self.status = "recebendo H.264 (OpenCV, 1 thread)"
        falhas = 0
        try:
            while self.running:
                ok, f = cap.read()
                if not ok or f is None:
                    falhas += 1
                    if falhas > 30:
                        raise ConnectionError("sem quadros")
                    time.sleep(0.02)
                    continue
                falhas = 0
                self._put(f, time.time())
        finally:
            cap.release()


def abrir_fonte(args):
    """--udp / --url / --pi / --cam / --arquivo -> leitor ja iniciado."""
    if getattr(args, "udp", None):
        r = UdpReader(args.udp, getattr(args, "leitor_udp", "ts"))
    elif getattr(args, "arquivo", None):
        r = LocalReader(args.arquivo)
    elif getattr(args, "cam", None) is not None:
        r = LocalReader(int(args.cam))
    else:
        url = args.url or f"http://{args.pi}:{args.porta}/stream"
        r = StreamReader(url)
    r.start()
    return r


# =========================================================================== camera
class Camera:
    """Intrinsecos (K, dist) carregados de JSON. Ajusta K se a resolucao do video for outra."""

    def __init__(self, path):
        with open(path) as f:
            c = json.load(f)
        self.path = path
        self.K0 = np.array(c["K"], dtype=np.float64)
        self.dist = np.array(c.get("dist", [0, 0, 0, 0, 0]), dtype=np.float64).reshape(-1, 1)
        self.size0 = tuple(c["size"])            # (w, h) da calibracao
        self.desc = c.get("descricao", os.path.basename(path))
        self._size = None
        self.K = self.K0.copy()
        self._maps = None
        self.has_dist = bool(np.any(np.abs(self.dist) > 1e-9))

    def for_size(self, w, h):
        if self._size != (w, h):
            sx, sy = w / self.size0[0], h / self.size0[1]
            K = self.K0.copy()
            K[0, 0] *= sx; K[0, 2] *= sx
            K[1, 1] *= sy; K[1, 2] *= sy
            self.K, self._size, self._maps = K, (w, h), None
        return self.K

    def undistort(self, frame):
        if not self.has_dist:
            return frame
        h, w = frame.shape[:2]
        K = self.for_size(w, h)
        if self._maps is None:
            self._maps = cv2.initUndistortRectifyMap(K, self.dist, None, K, (w, h), cv2.CV_16SC2)
        return cv2.remap(frame, self._maps[0], self._maps[1], cv2.INTER_LINEAR)

    @property
    def hfov(self):
        return 2 * math.degrees(math.atan(self.K0[0, 2] / self.K0[0, 0])) if self.K0[0, 0] else 0


def escolher_camera(path=None):
    if path:
        return Camera(path)
    for nome in ("camera_pi.json", "camera_lab.json"):
        p = os.path.join(AQUI, "..", "pi", nome)
        if os.path.exists(p):
            return Camera(os.path.normpath(p))
    raise SystemExit("nenhum arquivo de camera (camera_pi.json / camera_lab.json) em visao/pi/")


# =========================================================================== AprilTag
# Uma implementacao so: a mesma que roda no Pi (visao/pi/apriltag_pi.py).
sys.path.insert(0, os.path.join(AQUI, "..", "pi"))
from apriltag_pi import FAMILIAS, TagDetector, carregar_escala, salvar_escala, ESCALA_PATH  # noqa: E402,F401


class PiTags(threading.Thread):
    """Busca em /tags do Pi o resultado da AprilTag calculada LA (long-poll: chega assim que sai)."""

    def __init__(self, base):
        super().__init__(daemon=True)
        self.base = base.rstrip("/")
        self.snap = None
        self.t_recv = 0.0
        self.status = "aguardando o Pi"
        self.running = True

    def run(self):
        seq = 0
        while self.running:
            try:
                with urllib.request.urlopen(f"{self.base}/tags?depois={seq}", timeout=3) as r:
                    snap = json.loads(r.read().decode())
                seq = snap.get("seq", 0)
                self.snap, self.t_recv = snap, time.time()
                self.status = "ok" if not snap.get("erro") else f"erro no Pi: {snap['erro']}"
            except Exception as e:
                self.status = f"sem /tags do Pi ({e.__class__.__name__}: {e})"
                time.sleep(1.0)

    def tags(self):
        """Tags no mesmo formato do TagDetector (para desenhar)."""
        snap = self.snap
        if not snap or time.time() - self.t_recv > 2.0:
            return [], snap
        out = []
        for t in snap.get("tags", []):
            d = {"id": t["id"], "familia": t["familia"], "corners": np.array(t["corners"], dtype=np.float64),
                 "center": np.array(t["center"], dtype=np.float64)}
            if "dist_cm" in t:
                d.update(dist_m=t["dist_cm"] / 100, dist_raw_m=t["dist_raw_cm"] / 100, angulo_deg=t["angulo_deg"],
                         x_m=t["x_cm"] / 100, z_m=t["z_cm"] / 100,
                         rvec=np.array(t["rvec"], dtype=np.float64).reshape(3, 1),
                         tvec=np.array(t["tvec"], dtype=np.float64).reshape(3, 1))
            out.append(d)
        return out, snap

    def _get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=3) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            return {"ok": False, "msg": f"falha falando com o Pi: {e}"}

    def calibrar(self, ref_cm):
        return self._get(f"/calibrar?ref={ref_cm}")

    def zerar(self):
        return self._get("/zerar")

    def stop(self):
        self.running = False
