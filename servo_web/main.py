import os
import math
import time
import threading
from pathlib import Path
from contextlib import asynccontextmanager

import serial
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

PORTA = os.getenv("ARDUINO_PORT", "COM18")
R1, R2 = 15.0, 13.0
R_MIN, R_MAX = 12.0, 27.0
arduino = None
serial_lock = threading.Lock()


def calcular_direta(a1, a2):
    t1, t2 = math.radians(a1), math.radians(a2)
    return (R1 * math.cos(t1) + R2 * math.cos(t1 + t2),
            R1 * math.sin(t1) + R2 * math.sin(t1 + t2))


def calcular_inversa(x, y):
    raio = math.hypot(x, y)
    if raio < R_MIN or raio > R_MAX:
        raise HTTPException(422, "Ponto fora do alcance")
    c2 = (x*x + y*y - R1*R1 - R2*R2) / (2*R1*R2)
    c2 = max(-1.0, min(1.0, c2))
    s2 = math.sqrt(max(0.0, 1.0 - c2*c2))
    t2 = math.atan2(s2, c2)
    t1 = math.atan2(y, x) - math.atan2(R2*s2, R1+R2*c2)
    a1 = math.floor(math.degrees(t1) + 0.5)
    a2 = math.floor(math.degrees(t2) + 0.5)
    if not (0 <= a1 <= 180 and 0 <= a2 <= 180):
        raise HTTPException(422, "Limites das juntas excedidos")
    return a1, a2


@asynccontextmanager
async def lifespan(app: FastAPI):
    global arduino
    try:
        arduino = serial.Serial(PORTA, 9600, timeout=1, write_timeout=1)
        time.sleep(2)
        arduino.reset_input_buffer()
        print(f"[ARDUINO] Porta {PORTA} aberta")
    except (serial.SerialException, OSError) as exc:
        print(f"[ERRO] {exc}")
        arduino = None
    yield
    if arduino and arduino.is_open:
        arduino.close()


app = FastAPI(lifespan=lifespan)


class Comando(BaseModel):
    modo: int = Field(ge=0, le=1)
    valor1: float
    valor2: float


@app.get("/")
def pagina():
    return FileResponse(Path(__file__).with_name("index.html"))


@app.get("/status")
def status():
    return {"conectado": arduino is not None and arduino.is_open,
            "porta": PORTA}


@app.post("/servo")
def mover(dados: Comando):
    v1, v2 = dados.valor1, dados.valor2
    if not (math.isfinite(v1) and math.isfinite(v2)):
        raise HTTPException(422, "Valores invalidos")
    if dados.modo == 1:
        if not (0 <= v1 <= 180 and 0 <= v2 <= 180):
            raise HTTPException(422, "Angulos invalidos")
        a1, a2 = math.floor(v1 + 0.5), math.floor(v2 + 0.5)
        x, y = calcular_direta(a1, a2)
        comando = f"1,{a1},{a2}\n"
    else:
        a1, a2 = calcular_inversa(v1, v2)
        comando = f"0,{v1:.4f},{v2:.4f}\n"
        x, y = calcular_direta(a1, a2)
    if arduino is None or not arduino.is_open:
        raise HTTPException(503, "Arduino desconectado")
    try:
        with serial_lock:
            arduino.write(comando.encode("ascii"))
            arduino.flush()
    except (serial.SerialException, OSError) as exc:
        print("[ERRO SERIAL]", exc)
        raise HTTPException(503, "Falha na serial")
    print(f"[SERIAL ->] {comando.strip()}")
    return {"enviado": True, "ang1": a1, "ang2": a2,
            "x": round(x, 2), "y": round(y, 2)}
