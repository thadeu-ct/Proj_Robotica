#!/usr/bin/env python3
"""
Le uma copia local do video (MPEG-TS/UDP) e serve em MJPEG para o navegador/dashboard.
Serve para o operador ver o POV do robo sem depender do servidor nem do detector.

    python ver_video.py                                  # le udp://127.0.0.1:5002, serve em :8092
    python ver_video.py udp://127.0.0.1:5001 --porta 8093
    python ver_video.py --quadros 100                    # so mede fps e sai (teste)

Endpoints: /stream (MJPEG), /frame.jpg (ultimo quadro). Abra http://localhost:8092/stream
"""
import argparse
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import av

p = argparse.ArgumentParser()
p.add_argument("url", nargs="?", default="udp://127.0.0.1:5002")
p.add_argument("--porta", type=int, default=8092)
p.add_argument("--qualidade", type=int, default=5, help="qscale do JPEG (2 = melhor, 31 = pior)")
p.add_argument("--quadros", type=int, default=0, help="para depois de N quadros (0 = infinito, sem servidor HTTP se > 0)")
args = p.parse_args()

av.logging.set_level(av.logging.FATAL)   # perda de UDP gera erros de decodificacao a cada quadro
ultimo = {"jpg": None, "seq": 0}
cond = threading.Condition()


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def ler():
    url = args.url
    if url.startswith("udp://") and "?" not in url:
        url += "?fifo_size=1000000&overrun_nonfatal=1"
    enc = None
    total = 0
    while True:
        try:
            c = av.open(url, format="mpegts", options={"fflags": "nobuffer", "flags": "low_delay"}, timeout=(5, 3))
        except av.FFmpegError:
            log("aguardando video em %s ..." % args.url)
            continue
        s = c.streams.video[0]
        s.thread_type = "AUTO"
        t0, n = time.time(), 0
        try:
            for quadro in c.decode(s):
                if enc is None or (enc.width, enc.height) != (quadro.width, quadro.height):
                    enc = av.CodecContext.create("mjpeg", "w")
                    enc.width, enc.height, enc.pix_fmt = quadro.width, quadro.height, "yuvj420p"
                    enc.time_base = s.time_base or 1
                    enc.options = {"qscale": str(args.qualidade)}
                    enc.flags |= av.codec.context.Flags.qscale
                    enc.qscale = args.qualidade
                    log("video %dx%d" % (quadro.width, quadro.height))
                jpg = b"".join(bytes(pk) for pk in enc.encode(quadro.reformat(format="yuvj420p")))
                with cond:
                    ultimo["jpg"], ultimo["seq"] = jpg, ultimo["seq"] + 1
                    cond.notify_all()
                n += 1
                total += 1
                if time.time() - t0 >= 5:
                    log("%.1f fps" % (n / (time.time() - t0)))
                    t0, n = time.time(), 0
                if args.quadros and total >= args.quadros:
                    log("%d quadros decodificados" % total)
                    return
        except av.FFmpegError:
            log("video parou (3 s sem dados); aguardando de novo")
        finally:
            c.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        rota = self.path.split("?")[0]
        if rota == "/frame.jpg":
            jpg = ultimo["jpg"]
            if jpg is None:
                self.send_error(503, "sem video")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpg)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(jpg)
        elif rota in ("/", "/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=quadro")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            visto = -1
            try:
                while True:
                    with cond:
                        cond.wait_for(lambda: ultimo["seq"] != visto, timeout=5)
                        if ultimo["seq"] == visto:
                            continue
                        jpg, visto = ultimo["jpg"], ultimo["seq"]
                    self.wfile.write(b"--quadro\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg))
                    self.wfile.write(jpg + b"\r\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        else:
            self.send_error(404)


if __name__ == "__main__":
    if args.quadros:
        ler()
    else:
        threading.Thread(target=ler, daemon=True).start()
        srv = ThreadingHTTPServer(("0.0.0.0", args.porta), Handler)
        srv.daemon_threads = True
        log("MJPEG em http://localhost:%d/stream (lendo %s)" % (args.porta, args.url))
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
