#!/usr/bin/env python3
"""
AprilTag embarcada no Raspberry Pi: detecta a tag e calcula distancia e angulo ate ela.
Roda sozinho (imprime no terminal) ou como biblioteca (camera_server.py e, depois, o controle dos motores).

Uso no Pi:
    python apriltag_pi.py                    # camera 0, tag25h9, lado 100 mm
    python apriltag_pi.py --tag-mm 80        # tag com outro tamanho
    python apriltag_pi.py --tag-familias 25h9,36h11

Saida: "ID 5  52.3 cm  +4 graus  (x +3.6 cm, z 52.2 cm)"  -> angulo + = tag a direita da camera.

Depende so de OpenCV (cv2.aruco) e NumPy. Nas fotos do lab o cv2.aruco achou a tag25h9 onde o
pupil-apriltags falhou. Pose por solvePnP (IPPE_SQUARE) com os intrinsecos de camera_*.json.
"""
import json
import math
import os
import time

import cv2
import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
ESCALA_PATH = os.path.join(AQUI, "ajuste_distancia.json")

FAMILIAS = {
    "25h9": cv2.aruco.DICT_APRILTAG_25h9,
    "36h11": cv2.aruco.DICT_APRILTAG_36h11,
    "16h5": cv2.aruco.DICT_APRILTAG_16h5,
    "36h10": cv2.aruco.DICT_APRILTAG_36h10,
}


# --------------------------------------------------------------------------- camera
class Camera:
    """Intrinsecos (K, dist) de um JSON. Ajusta K se a resolucao do video for outra."""

    def __init__(self, path):
        with open(path) as f:
            c = json.load(f)
        self.path = path
        self.K0 = np.array(c["K"], dtype=np.float64)
        self.dist = np.array(c.get("dist", [0, 0, 0, 0, 0]), dtype=np.float64).reshape(-1, 1)
        self.size0 = tuple(c["size"])
        self.has_dist = bool(np.any(np.abs(self.dist) > 1e-9))
        self._size = None
        self.K = self.K0.copy()

    def for_size(self, w, h):
        if self._size != (w, h):
            sx, sy = w / self.size0[0], h / self.size0[1]
            K = self.K0.copy()
            K[0, 0] *= sx
            K[0, 2] *= sx
            K[1, 1] *= sy
            K[1, 2] *= sy
            self.K, self._size = K, (w, h)
        return self.K

    @property
    def hfov(self):
        return 2 * math.degrees(math.atan(self.size0[0] / 2 / self.K0[0, 0]))


def escolher_camera(path=None, pasta=AQUI):
    if path:
        return Camera(path)
    for nome in ("camera_pi.json", "camera_lab.json"):
        p = os.path.join(pasta, nome)
        if os.path.exists(p):
            return Camera(p)
    raise SystemExit(f"nenhum camera_pi.json / camera_lab.json em {pasta}")


# --------------------------------------------------------------------------- ajuste de escala
def carregar_escala(path=ESCALA_PATH):
    try:
        with open(path) as f:
            return float(json.load(f)["escala"])
    except Exception:
        return 1.0


def salvar_escala(escala, info, path=ESCALA_PATH):
    with open(path, "w") as f:
        json.dump({"escala": escala, **info, "quando": time.strftime("%Y-%m-%d %H:%M:%S")}, f, indent=2)


# --------------------------------------------------------------------------- detector
class TagDetector:
    def __init__(self, familias=("25h9",), tag_mm=100.0, ids=None):
        self.tag_mm = float(tag_mm)
        self.ids = set(ids) if ids else None
        self.dets = []
        for f in familias:
            f = str(f).lower().replace("tag", "").strip()
            if f not in FAMILIAS:
                raise SystemExit(f"familia desconhecida: {f} (use {', '.join(FAMILIAS)})")
            p = cv2.aruco.DetectorParameters()
            p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
            self.dets.append((f, cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(FAMILIAS[f]), p)))

    def detect(self, gray, K, dist=None, escala=1.0):
        """Lista de tags: id, familia, corners (px), dist_m, angulo_deg, x_m, y_m, z_m, ..."""
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
                ok, rvec, tvec = cv2.solvePnP(obj, pts, K, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
                if ok:
                    t = tvec.ravel()
                    raw = float(np.linalg.norm(t))
                    R, _ = cv2.Rodrigues(rvec)
                    tag.update(
                        rvec=rvec, tvec=tvec,
                        dist_raw_m=raw,
                        dist_m=raw * escala,
                        x_m=float(t[0]) * escala, y_m=float(t[1]) * escala, z_m=float(t[2]) * escala,
                        angulo_deg=float(math.degrees(math.atan2(t[0], t[2]))),        # + = tag a direita
                        yaw_tag_deg=float(math.degrees(math.atan2(-R[2, 0], R[2, 2]))),  # giro da tag em torno do eixo vertical
                        lado_px=float(np.mean([np.linalg.norm(pts[k] - pts[(k + 1) % 4]) for k in range(4)])),
                    )
                out.append(tag)
        return out


def tag_para_json(t):
    """Versao serializavel (sem arrays numpy), para mandar pela rede."""
    d = {"id": t["id"], "familia": t["familia"],
         "corners": [[round(float(x), 1), round(float(y), 1)] for x, y in t["corners"]],
         "center": [round(float(t["center"][0]), 1), round(float(t["center"][1]), 1)]}
    if "dist_m" in t:
        d.update(dist_cm=round(t["dist_m"] * 100, 1), dist_raw_cm=round(t["dist_raw_m"] * 100, 2),
                 angulo_deg=round(t["angulo_deg"], 1), yaw_tag_deg=round(t["yaw_tag_deg"], 1),
                 x_cm=round(t["x_m"] * 100, 1), y_cm=round(t["y_m"] * 100, 1), z_cm=round(t["z_m"] * 100, 1),
                 lado_px=round(t["lado_px"], 1),
                 rvec=[round(float(v), 5) for v in t["rvec"].ravel()],
                 tvec=[round(float(v), 5) for v in t["tvec"].ravel()])
    return d


class TagRunner:
    """Pipeline completo usado pelo servidor e pelo controle: frame BGR -> lista de tags (com distancia)."""

    def __init__(self, familias=("25h9",), tag_mm=100.0, ids=None, camera=None):
        self.det = TagDetector(familias, tag_mm, ids)
        self.cam = escolher_camera(camera)
        self.escala = carregar_escala()

    def process(self, frame):
        gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        K = self.cam.for_size(w, h)
        return self.det.detect(gray, K, self.cam.dist if self.cam.has_dist else None, self.escala)

    def calibrar(self, tags, ref_cm):
        validas = [t for t in tags if "dist_raw_m" in t]
        if len(validas) != 1:
            return False, f"precisa de exatamente 1 tag na imagem (tem {len(validas)})"
        t = validas[0]
        self.escala = (ref_cm / 100.0) / t["dist_raw_m"]
        salvar_escala(self.escala, {"dist_ref_cm": ref_cm, "tag_mm": self.det.tag_mm, "tag_id": t["id"],
                                    "dist_medida_sem_ajuste_cm": round(t["dist_raw_m"] * 100, 2),
                                    "camera": os.path.basename(self.cam.path)})
        return True, f"escala x{self.escala:.3f} ({t['dist_raw_m'] * 100:.1f} cm medido -> {ref_cm:.0f} cm real)"

    def zerar(self):
        self.escala = 1.0
        salvar_escala(1.0, {"zerado": True})


# --------------------------------------------------------------------------- uso direto no terminal do Pi
def main():
    import argparse
    import sys

    p = argparse.ArgumentParser(description="AprilTag no Pi: imprime distancia e angulo")
    p.add_argument("--device", default="0", help="indice da camera, /dev/videoX, ou arquivo de imagem/video")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--tag-familias", default="25h9")
    p.add_argument("--tag-mm", type=float, default=100.0)
    p.add_argument("--tag-ids", default=None)
    p.add_argument("--camera", default=None)
    p.add_argument("--quadros", type=int, default=0, help="para depois de N quadros (0 = infinito)")
    p.add_argument("--calibrar", type=float, default=None, help="ajusta a escala com a tag a essa distancia (cm) e sai")
    args = p.parse_args()

    ids = [int(x) for x in args.tag_ids.split(",")] if args.tag_ids else None
    run = TagRunner(args.tag_familias.split(","), args.tag_mm, ids, args.camera)
    print(f"[tag] {args.tag_familias}, lado {args.tag_mm:.0f} mm | camera {os.path.basename(run.cam.path)} "
          f"fx={run.cam.K0[0, 0]:.0f} (~{run.cam.hfov:.0f} graus) | escala x{run.escala:.3f}", flush=True)

    src = args.device
    if src.isdigit():
        src = int(src)
    still = None
    if isinstance(src, str) and src.lower().endswith((".jpg", ".jpeg", ".png", ".bmp")):
        still = cv2.imread(src)
        cap = None
    else:
        if isinstance(src, int) and sys.platform.startswith("linux"):
            cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
        else:
            cap = cv2.VideoCapture(src)
        if isinstance(src, int):
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            sys.exit(f"[cam] nao abriu {args.device}. Outro programa usando a camera? (pkill -f camera_server.py)")

    n, t0, t_print, proc = 0, time.time(), 0.0, []
    try:
        while True:
            if still is not None:
                frame = still
            else:
                ok, frame = cap.read()
                if not ok:
                    if isinstance(src, str):
                        break
                    print("[cam] falha de leitura", flush=True)
                    time.sleep(0.2)
                    continue
            tp = time.perf_counter()
            tags = run.process(frame)
            proc.append((time.perf_counter() - tp) * 1000)
            n += 1
            if args.calibrar:
                ok, msg = run.calibrar(tags, args.calibrar)
                print(("[ajuste] " if ok else "[ajuste] falhou: ") + msg, flush=True)
                if ok:
                    break
                time.sleep(0.3)
                continue
            now = time.time()
            if now - t_print >= 0.5 or still is not None:
                fps = n / (now - t0) if now > t0 else 0
                txt = "  |  ".join(
                    f"ID {t['id']}  {t['dist_m'] * 100:5.1f} cm  {t['angulo_deg']:+4.0f} graus  "
                    f"(x {t['x_m'] * 100:+5.1f}, z {t['z_m'] * 100:5.1f} cm)" for t in tags if "dist_m" in t
                ) or "nenhuma tag"
                print(f"{time.strftime('%H:%M:%S')} {fps:4.1f} fps  tag {np.mean(proc[-20:]):3.0f} ms  |  {txt}", flush=True)
                t_print = now
            if args.quadros and n >= args.quadros:
                break
            if still is not None:
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if cap is not None:
            cap.release()


if __name__ == "__main__":
    main()
