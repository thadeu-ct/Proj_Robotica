#!/usr/bin/env python3
"""
Processa no computador o video que o Raspberry Pi transmite (camera_server.py):
  - YOLO (nano_LR.pt): caixas de ampulheta, anel_base, anel_haste, cilindro, icosaedro, peca_chata
  - AprilTag: calculada NO PI (camera_server.py publica em /tags) e so desenhada aqui.
    Com --tags-local (ou com --cam/--arquivo) a tag e calculada aqui mesmo.

Uso:
    python detector_pc.py --pi 192.168.1.70          # video MJPEG do Pi
    python detector_pc.py --udp udp://127.0.0.1:5001 --saida-mjpeg 8091
                                                     # video H.264 (copia local do relay da GS) e
                                                     # video anotado em http://127.0.0.1:8091/stream
    python detector_pc.py --cam 0                    # webcam deste computador (teste sem Pi)
    python detector_pc.py --arquivo foto.jpg         # imagem ou video (teste)

Teclas na janela:
    q / Esc  sair                 s  salvar imagem em pc/capturas/
    c        ajustar distancia: deixe UMA tag a --dist-ref cm da camera e aperte c (o ajuste fica salvo no Pi)
    r        zerar o ajuste       y / a  liga/desliga YOLO / AprilTag
    + / -    confianca minima do YOLO
"""
import argparse
import json
import os
import sys
import time
from collections import deque

import cv2
import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import visao_lib as vl  # noqa: E402

MODELO_PADRAO = os.path.join(AQUI, "..", "pi", "models", "nano_LR.pt")

p = argparse.ArgumentParser(description="YOLO + AprilTag no computador, com o video vindo do Pi")
fonte = p.add_argument_group("fonte do video (escolha uma)")
fonte.add_argument("--pi", default="192.168.1.70", help="IP do Raspberry Pi (padrao 192.168.1.70)")
fonte.add_argument("--porta", type=int, default=8080)
fonte.add_argument("--url", default=None, help="URL completa do MJPEG (ex.: http://127.0.0.1:18080/stream)")
fonte.add_argument("--udp", default=None, help="H.264 MPEG-TS por UDP (ex.: udp://127.0.0.1:5001, copia local do relay da GS)")
fonte.add_argument("--cam", default=None, help="webcam deste computador (indice), para testar sem o Pi")
fonte.add_argument("--arquivo", default=None, help="imagem ou video, para testar sem camera")
p.add_argument("--modelo", default=MODELO_PADRAO)
p.add_argument("--classes", default=os.path.join(AQUI, "classes_contrato.json"),
               help="JSON que traduz classe do modelo -> artefato do contrato (edite la, nao no codigo)")
p.add_argument("--imgsz", type=int, default=512, help="o modelo foi treinado em 512")
p.add_argument("--conf", type=float, default=0.50)
p.add_argument("--device", default="auto", help="auto, mps, cpu ou cuda")
p.add_argument("--tags-local", action="store_true", help="calcula a AprilTag aqui em vez de usar a do Pi")
p.add_argument("--tag-familias", default="25h9,36h11", help="(so com tag local) familias de AprilTag")
p.add_argument("--tag-mm", type=float, default=42.0, help="(so com tag local) lado da tag em mm: borda preta, sem a margem branca; a do lab tem 42")
p.add_argument("--tag-ids", default=None, help="so considerar esses IDs (ex.: 5,7)")
p.add_argument("--camera", default=None, help="JSON de calibracao (padrao: visao/pi/camera_pi.json, senao camera_lab.json)")
p.add_argument("--dist-ref", type=float, default=50.0, help="distancia real (cm) usada na tecla c")
p.add_argument("--sem-janela", action="store_true", help="nao abre janela (so terminal)")
p.add_argument("--quadros", type=int, default=0, help="para depois de N quadros processados (0 = infinito)")
p.add_argument("--salvar-cada", type=int, default=0, help="salva imagem anotada a cada N quadros")
p.add_argument("--largura-janela", type=int, default=960)
p.add_argument("--saida-mjpeg", type=int, default=0, help="publica o video ANOTADO em http://127.0.0.1:PORTA/stream (dashboard)")
p.add_argument("--saida-bind", default="127.0.0.1", help="interface da saida anotada (0.0.0.0 para outros computadores)")
args = p.parse_args()

CAPTURAS = os.path.join(AQUI, "capturas")


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# --------------------------------------------------------------------------- modelo
if not os.path.exists(args.modelo):
    sys.exit(f"[modelo] nao achei {args.modelo}. Ele fica fora do git: copie visao_pi.tar.gz/models para visao/pi/models/")

from ultralytics import YOLO  # noqa: E402
import torch  # noqa: E402

log(f"[modelo] carregando {os.path.relpath(args.modelo)}")
model = YOLO(args.modelo, task="detect")
NAMES = model.names if isinstance(model.names, dict) else dict(enumerate(model.names))
log(f"[modelo] classes: {NAMES}")


def carregar_mapa_classes(path):
    """classe do modelo -> nome do artefato no contrato (None = nao e artefato). Arquivo: classes_contrato.json."""
    if not os.path.exists(path):
        log(f"[classes] {path} nao existe: usando os nomes do modelo")
        return {n: n for n in NAMES.values()}
    with open(path) as f:
        cfg = json.load(f)
    mapa, validos = cfg.get("mapa", {}), set(cfg.get("artefatos_validos", []))
    for nome in NAMES.values():
        if nome not in mapa:
            log(f"[classes] AVISO: classe '{nome}' do modelo nao esta no mapa; nao vai virar artefato")
    for k, v in mapa.items():
        if v is not None and validos and v not in validos:
            log(f"[classes] AVISO: '{k}' -> '{v}', mas '{v}' nao esta em artefatos_validos")
    log("[classes] " + ", ".join(f"{k}->{v or '-'}" for k, v in mapa.items()))
    return mapa


MAPA_CLASSES = carregar_mapa_classes(args.classes)


def escolher_device():
    if args.device != "auto":
        return [args.device]
    c = []
    if torch.cuda.is_available():
        c.append("cuda")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        c.append("mps")
    c.append("cpu")
    return c


DEVICE = None
dummy = np.zeros((480, 640, 3), np.uint8)
for dev in escolher_device():
    try:
        model.predict(dummy, imgsz=args.imgsz, conf=args.conf, device=dev, verbose=False)
        DEVICE = dev
        break
    except Exception as e:
        log(f"[modelo] device {dev} falhou ({e.__class__.__name__}); tentando o proximo")
if DEVICE is None:
    sys.exit("[modelo] nenhum device funcionou")
log(f"[modelo] rodando em {DEVICE}, imgsz={args.imgsz}")

# cores fixas por classe (BGR)
PALETA = [(0, 200, 255), (255, 120, 0), (0, 220, 0), (255, 0, 200), (0, 90, 255), (200, 200, 0), (180, 105, 255)]
COR = {i: PALETA[i % len(PALETA)] for i in NAMES}

# --------------------------------------------------------------------------- camera e tags
cam = vl.escolher_camera(args.camera)
ids = [int(x) for x in args.tag_ids.split(",")] if args.tag_ids else None
tags_det = vl.TagDetector(args.tag_familias.split(","), args.tag_mm, ids)
escala = vl.carregar_escala()
log(f"[camera] {os.path.basename(cam.path)}: fx={cam.K0[0,0]:.0f} fy={cam.K0[1,1]:.0f} "
    f"(~{cam.hfov:.0f} graus de campo) {'com' if cam.has_dist else 'sem'} correcao de distorcao")

# --------------------------------------------------------------------------- fonte
src = vl.abrir_fonte(args)
nome_fonte = getattr(src, "url", None) or str(args.arquivo or f"webcam {args.cam}")
log(f"[fonte] {nome_fonte}")
pitags = None
if isinstance(src, (vl.StreamReader, vl.UdpReader)) and not args.tags_local:
    base = src.base if isinstance(src, vl.StreamReader) else f"http://{args.pi}:{args.porta}"
    pitags = vl.PiTags(base)
    pitags.start()
    log("[tag] AprilTag calculada no Pi (lida de /tags); lado/familias/escala definidos no Pi")
else:
    log(f"[tag] AprilTag local: {args.tag_familias}, lado {args.tag_mm:.0f} mm, escala x{escala:.3f}")


# --------------------------------------------------------------------------- desenho
def texto(img, s, org, cor=(255, 255, 255), escala_f=0.55, fundo=(0, 0, 0), esp=1):
    (w, h), b = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, escala_f, esp)
    x, y = int(org[0]), int(org[1])
    x = max(0, min(x, img.shape[1] - w - 4))
    y = max(h + 4, min(y, img.shape[0] - b - 2))
    if fundo is not None:
        cv2.rectangle(img, (x - 2, y - h - 4), (x + w + 2, y + b + 2), fundo, -1)
    cv2.putText(img, s, (x, y), cv2.FONT_HERSHEY_SIMPLEX, escala_f, cor, esp, cv2.LINE_AA)


def desenhar(img, dets, tags, hud, msg):
    for d in dets:
        x1, y1, x2, y2 = map(int, d["xyxy"])
        cor = COR.get(d["cls"], (0, 255, 255))
        cv2.rectangle(img, (x1, y1), (x2, y2), cor, 2)
        # nome do contrato; classe fora do contrato aparece com o nome do modelo entre parenteses
        rotulo = d["artefato"] if d["artefato"] else f"({d['nome']})"
        texto(img, f"{rotulo} {d['conf']:.2f}", (x1, y1 - 6), (0, 0, 0), 0.55, cor)
    for t in tags:
        pts = t["corners"].astype(int)
        cv2.polylines(img, [pts.reshape(-1, 1, 2)], True, (0, 255, 0), 2)
        cv2.circle(img, tuple(pts[0]), 4, (0, 0, 255), -1)          # canto 0 (orientacao)
        if "rvec" in t:
            cv2.drawFrameAxes(img, cam.K, None, t["rvec"], t["tvec"], args.tag_mm / 1000 * 0.5, 2)
        cx, cy = t["center"]
        l1 = f"tag{t['familia']} ID {t['id']}"
        l2 = f"{t['dist_m'] * 100:.1f} cm" if "dist_m" in t else "sem pose"
        l3 = f"{t['angulo_deg']:+.0f} graus" if "angulo_deg" in t else ""
        yb = int(pts[:, 1].max()) + 22
        texto(img, l1, (cx - 60, yb), (0, 0, 0), 0.55, (0, 255, 0))
        texto(img, l2, (cx - 60, yb + 26), (0, 0, 0), 0.8, (0, 255, 0), 2)
        if l3:
            texto(img, l3, (cx - 60, yb + 50), (0, 0, 0), 0.5, (0, 255, 0))
    # barra de status
    y = 20
    for s in hud:
        texto(img, s, (6, y), (255, 255, 255), 0.5, (40, 40, 40))
        y += 22
    if msg:
        texto(img, msg, (6, img.shape[0] - 12), (0, 0, 0), 0.6, (0, 255, 255), 2)


def tela_espera(status):
    img = np.zeros((480, 640, 3), np.uint8)
    texto(img, "Aguardando imagem...", (20, 200), (255, 255, 255), 0.9, None, 2)
    texto(img, nome_fonte, (20, 240), (200, 200, 200), 0.5, None)
    for k, linha in enumerate([status[i:i + 70] for i in range(0, len(status), 70)][:4]):
        texto(img, linha, (20, 280 + 24 * k), (0, 200, 255), 0.5, None)
    texto(img, "Confira no Pi: tmux attach -t cam   (q sai)", (20, 440), (160, 160, 160), 0.5, None)
    return img


# --------------------------------------------------------------------------- saida anotada (dashboard)
saida = None
if args.saida_mjpeg:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Saida:
        def __init__(self):
            self.cond = threading.Condition()
            self.jpg, self.seq, self.clientes = None, 0, 0
            self.deteccoes = {"ts": 0, "objetos": [], "tags": []}   # servido em /deteccoes

        def put(self, img):
            if self.clientes <= 0 and self.jpg is not None and self.seq % 10:
                self.seq += 1                                   # ninguem assistindo: atualiza so 1 em 10
                return
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ok:
                with self.cond:
                    self.jpg = buf.tobytes()
                    self.seq += 1
                    self.cond.notify_all()

    saida = _Saida()

    class _H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/deteccoes":
                body = json.dumps(saida.deteccoes).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/frame.jpg":
                jpg = saida.jpg
                if jpg is None:
                    self.send_response(503); self.end_headers(); return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpg)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(jpg)
                return
            if path not in ("/stream", "/"):
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            saida.clientes += 1
            last = -1
            try:
                while True:
                    with saida.cond:
                        if saida.seq == last:
                            saida.cond.wait(2.0)
                        jpg, last = saida.jpg, saida.seq
                    if jpg is None:
                        continue
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg) + jpg + b"\r\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                saida.clientes -= 1

    _srv = ThreadingHTTPServer((args.saida_bind, args.saida_mjpeg), _H)
    _srv.daemon_threads = True
    threading.Thread(target=_srv.serve_forever, daemon=True).start()
    log(f"[saida] video anotado em http://{args.saida_bind}:{args.saida_mjpeg}/stream")

# --------------------------------------------------------------------------- loop
JANELA = "Visao Robotica (q sai)"
if not args.sem_janela:
    cv2.namedWindow(JANELA, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(JANELA, args.largura_janela, int(args.largura_janela * 0.75))

usar_yolo, usar_tag = True, True
last_seq = -1
n = 0
t_proc = deque(maxlen=30)
lat_hist, yolo_hist, tag_hist = deque(maxlen=30), deque(maxlen=30), deque(maxlen=30)
msg, msg_ate = "", 0.0
t_print = time.time()
ultimo = None          # (img_anotada, tags) para a tecla c / s
t_ultimo_quadro = time.time()


def mostrar_msg(s, dur=4.0):
    global msg, msg_ate
    msg, msg_ate = s, time.time() + dur
    log(s)


try:
    while True:
        frame, seq, t_cap = src.get(last_seq)
        if frame is None:
            if time.time() - t_ultimo_quadro > 1.0 or ultimo is None:
                if not args.sem_janela:
                    cv2.imshow(JANELA, tela_espera(src.status) if ultimo is None or not src.connected else ultimo[0])
                if time.time() - t_print > 3:
                    log(f"[fonte] {src.status}")
                    t_print = time.time()
            if not args.sem_janela:
                k = cv2.waitKey(5) & 0xFF
                if k in (ord("q"), 27):
                    break
            else:
                time.sleep(0.005)
            continue
        last_seq = seq
        t_ultimo_quadro = time.time()
        t0 = time.perf_counter()

        img = cam.undistort(frame)
        h, w = img.shape[:2]
        K = cam.for_size(w, h)

        dets = []
        if usar_yolo:
            ty = time.perf_counter()
            r = model.predict(img, imgsz=args.imgsz, conf=args.conf, device=DEVICE, verbose=False)[0]
            yolo_hist.append((time.perf_counter() - ty) * 1000)
            if r.boxes is not None and len(r.boxes):
                xyxy = r.boxes.xyxy.cpu().numpy()
                confs = r.boxes.conf.cpu().numpy()
                clss = r.boxes.cls.cpu().numpy().astype(int)
                for b, c, k in zip(xyxy, confs, clss):
                    if np.all(np.isfinite(b)):
                        nome = NAMES.get(int(k), str(k))
                        dets.append({"xyxy": b, "conf": float(c), "cls": int(k), "nome": nome,
                                     "artefato": MAPA_CLASSES.get(nome)})

        tags = []
        snap = None
        if usar_tag and pitags is not None:
            tags, snap = pitags.tags()
        elif usar_tag:
            tt = time.perf_counter()
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            tags = tags_det.detect(gray, K, escala=escala)
            tag_hist.append((time.perf_counter() - tt) * 1000)

        t_proc.append(time.perf_counter())
        lat_hist.append((time.time() - t_cap) * 1000)
        fps_proc = (len(t_proc) - 1) / (t_proc[-1] - t_proc[0]) if len(t_proc) > 1 and t_proc[-1] > t_proc[0] else 0.0

        if pitags is not None:
            if snap:
                tag_txt = (f"Tag NO PI {'on ' if usar_tag else 'off'} {snap.get('fps', 0):4.1f} fps {snap.get('proc_ms', 0):3.0f} ms  |  "
                           f"lado {snap.get('tag_mm') or 0:.0f} mm, escala x{snap.get('escala') or 1:.3f}")
            else:
                tag_txt = f"Tag NO PI: {pitags.status[:60]}"
        else:
            tag_txt = (f"Tag local {'on ' if usar_tag else 'off'} {np.mean(tag_hist) if tag_hist else 0:3.0f} ms  |  "
                       f"lado {args.tag_mm:.0f} mm, escala x{escala:.3f}")
        hud = [
            f"{nome_fonte}  |  video {src.fps:4.1f} fps  |  processado {fps_proc:4.1f} fps  |  atraso {np.mean(lat_hist):4.0f} ms",
            f"YOLO {'on ' if usar_yolo else 'off'} {np.mean(yolo_hist) if yolo_hist else 0:4.0f} ms ({DEVICE}, conf {args.conf:.2f})  |  {tag_txt}",
        ]
        if time.time() > msg_ate:
            msg = ""
        anot = img.copy()
        desenhar(anot, dets, tags, hud, msg)
        ultimo = (anot, tags)
        if saida is not None:
            saida.deteccoes = {
                "ts": time.time(),
                # "artifact" usa o nome do contrato (null = classe do modelo fora do contrato)
                "objetos": [{"artifact": d["artefato"], "classe_modelo": d["nome"], "conf": round(d["conf"], 3),
                             "box": [round(float(v), 1) for v in d["xyxy"]]} for d in dets],
                "tags": [{"id": t["id"], "dist_cm": round(t["dist_m"] * 100, 1), "angulo_deg": round(t["angulo_deg"], 1)}
                         for t in tags if "dist_m" in t],
            }
            saida.put(anot)
        n += 1

        if args.salvar_cada and n % args.salvar_cada == 0:
            os.makedirs(CAPTURAS, exist_ok=True)
            cv2.imwrite(os.path.join(CAPTURAS, f"quadro_{time.strftime('%H%M%S')}_{n:05d}.jpg"), anot)

        if time.time() - t_print >= 1.0:
            objs = ", ".join(f"{d['artefato'] or '(' + d['nome'] + ')'} {d['conf']:.2f}" for d in dets) or "-"
            tg = ", ".join(f"ID{t['id']} {t['dist_m'] * 100:.1f}cm {t['angulo_deg']:+.0f}°" for t in tags if "dist_m" in t) or "-"
            log(f"proc {fps_proc:4.1f} fps | atraso {np.mean(lat_hist):4.0f} ms | objetos: {objs} | tags: {tg}")
            t_print = time.time()

        if not args.sem_janela:
            cv2.imshow(JANELA, anot)
            k = cv2.waitKey(1) & 0xFF
            if k in (ord("q"), 27):
                break
            elif k == ord("s"):
                os.makedirs(CAPTURAS, exist_ok=True)
                fn = os.path.join(CAPTURAS, f"captura_{time.strftime('%Y%m%d_%H%M%S')}.jpg")
                cv2.imwrite(fn, anot)
                cv2.imwrite(fn.replace(".jpg", "_cru.jpg"), frame)
                mostrar_msg(f"salvo {os.path.relpath(fn)}")
            elif k == ord("c") and pitags is not None:
                r = pitags.calibrar(args.dist_ref)
                mostrar_msg(("ajuste salvo no Pi: " if r.get("ok") else "ajuste falhou: ") + r.get("msg", ""), 6)
            elif k == ord("r") and pitags is not None:
                r = pitags.zerar()
                mostrar_msg(r.get("msg", ""))
            elif k == ord("c"):
                validas = [t for t in tags if "dist_raw_m" in t]
                if len(validas) != 1:
                    mostrar_msg(f"ajuste: precisa de exatamente 1 tag na imagem (tem {len(validas)})")
                else:
                    t = validas[0]
                    escala = (args.dist_ref / 100.0) / t["dist_raw_m"]
                    vl.salvar_escala(escala, {"dist_ref_cm": args.dist_ref, "tag_mm": args.tag_mm,
                                              "tag_id": t["id"], "dist_medida_sem_ajuste_cm": t["dist_raw_m"] * 100,
                                              "camera": os.path.basename(cam.path)})
                    mostrar_msg(f"ajuste salvo: escala x{escala:.3f} (media {t['dist_raw_m']*100:.1f} cm -> real {args.dist_ref:.0f} cm)", 6)
            elif k == ord("r"):
                escala = 1.0
                vl.salvar_escala(1.0, {"zerado": True})
                mostrar_msg("ajuste zerado (escala x1.000)")
            elif k == ord("y"):
                usar_yolo = not usar_yolo
                mostrar_msg(f"YOLO {'ligado' if usar_yolo else 'desligado'}")
            elif k == ord("a"):
                usar_tag = not usar_tag
                mostrar_msg(f"AprilTag {'ligada' if usar_tag else 'desligada'}")
            elif k in (ord("+"), ord("=")):
                args.conf = min(0.95, args.conf + 0.05)
                mostrar_msg(f"confianca minima {args.conf:.2f}")
            elif k in (ord("-"), ord("_")):
                args.conf = max(0.05, args.conf - 0.05)
                mostrar_msg(f"confianca minima {args.conf:.2f}")

        if args.quadros and n >= args.quadros:
            break
except KeyboardInterrupt:
    pass
finally:
    src.stop()
    if pitags is not None:
        pitags.stop()
    if not args.sem_janela:
        cv2.destroyAllWindows()
    if lat_hist:
        log(f"[fim] {n} quadros | atraso medio {np.mean(lat_hist):.0f} ms | YOLO {np.mean(yolo_hist) if yolo_hist else 0:.0f} ms | tag {np.mean(tag_hist) if tag_hist else 0:.0f} ms")
