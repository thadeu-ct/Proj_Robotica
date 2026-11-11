#!/usr/bin/env python3
"""
Servidor de camera do Raspberry Pi.
  - transmite o video (MJPEG) para o computador, que roda o YOLO (visao/pc/detector_pc.py)
  - roda a AprilTag AQUI no Pi (apriltag_pi.py) e publica distancia/angulo em /tags,
    para o controle autonomo nao depender do computador

Uso no Pi:
    python camera_server.py                 # webcam USB, 640x480, porta 8080
    python camera_server.py --device 1      # outra camera
    python camera_server.py --source video.mp4   # testar sem camera (repete o arquivo)

Endpoints:
    /           pagina com o video (para conferir no navegador)
    /stream     MJPEG (cada quadro leva X-Timestamp e X-Seq)
    /frame.jpg  ultimo quadro
    /status     JSON com fps, resolucao e hora do Pi
    /tags       JSON com as tags do ultimo quadro processado (?depois=N espera um resultado mais novo que N)
    /calibrar?ref=50   ajusta a escala da distancia com UMA tag a 50 cm
    /zerar      zera o ajuste

H.264 para a ground station (contrato da competicao, secao 4.1):
    python camera_server.py --h264 udp://IP_DO_MAC:5000
    -> ffmpeg h264_v4l2m2m (hardware do Pi), Constrained Baseline, sem B-frames, GOP = fps (1 keyframe/s)
       com SPS/PPS repetidos, 640x480, taxa FIXA de --h264-fps (repete o ultimo quadro se a camera atrasar),
       --h264-kbps (padrao 900; o v4l2m2m nao respeita -maxrate), MPEG-TS por UDP.
    Com --h264 o JPEG do MJPEG so e gerado quando ha cliente no /stream (economiza CPU e Wi-Fi).

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
p.add_argument("--quality", type=int, default=65, help="qualidade JPEG (50-95); 65 cabe no Wi-Fi do Pi 3")
p.add_argument("--sndbuf-kb", type=int, default=32, help="buffer de envio por cliente: pequeno = descarta quadros velhos em vez de acumular atraso")
p.add_argument("--port", type=int, default=8080)
p.add_argument("--bind", default="0.0.0.0")
p.add_argument("--sem-tags", action="store_true", help="nao roda a AprilTag no Pi")
p.add_argument("--tag-familias", default="25h9", help="a tag do lab e 25h9; use 25h9,36h11 para as duas")
p.add_argument("--tag-mm", type=float, default=42.0, help="lado da tag em mm (borda preta, sem margem branca); a do lab tem 42")
p.add_argument("--tag-ids", default=None, help="so considerar esses IDs (ex.: 5)")
p.add_argument("--camera", default=None, help="JSON de calibracao (padrao: camera_pi.json, senao camera_lab.json)")
p.add_argument("--log-tags", type=float, default=1.0, help="imprime as tags no terminal a cada N s (0 desliga)")
p.add_argument("--h264", default=None, help="envia H.264 em MPEG-TS por UDP para esse destino (ex.: udp://192.168.1.217:5000)")
p.add_argument("--h264-fps", type=int, default=10, help="taxa FIXA do H.264; o GOP e igual a ela (1 keyframe por segundo)")
p.add_argument("--h264-kbps", type=int, default=900, help="bitrate medio do H.264 (contrato: no maximo 1500; 900 deixa folga para os picos)")
p.add_argument("--sem-mjpeg", action="store_true", help="nao gera JPEG nem para /frame.jpg (so H.264 + tags)")
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
        self.frame = None

    def put(self, jpg, ts, w, h, frame=None):
        with self.cond:
            self.jpg, self.ts, self.w, self.h = jpg, ts, w, h
            self.frame = frame
            self.seq += 1
            self.cond.notify_all()

    def wait_new(self, last_seq, timeout=2.0):
        with self.cond:
            if self.seq == last_seq:
                self.cond.wait(timeout)
            return self.jpg, self.seq, self.ts

    def wait_frame(self, last_seq, timeout=1.0):
        with self.cond:
            if self.seq == last_seq:
                self.cond.wait(timeout)
            return self.frame, self.seq, self.ts


latest = Latest()
running = True


# --------------------------------------------------------------------------- AprilTag no Pi
class TagState:
    """Resultado mais recente da AprilTag. O controle dos motores vai ler daqui (ou usar TagRunner direto)."""

    def __init__(self):
        self.cond = threading.Condition()
        self.res_seq = 0          # contador de resultados
        self.frame_seq = 0        # quadro de origem
        self.ts_frame = 0.0
        self.ts_done = 0.0
        self.tags = []            # objetos completos (com numpy)
        self.json_tags = []
        self.proc_ms = 0.0
        self.fps = 0.0
        self.erro = ""

    def put(self, frame_seq, ts_frame, tags, json_tags, proc_ms):
        with self.cond:
            self.frame_seq, self.ts_frame, self.tags, self.json_tags, self.proc_ms = frame_seq, ts_frame, tags, json_tags, proc_ms
            self.ts_done = time.time()
            self.res_seq += 1
            self.cond.notify_all()

    def snapshot(self, depois=None, timeout=1.0):
        with self.cond:
            if depois is not None and self.res_seq <= depois:
                self.cond.wait(timeout)
            return {
                "seq": self.res_seq, "frame_seq": self.frame_seq, "ts_frame": self.ts_frame, "ts_done": self.ts_done,
                "proc_ms": round(self.proc_ms, 1), "fps": round(self.fps, 1), "tags": self.json_tags,
                "tag_mm": runner.det.tag_mm if runner else None, "escala": round(runner.escala, 4) if runner else None,
                "erro": self.erro, "time": time.time(),
            }


tagstate = TagState()
runner = None
if not args.sem_tags:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import apriltag_pi  # noqa: E402

    _ids = [int(x) for x in args.tag_ids.split(",")] if args.tag_ids else None
    runner = apriltag_pi.TagRunner(args.tag_familias.split(","), args.tag_mm, _ids, args.camera)


def tag_loop():
    last = -1
    n, t_fps = 0, time.time()
    t_log = 0.0
    while running:
        frame, seq, ts = latest.wait_frame(last)
        if frame is None or seq == last:
            continue
        last = seq
        t0 = time.perf_counter()
        try:
            tags = runner.process(frame)
            tagstate.erro = ""
        except Exception as e:                      # nunca derruba o servidor por causa da tag
            tags = []
            tagstate.erro = f"{e.__class__.__name__}: {e}"
        ms = (time.perf_counter() - t0) * 1000
        tagstate.put(seq, ts, tags, [apriltag_pi.tag_para_json(t) for t in tags], ms)
        n += 1
        now = time.time()
        if now - t_fps >= 2.0:
            tagstate.fps = n / (now - t_fps)
            n, t_fps = 0, now
        if args.log_tags and now - t_log >= args.log_tags:
            txt = "  |  ".join(f"ID {t['id']} {t['dist_m'] * 100:5.1f} cm {t['angulo_deg']:+4.0f} graus"
                               for t in tags if "dist_m" in t) or "nenhuma tag"
            log(f"[tag] {tagstate.fps:4.1f} fps {ms:3.0f} ms | {txt}" + (f" | ERRO {tagstate.erro}" if tagstate.erro else ""))
            t_log = now


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
    t_ultimo_jpg = 0.0
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
        jpg = None
        precisa_jpg = not args.sem_mjpeg and (not args.h264 or latest.clients > 0 or ts - t_ultimo_jpg > 1.0)
        if precisa_jpg:
            ok, buf = cv2.imencode(".jpg", frame, params)
            if ok:
                jpg = buf.tobytes()
                t_ultimo_jpg = ts
        if jpg is None:
            jpg = latest.jpg                       # mantem o ultimo JPEG para /frame.jpg
        latest.put(jpg, ts, frame.shape[1], frame.shape[0], frame)

        n += 1
        if ts - t_fps >= 2.0:
            latest.fps = n / (ts - t_fps)
            n, t_fps = 0, ts
    if cap is not None:
        cap.release()


# --------------------------------------------------------------------------- H.264 (ffmpeg, encoder de hardware)
class H264Sender(threading.Thread):
    """Alimenta um ffmpeg (h264_v4l2m2m) com YUV420 a taxa FIXA e manda MPEG-TS por UDP.
    Taxa fixa = timestamps regulares e GOP de exatamente 1 s, mesmo com a webcam oscilando (~11 fps)."""

    def __init__(self, url, fps, kbps):
        super().__init__(daemon=True)
        self.url = url if "pkt_size" in url else url + ("&" if "?" in url else "?") + "pkt_size=1316"
        self.fps, self.kbps = int(fps), int(kbps)
        self.proc = None
        self.enviados = 0
        self.repetidos = 0
        self.status = "iniciando"

    def _cmd(self, w, h):
        return ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostats",
                "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{w}x{h}", "-framerate", str(self.fps), "-i", "pipe:0",
                "-c:v", "h264_v4l2m2m", "-b:v", f"{self.kbps}k", "-g", str(self.fps), "-bf", "0",
                "-profile:v", "578",                      # Constrained Baseline (o encoder nao aceita o nome)
                "-num_capture_buffers", "16", "-bsf:v", "dump_extra",   # SPS/PPS em todo keyframe
                "-flush_packets", "1", "-muxdelay", "0", "-muxpreload", "0",
                # grava o tamanho de cada PES: o receptor sabe que o quadro acabou SEM esperar o proximo
                # (sem isso, cada demux MPEG-TS no caminho segura 1 quadro = 100 ms a 10 fps)
                "-omit_video_pes_length", "0",
                "-f", "mpegts", self.url]

    def _ler_stderr(self, proc):
        for line in iter(proc.stderr.readline, b""):
            log("[h264] ffmpeg: " + line.decode(errors="replace").rstrip())

    def run(self):
        import subprocess
        dt = 1.0 / self.fps
        while running:
            frame, _, _ = latest.wait_frame(-1, 2.0)
            if frame is None:
                time.sleep(0.2)                              # camera ainda nao deu o primeiro quadro
                continue
            h, w = frame.shape[:2]
            try:
                self.proc = subprocess.Popen(self._cmd(w, h), stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            except FileNotFoundError:
                log("[h264] ffmpeg nao encontrado (sudo apt install ffmpeg); H.264 desligado")
                return
            threading.Thread(target=self._ler_stderr, args=(self.proc,), daemon=True).start()
            log(f"[h264] enviando para {self.url}: {w}x{h} {self.fps} fps fixos, GOP {self.fps}, {self.kbps} kbps")
            self.status = "enviando"
            last_seq = -1
            t_next = time.monotonic()
            try:
                while running:
                    frame, seq, _ = latest.wait_frame(-2, 0)        # pega o mais recente sem esperar
                    if frame is None:
                        time.sleep(dt)
                        continue
                    if seq == last_seq:
                        self.repetidos += 1                         # camera mais lenta que a taxa fixa
                    last_seq = seq
                    if frame.shape[:2] != (h, w):
                        frame = cv2.resize(frame, (w, h))
                    self.proc.stdin.write(cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420).tobytes())
                    self.enviados += 1
                    t_next += dt
                    espera = t_next - time.monotonic()
                    if espera > 0:
                        time.sleep(espera)
                    elif espera < -1.0:                              # atrasou muito: realinha o relogio
                        t_next = time.monotonic()
                    if self.proc.poll() is not None:
                        raise BrokenPipeError("ffmpeg terminou")
            except (BrokenPipeError, OSError, ValueError) as e:
                self.status = f"ffmpeg caiu ({e}); reiniciando"
                log(f"[h264] {self.status}")
            finally:
                try:
                    self.proc.stdin.close()
                except Exception:
                    pass
                try:
                    self.proc.kill()
                except Exception:
                    pass
            time.sleep(1.0)


h264 = None


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
                "h264": ({"url": h264.url, "status": h264.status, "enviados": h264.enviados, "repetidos": h264.repetidos,
                          "fps": h264.fps, "kbps": h264.kbps} if h264 else None),
            }).encode()
            return self._send(200, "application/json", body)
        if path == "/tags":
            if runner is None:
                return self._send(404, "application/json", b'{"erro": "AprilTag desligada (--sem-tags)"}')
            depois = None
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            for kv in q.split("&"):
                if kv.startswith("depois="):
                    try:
                        depois = int(kv.split("=", 1)[1])
                    except ValueError:
                        pass
            return self._send(200, "application/json", json.dumps(tagstate.snapshot(depois)).encode())
        if path in ("/calibrar", "/zerar"):
            if runner is None:
                return self._send(404, "application/json", b'{"ok": false, "msg": "AprilTag desligada"}')
            if path == "/zerar":
                runner.zerar()
                ok, msg = True, "ajuste zerado (escala x1.000)"
            else:
                ref = 50.0
                q = self.path.split("?", 1)[1] if "?" in self.path else ""
                for kv in q.split("&"):
                    if kv.startswith("ref="):
                        try:
                            ref = float(kv.split("=", 1)[1])
                        except ValueError:
                            pass
                ok, msg = runner.calibrar(tagstate.tags, ref)
            log(f"[ajuste] {msg}")
            return self._send(200, "application/json", json.dumps({"ok": ok, "msg": msg}).encode())
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
            # buffer pequeno: se o Wi-Fi nao der vazao, o write bloqueia e o proximo envio ja pega o quadro
            # mais novo (os intermediarios sao descartados) em vez de acumular segundos de video na fila
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, args.sndbuf_kb * 1024)
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
    global h264
    if args.h264:
        h264 = H264Sender(args.h264, args.h264_fps, args.h264_kbps)
        h264.start()
    if runner is not None:
        threading.Thread(target=tag_loop, daemon=True).start()
        log(f"[tag] AprilTag no Pi: {args.tag_familias}, lado {args.tag_mm:.0f} mm, camera "
            f"{os.path.basename(runner.cam.path)} (fx {runner.cam.K0[0, 0]:.0f}), escala x{runner.escala:.3f}")
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
