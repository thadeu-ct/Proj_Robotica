"""
Pecas compartilhadas do lado do computador: leitura do video (Pi, webcam local ou arquivo),
modelo da camera (intrinsecos) e detector de AprilTag com distancia.
"""
import json
import math
import os
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


def abrir_fonte(args):
    """--url / --pi / --cam / --arquivo -> leitor ja iniciado."""
    if getattr(args, "arquivo", None):
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
        p = os.path.join(AQUI, nome)
        if os.path.exists(p):
            return Camera(p)
    raise SystemExit("nenhum arquivo de camera (camera_pi.json / camera_lab.json) em visao/pc/")


# =========================================================================== AprilTag
FAMILIAS = {
    "25h9": cv2.aruco.DICT_APRILTAG_25h9,
    "36h11": cv2.aruco.DICT_APRILTAG_36h11,
    "16h5": cv2.aruco.DICT_APRILTAG_16h5,
    "36h10": cv2.aruco.DICT_APRILTAG_36h10,
}


class TagDetector:
    """
    Detecta AprilTags com o modulo aruco do OpenCV (nas fotos do lab ele achou a tag25h9
    onde o pupil-apriltags falhou) e estima a pose com solvePnP (IPPE_SQUARE).
    Distancia = |t| (do centro optico da camera ao centro da tag), vezes a escala de ajuste.
    """

    def __init__(self, familias=("25h9", "36h11"), tag_mm=100.0, ids=None):
        self.tag_mm = float(tag_mm)
        self.ids = set(ids) if ids else None
        self.dets = []
        for f in familias:
            f = f.lower().replace("tag", "")
            if f not in FAMILIAS:
                raise SystemExit(f"familia desconhecida: {f} (use {', '.join(FAMILIAS)})")
            p = cv2.aruco.DetectorParameters()
            p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
            self.dets.append((f, cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(FAMILIAS[f]), p)))

    def detect(self, gray, K, escala=1.0):
        s = self.tag_mm / 1000.0
        obj = np.array([[-s / 2, s / 2, 0], [s / 2, s / 2, 0], [s / 2, -s / 2, 0], [-s / 2, -s / 2, 0]], dtype=np.float64)
        out = []
        for fam, det in self.dets:
            corners, ids, _ = det.detectMarkers(gray)
            if ids is None:
                continue
            for c, i in zip(corners, ids.ravel()):
                i = int(i)
                if self.ids and i not in self.ids:
                    continue
                pts = c.reshape(4, 2).astype(np.float64)
                tag = {"familia": fam, "id": i, "corners": pts, "center": pts.mean(0)}
                ok, rvec, tvec = cv2.solvePnP(obj, pts, K, None, flags=cv2.SOLVEPNP_IPPE_SQUARE)
                if ok:
                    t = tvec.ravel()
                    raw = float(np.linalg.norm(t))
                    tag.update(
                        rvec=rvec, tvec=tvec,
                        dist_raw_m=raw,
                        dist_m=raw * escala,
                        x_m=float(t[0]) * escala, y_m=float(t[1]) * escala, z_m=float(t[2]) * escala,
                        angulo_deg=float(math.degrees(math.atan2(t[0], t[2]))),   # + = tag a direita
                        lado_px=float(np.mean([np.linalg.norm(pts[k] - pts[(k + 1) % 4]) for k in range(4)])),
                    )
                out.append(tag)
        return out


# =========================================================================== ajuste de escala da distancia
ESCALA_PATH = os.path.join(AQUI, "ajuste_distancia.json")


def carregar_escala():
    try:
        with open(ESCALA_PATH) as f:
            return float(json.load(f)["escala"])
    except Exception:
        return 1.0


def salvar_escala(escala, info):
    with open(ESCALA_PATH, "w") as f:
        json.dump({"escala": escala, **info, "quando": time.strftime("%Y-%m-%d %H:%M:%S")}, f, indent=2)
