#!/usr/bin/env python3
"""
Simula o video do robo: gera H.264 no padrao do contrato (4.1) e manda em MPEG-TS/UDP,
igual ao Pi. Serve para testar a ground station sem o robo.

    python robo_simulado.py                       # para udp://127.0.0.1:5000, 15 fps, 30 s
    python robo_simulado.py --segundos 0          # sem fim
    python robo_simulado.py --fora-do-padrao      # perfil high, B-frames, 4 fatias, GOP 3 s (testa os avisos)
"""
import argparse
import fractions
import time

import av

p = argparse.ArgumentParser()
p.add_argument("--destino", default="udp://127.0.0.1:5000")
p.add_argument("--fps", type=int, default=15)
p.add_argument("--kbps", type=int, default=1000)
p.add_argument("--segundos", type=float, default=30)
p.add_argument("--fora-do-padrao", action="store_true")
args = p.parse_args()

av.logging.set_level(av.logging.ERROR)
W, H = 640, 480
url = args.destino + ("" if "pkt_size" in args.destino else "?pkt_size=1316")
out = av.open(url, mode="w", format="mpegts")
s = out.add_stream("libx264", rate=args.fps)
s.width, s.height, s.pix_fmt = W, H, "yuv420p"
s.bit_rate = args.kbps * 1000
if args.fora_do_padrao:
    s.codec_context.profile = "High"
    s.options = {"x264-params": "keyint=%d:bframes=2:slices=4" % (3 * args.fps), "preset": "veryfast"}
else:
    # o mesmo que o contrato pede: baseline, sem B-frames, 1 fatia, keyframe a cada 1 s com SPS/PPS
    s.codec_context.profile = "Baseline"
    s.options = {"preset": "ultrafast", "tune": "zerolatency",
                 "x264-params": "keyint=%d:min-keyint=%d:scenecut=0:bframes=0:sliced-threads=0:slices=1:repeat-headers=1:vbv-maxrate=%d:vbv-bufsize=%d"
                 % (args.fps, args.fps, args.kbps, args.kbps)}

print("enviando %s para %s (%d fps, %d kbps)" % ("FORA do padrao" if args.fora_do_padrao else "H.264 baseline", args.destino, args.fps, args.kbps), flush=True)
t0, i = time.time(), 0
try:
    while not args.segundos or i < args.segundos * args.fps:
        q = av.VideoFrame(W, H, "yuv420p")
        # faixa que anda + fundo que muda de cor, para ver movimento e travamentos
        y = q.planes[0]
        linha = bytes([(x + i * 4) % 256 for x in range(W)])
        y.update(linha * H if y.line_size == W else b"".join(linha + bytes(y.line_size - W) for _ in range(H)))
        for pl, v in ((q.planes[1], (i * 3) % 256), (q.planes[2], 255 - (i * 3) % 256)):
            pl.update(bytes([v]) * (pl.line_size * pl.height))
        q.pts, q.time_base = i, fractions.Fraction(1, args.fps)
        for pk in s.encode(q):
            out.mux(pk)
        i += 1
        espera = t0 + i / args.fps - time.time()
        if espera > 0:
            time.sleep(espera)
except KeyboardInterrupt:
    pass
for pk in s.encode(None):
    out.mux(pk)
out.close()
print("%d quadros enviados" % i)
