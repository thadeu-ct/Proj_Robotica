#!/usr/bin/env python3
"""
Teste headless do detector (YOLO26n treinado) + AprilTag no Raspberry Pi.
Projeto ENG4461 - rover de resgate.

Uso basico (via SSH):
    python3 teste_visao.py                              # NCNN 320, camera 0
    python3 teste_visao.py --model models/nano_LR.pt    # comparar com o .pt
    python3 teste_visao.py --apriltag                   # liga deteccao de tag
    python3 teste_visao.py --stream                     # ver no navegador: http://IP_DO_PI:8080
    python3 teste_visao.py --source foto.jpg            # testar sem camera

Ctrl+C encerra e imprime o resumo (FPS medio, latencias).
"""
import argparse
import csv
import os
import signal
import sys
import threading
import time
from collections import defaultdict, deque

import cv2
import numpy as np

# --------------------------------------------------------------------------- args
p = argparse.ArgumentParser()
p.add_argument("--model", default="models/nano_LR_320_ncnn_model", help=".pt, .onnx ou pasta *_ncnn_model")
p.add_argument("--imgsz", type=int, default=None, help="padrao: tirado do nome do modelo (320/416) ou 320")
p.add_argument("--conf", type=float, default=0.40)
p.add_argument("--source", default="0", help="indice da camera, /dev/videoX, arquivo de video ou imagem")
p.add_argument("--width", type=int, default=640)
p.add_argument("--height", type=int, default=480)
p.add_argument("--frames", type=int, default=0, help="0 = infinito")
p.add_argument("--confirm", type=int, default=3, help="quadros consecutivos para aceitar um alvo")
p.add_argument("--save-every", type=int, default=30, help="salva imagem anotada a cada N quadros (0 desliga)")
p.add_argument("--out", default="out")
p.add_argument("--apriltag", action="store_true", help="detecta tag36h11 no quadro")
p.add_argument("--tag-size", type=float, default=0.100, help="lado da tag em metros (para pose)")
p.add_argument("--calib", default=None, help="npz com K e dist (cv2.calibrateCamera) para pose da tag")
p.add_argument("--stream", action="store_true", help="servidor MJPEG em :8080 com o quadro anotado")
p.add_argument("--port", type=int, default=8080)
p.add_argument("--quiet", action="store_true", help="nao imprime deteccoes por quadro")
args = p.parse_args()

if args.imgsz is None:
    args.imgsz = 416 if "416" in os.path.basename(args.model.rstrip("/")) else 320

os.makedirs(args.out, exist_ok=True)

# --------------------------------------------------------------------------- modelo
from ultralytics import YOLO  # noqa: E402

print(f"[modelo] carregando {args.model} (imgsz={args.imgsz})")
t0 = time.time()
model = YOLO(args.model, task="detect")
NAMES = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
print(f"[modelo] classes: {NAMES}  ({time.time() - t0:.1f}s)")

# pontuacao base de cada formato (regras do jogo)
PONTOS = {"anel_base": 3, "ampulheta": 4, "anel_haste": 5, "cilindro": 6, "peca_chata": 8, "icosaedro": 4}

# --------------------------------------------------------------------------- AprilTag
tag_det = None
K = dist = None
if args.apriltag:
    try:
        from pupil_apriltags import Detector
        tag_det = Detector(families="tag36h11", nthreads=2, quad_decimate=2.0)
        print("[tag] pupil-apriltags ok")
    except ImportError:
        print("[tag] pupil-apriltags nao encontrado; usando cv2.aruco")
        tag_det = "aruco"
    if args.calib:
        c = np.load(args.calib)
        K, dist = c["K"], c["dist"]
        print("[tag] calibracao carregada, pose ativada")


def detect_tags(gray):
    out = []
    if tag_det is None:
        return out
    if tag_det == "aruco":
        d = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11))
        corners, ids, _ = d.detectMarkers(gray)
        if ids is not None:
            for c, i in zip(corners, ids.flatten()):
                out.append({"id": int(i), "corners": c.reshape(4, 2), "center": c.reshape(4, 2).mean(0)})
        return out
    kw = {}
    if K is not None:
        kw = dict(estimate_tag_pose=True, camera_params=(K[0, 0], K[1, 1], K[0, 2], K[1, 2]), tag_size=args.tag_size)
    for r in tag_det.detect(gray, **kw):
        t = {"id": r.tag_id, "corners": r.corners, "center": r.center}
        if K is not None and r.pose_t is not None:
            t["t"] = r.pose_t.flatten()
            R = r.pose_R
            t["yaw_deg"] = float(np.degrees(np.arctan2(R[1, 0], R[0, 0])))
        out.append(t)
    return out


# --------------------------------------------------------------------------- fonte
src = int(args.source) if args.source.isdigit() else args.source
is_image = isinstance(src, str) and src.lower().endswith((".jpg", ".jpeg", ".png", ".bmp"))

if is_image:
    still = cv2.imread(src)
    assert still is not None, f"nao consegui ler {src}"
    cap = None
else:
    cap = cv2.VideoCapture(src, cv2.CAP_V4L2) if isinstance(src, int) else cv2.VideoCapture(src)
    if isinstance(src, int):
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # MJPG evita gargalo USB 2.0
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cap.isOpened():
        sys.exit(f"[cam] nao abriu a fonte {args.source}. Rode: v4l2-ctl --list-devices")
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"[cam] {args.source} aberta em {w}x{h}")


class Grabber(threading.Thread):
    """Le a camera continuamente e guarda so o quadro mais recente.
    Assim a inferencia (lenta no Pi 3) sempre pega um quadro novo, sem fila acumulando latencia."""

    def __init__(self, cap):
        super().__init__(daemon=True)
        self.cap, self.frame, self.ts, self.ok = cap, None, 0.0, True
        self.lock = threading.Lock()

    def run(self):
        while self.ok:
            ok, f = self.cap.read()
            if not ok:
                self.ok = False
                break
            with self.lock:
                self.frame, self.ts = f, time.time()

    def get(self):
        with self.lock:
            return (None, 0.0) if self.frame is None else (self.frame.copy(), self.ts)


grabber = None
if cap is not None and isinstance(src, int):
    grabber = Grabber(cap)
    grabber.start()
    while grabber.get()[0] is None and grabber.ok:
        time.sleep(0.01)

# --------------------------------------------------------------------------- stream MJPEG opcional
latest_jpg = None
if args.stream:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
            self.end_headers()
            try:
                while True:
                    if latest_jpg is not None:
                        self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\n\r\n" + latest_jpg + b"\r\n")
                    time.sleep(0.1)
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = ThreadingHTTPServer(("0.0.0.0", args.port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"[stream] abra http://<IP_DO_PI>:{args.port} no navegador do notebook")

# --------------------------------------------------------------------------- loop
stop = False
signal.signal(signal.SIGINT, lambda *a: globals().__setitem__("stop", True))

log_path = os.path.join(args.out, f"log_{time.strftime('%Y%m%d_%H%M%S')}.csv")
logf = open(log_path, "w", newline="")
log = csv.writer(logf)
log.writerow(["frame", "t", "lat_total_ms", "infer_ms", "n_det", "dets", "tags"])

streak = defaultdict(int)          # classe -> quadros vistos (tolerando falhas curtas)
miss = defaultdict(int)            # classe -> quadros seguidos sem ver
MAX_MISS = 2
nan_count = 0
lat_hist, inf_hist = [], []
fps_win = deque(maxlen=20)
n = 0
t_start = time.time()
warm = 3                           # primeiros quadros sao lentos (aquecimento), fora das estatisticas

print("[run] rodando... Ctrl+C para parar\n")
while not stop:
    if is_image:
        frame, ts = still.copy(), time.time()
    elif grabber is not None:
        if not grabber.ok:
            break
        frame, ts = grabber.get()
    else:
        ok, frame = cap.read()
        if not ok:
            break
        ts = time.time()

    ti = time.time()
    r = model.predict(frame, imgsz=args.imgsz, conf=args.conf, verbose=False)[0]
    infer_ms = (time.time() - ti) * 1000

    # o NCNN no ARM as vezes devolve caixa com NaN; descarta antes de usar ou desenhar
    if len(r.boxes):
        xy = r.boxes.xyxy
        valid = np.isfinite(xy.numpy() if hasattr(xy, "numpy") else np.asarray(xy)).all(axis=1)
        if not valid.all():
            nan_count += int((~valid).sum())
            r = r[np.flatnonzero(valid)]

    dets = []
    seen = set()
    for b in r.boxes:
        cls = NAMES[int(b.cls)]
        x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
        dets.append((cls, float(b.conf), (x1, y1, x2, y2)))
        seen.add(cls)
    # confirmacao tolerante: a ~4 fps o detector pisca, entao so zera depois de MAX_MISS quadros sem ver
    for c in list(streak):
        if c not in seen:
            miss[c] += 1
            if miss[c] > MAX_MISS:
                streak[c] = 0
    for c in seen:
        streak[c] += 1
        miss[c] = 0

    tags = detect_tags(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)) if tag_det is not None else []

    lat_ms = (time.time() - ts) * 1000
    n += 1
    fps_win.append(time.time())
    if n > warm:
        lat_hist.append(lat_ms)
        inf_hist.append(infer_ms)
    fps = (len(fps_win) - 1) / (fps_win[-1] - fps_win[0]) if len(fps_win) > 1 else 0.0

    # ---- saida no terminal
    if not args.quiet:
        ds = "  ".join(
            f"{c}{'*' if streak[c] >= args.confirm else ''}:{p:.2f}@({(bx[0]+bx[2])//2},{bx[3]})" for c, p, bx in dets
        )
        ts_ = "  ".join(
            f"tag{t['id']}@({t['center'][0]:.0f},{t['center'][1]:.0f})"
            + (f" z={t['t'][2]:.2f}m yaw={t['yaw_deg']:.0f}" if "t" in t else "")
            for t in tags
        )
        print(f"#{n:05d} {fps:4.1f}fps inf={infer_ms:5.0f}ms lat={lat_ms:5.0f}ms | {ds or '-'}"
              + (f" | {ts_}" if tags else ""))
    elif n % 20 == 0:
        print(f"#{n:05d} {fps:4.1f} fps  inf={np.mean(inf_hist[-20:] or [0]):.0f} ms")

    log.writerow([n, f"{ts:.3f}", f"{lat_ms:.1f}", f"{infer_ms:.1f}", len(dets),
                  ";".join(f"{c}:{p:.2f}:{b}" for c, p, b in dets),
                  ";".join(str(t["id"]) for t in tags)])

    # ---- imagem anotada (salva e/ou stream)
    need_img = args.stream or (args.save_every and n % args.save_every == 0)
    if need_img:
        img = r.plot()
        for t in tags:
            cv2.polylines(img, [t["corners"].astype(int)], True, (0, 255, 255), 2)
            cv2.putText(img, f"tag {t['id']}", tuple(t["center"].astype(int)), 0, 0.6, (0, 255, 255), 2)
        cv2.putText(img, f"{fps:.1f} fps  inf {infer_ms:.0f} ms", (8, 20), 0, 0.6, (0, 255, 0), 2)
        if args.stream:
            latest_jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()
        if args.save_every and n % args.save_every == 0:
            cv2.imwrite(os.path.join(args.out, f"f{n:05d}.jpg"), img)
            cv2.imwrite(os.path.join(args.out, "ultimo.jpg"), img)

    if args.frames and n >= args.frames:
        break

# --------------------------------------------------------------------------- resumo
if grabber is not None:
    grabber.ok = False
if cap is not None:
    cap.release()
logf.close()
dt = time.time() - t_start
print("\n================ RESUMO ================")
print(f"modelo        : {args.model}  imgsz={args.imgsz}")
print(f"quadros       : {n} em {dt:.1f}s -> {n / dt:.2f} fps medio (inclui aquecimento)")
if inf_hist:
    print(f"inferencia    : media {np.mean(inf_hist):.0f} ms | p50 {np.percentile(inf_hist, 50):.0f} | "
          f"p95 {np.percentile(inf_hist, 95):.0f} ms  -> {1000 / np.mean(inf_hist):.2f} fps so de rede")
    print(f"latencia c->a : media {np.mean(lat_hist):.0f} ms | p95 {np.percentile(lat_hist, 95):.0f} ms "
          f"(meta do relatorio: < 250 ms)")
print(f"caixas NaN    : {nan_count} descartadas")
print(f"log           : {log_path}")
print("========================================")

# ncnn as vezes da crash no destrutor ao sair; resultados ja foram gravados
sys.stdout.flush()
os._exit(0)
