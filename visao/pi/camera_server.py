#!/usr/bin/env python3
"""
Servidor de camera do Raspberry Pi. So captura e transmite: nenhum modelo roda aqui.
O processamento (YOLO + AprilTag) roda no computador com visao/pc/detector_pc.py.

Uso no Pi:
    python camera_server.py                 # webcam USB, 640x480, porta 8080
    python camera_server.py --device 1      # outra camera
    python camera_server.py --source video.mp4   # testar sem camera (repete o arquivo)

Endpoints:
    /           pagina com o video (para conferir no navegador)
    /stream     MJPEG (cada quadro leva X-Timestamp e X-Seq)
    /frame.jpg  ultimo quadro
    /status     JSON com fps, resolucao e hora do Pi

Depende so de OpenCV, NumPy e da biblioteca padrao (Python 3.8+).
"""
import argparse
import json
import os
import signal
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

p = argparse.ArgumentParser(description="Transmite a camera do Pi em MJPEG")
p.add_argument("--device", default="auto", help="indice da camera, /dev/videoX ou 'auto' (tenta 0..4)")
p.add_argument("--source", default=None, help="arquivo de video/imagem no lugar da camera (teste)")
p.add_argument("--width", type=int, default=640)
p.add_argument("--height", type=int, default=480)
p.add_argument("--fps", type=float, default=30, help="fps pedido a camera")
p.add_argument("--max-fps", type=float, default=20, help="limite de quadros enviados por segundo")
p.add_argument("--quality", type=int, default=80, help="qualidade JPEG (50-95)")
p.add_argument("--port", type=int, default=8080)
p.add_argument("--bind", default="0.0.0.0")
args = p.parse_args()

IS_LINUX = sys.platform.startswith("linux")


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# --------------------------------------------------------------------------- camera
def open_camera(dev):
    if IS_LINUX and (isinstance(dev, int) or str(dev).startswith("/dev/")):
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
    else:
        cap = cv2.VideoCapture(dev)
    if not cap.isOpened():
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # MJPG evita gargalo do USB 2.0
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, args.fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    ok, frame = cap.read()
    if not ok or frame is None:
        cap.release()
        return None
    return cap


def find_camera():
    if args.device != "auto":
        dev = int(args.device) if str(args.device).isdigit() else args.device
        cap = open_camera(dev)
        return (cap, dev) if cap else (None, dev)
    for dev in range(5):
        cap = open_camera(dev)
        if cap:
            return cap, dev
    return None, "auto"


# --------------------------------------------------------------------------- estado compartilhado
class Latest:
    """Guarda so o quadro mais recente ja em JPEG. Clientes lentos pulam quadros em vez de acumular atraso."""

    def __init__(self):
        self.cond = threading.Condition()
        self.jpg, self.seq, self.ts = None, 0, 0.0
        self.w = self.h = 0
        self.fps = 0.0
        self.clients = 0
        self.cam_ok = False
        self.cam_name = ""

    def put(self, jpg, ts, w, h):
        with self.cond:
            self.jpg, self.ts, self.w, self.h = jpg, ts, w, h
            self.seq += 1
            self.cond.notify_all()

    def wait_new(self, last_seq, timeout=2.0):
        with self.cond:
            if self.seq == last_seq:
                self.cond.wait(timeout)
            return self.jpg, self.seq, self.ts


latest = Latest()
running = True


def capture_loop():
    params = [int(cv2.IMWRITE_JPEG_QUALITY), int(args.quality)]
    min_dt = 1.0 / args.max_fps if args.max_fps > 0 else 0.0
    still = None
    cap = None
    if args.source:
        if args.source.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")):
            still = cv2.imread(args.source)
            if still is None:
                log(f"[erro] nao consegui ler {args.source}")
                os._exit(1)
            latest.cam_name = args.source
        else:
            cap = cv2.VideoCapture(args.source)
            if not cap.isOpened():
                log(f"[erro] nao consegui abrir {args.source}")
                os._exit(1)
            latest.cam_name = args.source
        latest.cam_ok = True
        log(f"[fonte] {args.source}")

    t_last_send = 0.0
    n, t_fps = 0, time.time()
    warned = False
    while running:
        # --- (re)abre a camera se precisar
        if still is None and cap is None:
            cap, dev = find_camera()
            if cap is None:
                latest.cam_ok = False
                if not warned:
                    log(f"[cam] nenhuma camera abriu (device={dev}). Confira: ls /dev/video* ; v4l2-ctl --list-devices")
                    log("[cam] se outro programa estiver usando a camera (ex.: teste_visao.py), feche ele. Tentando de novo a cada 2 s...")
                    warned = True
                time.sleep(2)
                continue
            warned = False
            latest.cam_ok = True
            latest.cam_name = str(dev)
            w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            log(f"[cam] camera {dev} aberta em {w}x{h}")

        # --- le um quadro
        if still is not None:
            frame = still.copy()
            ok = True
            time.sleep(max(min_dt, 0.03))
        else:
            ok, frame = cap.read()
            if not ok and args.source:          # video de teste: volta para o inicio
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
        if not ok or frame is None:
            log("[cam] falha de leitura; reabrindo a camera")
            cap.release()
            cap = None
            latest.cam_ok = False
            time.sleep(0.5)
            continue
        ts = time.time()

        # --- limita o envio (a camera continua sendo lida para nao acumular buffer)
        if ts - t_last_send < min_dt:
            continue
        t_last_send = ts
        ok, buf = cv2.imencode(".jpg", frame, params)
        if not ok:
            continue
        latest.put(buf.tobytes(), ts, frame.shape[1], frame.shape[0])

        n += 1
        if ts - t_fps >= 2.0:
            latest.fps = n / (ts - t_fps)
            n, t_fps = 0, ts
    if cap is not None:
        cap.release()


# --------------------------------------------------------------------------- HTTP
PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><title>Camera do Pi</title>
<style>body{background:#111;color:#ddd;font-family:sans-serif;text-align:center}img{max-width:100%}</style>
</head><body><p>Camera do Pi (sem processamento). Status em <a href="/status" style="color:#8cf">/status</a></p>
<img src="/stream"></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            return self._send(200, "text/html; charset=utf-8", PAGE)
        if path == "/status":
            body = json.dumps({
                "ok": latest.cam_ok, "camera": latest.cam_name, "width": latest.w, "height": latest.h,
                "fps": round(latest.fps, 1), "seq": latest.seq, "clients": latest.clients,
                "time": time.time(), "host": socket.gethostname(),
            }).encode()
            return self._send(200, "application/json", body)
        if path == "/frame.jpg":
            jpg, _, ts = latest.wait_new(-1, 0)
            if jpg is None:
                return self._send(503, "text/plain", b"sem quadro ainda")
            return self._send(200, "image/jpeg", jpg)
        if path == "/stream":
            return self._stream()
        return self._send(404, "text/plain", b"nao encontrado")

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        latest.clients += 1
        peer = self.client_address[0]
        log(f"[stream] cliente conectado: {peer}")
        last = -1
        try:
            while running:
                jpg, seq, ts = latest.wait_new(last)
                if jpg is None or seq == last:
                    continue
                last = seq
                head = (
                    "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\nX-Timestamp: %.6f\r\nX-Seq: %d\r\n\r\n"
                    % (len(jpg), ts, seq)
                ).encode()
                self.wfile.write(head + jpg + b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
            pass
        finally:
            latest.clients -= 1
            log(f"[stream] cliente saiu: {peer}")


def main():
    global running
    t = threading.Thread(target=capture_loop, daemon=True)
    t.start()
    srv = None
    for tentativa in range(20):              # um servidor antigo pode estar terminando de fechar
        try:
            srv = ThreadingHTTPServer((args.bind, args.port), Handler)
            break
        except OSError as e:
            if tentativa == 0:
                log(f"[aviso] porta {args.port} ocupada ({e}); tentando de novo por 10 s")
            time.sleep(0.5)
    if srv is None:
        log(f"[erro] porta {args.port} continua ocupada. Outro servidor rodando? Use: pkill -f camera_server.py")
        os._exit(1)
    srv.daemon_threads = True

    def stop(*_):
        global running
        running = False
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    log(f"[servidor] porta {args.port}. No computador: http://{ips[0] if ips else '<IP_DO_PI>'}:{args.port}  (Ctrl+C encerra)")
    srv.serve_forever()
    log("[servidor] encerrado")


if __name__ == "__main__":
    main()
