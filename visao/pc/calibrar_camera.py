#!/usr/bin/env python3
"""
Calibra a camera do Pi pelo video transmitido, com o tabuleiro de xadrez do lab (8x5 cantos internos).
Gera visao/pi/camera_pi.json, que o Pi (AprilTag) e o detector_pc.py passam a usar no lugar do camera_lab.json.
Depois de calibrar, rode ./iniciar.sh de novo para ele mandar o arquivo para o Pi.

Uso:
    python calibrar_camera.py --pi 192.168.1.70
    python calibrar_camera.py --cam 0          # webcam deste computador

Mostre o tabuleiro em posicoes e inclinacoes diferentes (perto, longe, cantos da imagem, inclinado).
Mais facil: python calibrar_camera.py --auto   (captura sozinho a cada posicao nova e salva com 20 capturas)
Teclas: espaco = captura (so quando o tabuleiro aparece colorido)   a = captura automatica on/off
        c = calcula e salva (minimo 10 capturas, ideal 15-25)        q = sair
"""
import argparse
import json
import math
import os
import sys
import time

import cv2
import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import visao_lib as vl  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--pi", default="192.168.1.70")
p.add_argument("--porta", type=int, default=8080)
p.add_argument("--url", default=None)
p.add_argument("--cam", default=None)
p.add_argument("--arquivo", default=None)
p.add_argument("--colunas", type=int, default=8, help="cantos internos na horizontal (tabuleiro do lab: 8)")
p.add_argument("--linhas", type=int, default=5, help="cantos internos na vertical (tabuleiro do lab: 5)")
p.add_argument("--quadrado-mm", type=float, default=25.0, help="nao afeta a distancia focal; so a escala das poses")
p.add_argument("--saida", default=os.path.normpath(os.path.join(AQUI, "..", "pi", "camera_pi.json")))
p.add_argument("--min", type=int, default=10)
p.add_argument("--auto", action="store_true", help="captura sozinho e calcula ao chegar em --alvo capturas")
p.add_argument("--alvo", type=int, default=20, help="no modo --auto, calcula e salva com esse numero de capturas")
p.add_argument("--sem-janela", action="store_true", help="(teste) nao abre janela; exige --auto")
args = p.parse_args()

PAT = (args.colunas, args.linhas)
objp = np.zeros((PAT[0] * PAT[1], 3), np.float32)
objp[:, :2] = np.mgrid[0:PAT[0], 0:PAT[1]].T.reshape(-1, 2) * args.quadrado_mm / 1000.0
CRIT = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3)

src = vl.abrir_fonte(args)
JAN = "Calibracao (espaco captura, c calcula, q sai)"
if not args.sem_janela:
    cv2.namedWindow(JAN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(JAN, 960, 720)

objpoints, imgpoints, centros = [], [], []
size = None
auto = args.auto
if args.sem_janela and not args.auto:
    sys.exit("--sem-janela exige --auto")
t_auto = 0.0
msg = "mostre o tabuleiro"
last = -1
vis = np.zeros((480, 640, 3), np.uint8)


def calcular():
    flags = cv2.CALIB_ZERO_TANGENT_DIST | cv2.CALIB_FIX_K3
    rms, K, dist, _, _ = cv2.calibrateCamera(objpoints, imgpoints, size, None, None, flags=flags)
    hfov = 2 * math.degrees(math.atan(size[0] / 2 / K[0, 0]))
    out = {
        "descricao": f"Camera do Pi calibrada em {time.strftime('%Y-%m-%d %H:%M')} com tabuleiro {PAT[0]}x{PAT[1]}",
        "K": K.round(3).tolist(), "dist": dist.ravel().round(6).tolist(), "size": list(size),
        "rms_px": round(float(rms), 4), "n_fotos": len(objpoints), "hfov_graus": round(hfov, 1),
    }
    with open(args.saida, "w") as f:
        json.dump(out, f, indent=2)
    return rms, K, dist, hfov


while True:
    frame, seq, _ = src.get(last)
    if frame is not None:
        last = seq
        size = (frame.shape[1], frame.shape[0])
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        ok, corners = cv2.findChessboardCorners(gray, PAT, cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE + cv2.CALIB_CB_FAST_CHECK)
        vis = frame.copy()
        for c in centros:
            cv2.circle(vis, c, 3, (255, 0, 255), -1)       # onde ja capturou (cubra a imagem toda)
        if ok:
            cv2.drawChessboardCorners(vis, PAT, corners, ok)
        if ok and auto and time.time() - t_auto > 1.2:
            c = tuple(int(v) for v in corners.reshape(-1, 2).mean(0))
            if all(math.dist(c, o) > 40 for o in centros[-3:]):
                corners2 = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), CRIT)
                objpoints.append(objp); imgpoints.append(corners2); centros.append(c)
                t_auto = time.time()
                msg = f"captura automatica {len(objpoints)}"
        k = (cv2.waitKey(1) & 0xFF) if not args.sem_janela else 255
        if args.auto and len(objpoints) >= args.alvo:
            k = ord("c")
        if k == ord(" "):
            if ok:
                corners2 = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), CRIT)
                objpoints.append(objp); imgpoints.append(corners2)
                centros.append(tuple(int(v) for v in corners.reshape(-1, 2).mean(0)))
                msg = f"capturado {len(objpoints)}"
            else:
                msg = "tabuleiro nao encontrado neste quadro"
    else:
        k = (cv2.waitKey(10) & 0xFF) if not args.sem_janela else 255
        if args.sem_janela:
            time.sleep(0.01)
        if last == -1:
            vis = np.zeros((480, 640, 3), np.uint8)
            cv2.putText(vis, src.status[:80], (10, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1)

    if k in (ord("q"), 27):
        break
    if k == ord("a"):
        auto = not auto
        msg = f"captura automatica {'ligada' if auto else 'desligada'}"
    if k == ord("c"):
        if len(objpoints) < args.min:
            msg = f"precisa de pelo menos {args.min} capturas (tem {len(objpoints)})"
        else:
            rms, K, dist, hfov = calcular()
            msg = f"salvo! erro {rms:.2f} px, fx={K[0,0]:.0f}, campo {hfov:.0f} graus"
            print(f"[calibracao] {args.saida}\n  erro de reprojecao: {rms:.3f} px (bom: < 0.5; aceitavel: < 1.0)\n"
                  f"  fx={K[0,0]:.1f} fy={K[1,1]:.1f} cx={K[0,2]:.1f} cy={K[1,2]:.1f}  campo horizontal {hfov:.1f} graus\n"
                  f"  dist={dist.ravel().round(4).tolist()}", flush=True)
            if args.auto:
                if not args.sem_janela:
                    cv2.putText(vis, msg, (8, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)
                    cv2.imshow(JAN, vis)
                    cv2.waitKey(2500)
                break

    cv2.putText(vis, f"{len(objpoints)} capturas | auto {'on' if auto else 'off'} | {msg}", (8, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2, cv2.LINE_AA)
    if not args.sem_janela:
        cv2.imshow(JAN, vis)

src.stop()
if not args.sem_janela:
    cv2.destroyAllWindows()
