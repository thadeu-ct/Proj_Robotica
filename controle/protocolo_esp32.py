"""
Protocolo Pi <-> ESP32 (relatorio, secao 5.5). ESTE ARQUIVO E A REFERENCIA: o firmware do ESP32
precisa implementar exatamente o mesmo formato.

Quadro (bytes):
    0xAA 0x55 | tipo (1) | seq (1) | len (1) | carga (len bytes, 0..64) | crc16 (2, little-endian)
    - seq: contador ciclico 0..255 por quem envia (detecta perdas).
    - crc16: CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, sem reflexao, sem xor final)
      calculado sobre tipo, seq, len e carga (NAO inclui 0xAA 0x55).
    - numeros na carga: inteiros em ponto fixo, little-endian.

Tipos (Pi -> ESP32):
    0x01 CMD_VEL    carga: v int16 [mm/s], w int16 [mrad/s]   (w > 0 = gira para a esquerda, anti-horario visto de cima)
    0x03 MODE       carga: modo uint8 (0 = ESPERA, 1 = AUTONOMO, 2 = MANUAL)
    0x04 HEARTBEAT  carga: vazia
Tipos (ESP32 -> Pi):
    0x81 TELEMETRIA a 100 Hz (ou menos), 20 bytes, struct "<iihhHBBhH":
         enc_esq int32 [contagens acumuladas], enc_dir int32,
         v_esq int16 [mm/s medida], v_dir int16,
         bateria uint16 [mV, do INA219], modo uint8 (o modo EFETIVO do ESP32), falhas uint8 (bits, abaixo),
         rumo int16 [centesimos de grau, -18000..18000], tof uint16 [mm; 0 = sem leitura]
       bits de falhas: 0 watchdog (ficou sem quadro do Pi), 1 bateria baixa, 2 nFAULT do DRV8833,
                       3 erro I2C (IMU/ToF/INA), 4..7 reservados

Seguranca (lado ESP32): sem quadro valido por 250 ms -> zera os motores e vai para ESPERA.
O Pi manda CMD_VEL a 20-50 Hz, mesmo que seja zero.

Uso:
    from protocolo_esp32 import montar_cmd_vel, Decodificador
    porta.write(montar_cmd_vel(0.10, 0.0, seq))      # 10 cm/s para a frente
"""
import struct

CABECALHO = b"\xAA\x55"
CMD_VEL, ARM_POSE, MODE, HEARTBEAT, TELEMETRIA = 0x01, 0x02, 0x03, 0x04, 0x81
MODO_ESPERA, MODO_AUTONOMO, MODO_MANUAL = 0, 1, 2
MAX_CARGA = 64


def crc16_ccitt(dados: bytes, crc: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE. Vetor de teste: crc16_ccitt(b"123456789") == 0x29B1."""
    for b in dados:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def montar_quadro(tipo: int, seq: int, carga: bytes = b"") -> bytes:
    if len(carga) > MAX_CARGA:
        raise ValueError(f"carga de {len(carga)} bytes (maximo {MAX_CARGA})")
    corpo = bytes([tipo & 0xFF, seq & 0xFF, len(carga)]) + carga
    return CABECALHO + corpo + struct.pack("<H", crc16_ccitt(corpo))


def _sat16(x: float) -> int:
    return max(-32768, min(32767, int(round(x))))


def montar_cmd_vel(v_m_s: float, w_rad_s: float, seq: int) -> bytes:
    """v em m/s (+ = frente), w em rad/s (+ = esquerda). Vai como mm/s e mrad/s em int16."""
    return montar_quadro(CMD_VEL, seq, struct.pack("<hh", _sat16(v_m_s * 1000), _sat16(w_rad_s * 1000)))


def montar_modo(modo: int, seq: int) -> bytes:
    return montar_quadro(MODE, seq, bytes([modo]))


def montar_heartbeat(seq: int) -> bytes:
    return montar_quadro(HEARTBEAT, seq)


FORMATO_TELEMETRIA = "<iihhHBBhH"          # 20 bytes; ver docstring do modulo
FALHAS = {0: "watchdog", 1: "bateria_baixa", 2: "driver_falha", 3: "erro_i2c"}


def montar_telemetria(seq, enc_esq, enc_dir, v_esq_mm_s, v_dir_mm_s, bateria_mv, modo, falhas, rumo_cdeg, tof_mm):
    """Usado pelo firmware (como referencia) e por simuladores do lado do Pi."""
    return montar_quadro(TELEMETRIA, seq, struct.pack(FORMATO_TELEMETRIA, enc_esq, enc_dir, _sat16(v_esq_mm_s),
                                                      _sat16(v_dir_mm_s), bateria_mv, modo, falhas,
                                                      _sat16(rumo_cdeg), tof_mm))


def decodificar_telemetria(carga: bytes) -> dict:
    """Carga de um quadro TELEMETRIA -> dict com unidades no nome."""
    if len(carga) != struct.calcsize(FORMATO_TELEMETRIA):
        raise ValueError(f"telemetria com {len(carga)} bytes (esperado {struct.calcsize(FORMATO_TELEMETRIA)})")
    ee, ed, ve, vd, bat, modo, falhas, rumo, tof = struct.unpack(FORMATO_TELEMETRIA, carga)
    return {"enc_esq": ee, "enc_dir": ed, "v_esq_m_s": ve / 1000, "v_dir_m_s": vd / 1000,
            "bateria_v": bat / 1000, "modo": modo, "falhas": [n for b, n in FALHAS.items() if falhas >> b & 1],
            "rumo_graus": rumo / 100, "tof_cm": (tof / 10) if tof else None}


class Decodificador:
    """Recebe bytes soltos da serial e devolve quadros validos (tipo, seq, carga). Descarta lixo e CRC errado."""

    def __init__(self):
        self.buf = bytearray()
        self.crc_erros = 0

    def alimentar(self, dados: bytes):
        self.buf += dados
        quadros = []
        while True:
            i = self.buf.find(CABECALHO)
            if i < 0:
                del self.buf[:-1]                  # guarda o ultimo byte (pode ser o 0xAA de um cabecalho)
                return quadros
            if i:
                del self.buf[:i]
            if len(self.buf) < 5:
                return quadros
            n = self.buf[4]
            total = 2 + 3 + n + 2
            if n > MAX_CARGA:
                del self.buf[:2]
                continue
            if len(self.buf) < total:
                return quadros
            corpo = bytes(self.buf[2:5 + n])
            crc = struct.unpack("<H", self.buf[5 + n:total])[0]
            if crc == crc16_ccitt(corpo):
                quadros.append((corpo[0], corpo[1], corpo[3:]))
                del self.buf[:total]
            else:
                self.crc_erros += 1
                del self.buf[:2]


if __name__ == "__main__":
    # autoteste: python protocolo_esp32.py
    assert crc16_ccitt(b"123456789") == 0x29B1, hex(crc16_ccitt(b"123456789"))
    q = montar_cmd_vel(0.123, -0.5, 7)
    print("CMD_VEL(0.123 m/s, -0.5 rad/s, seq 7):", q.hex(" "))
    d = Decodificador()
    out = d.alimentar(b"\x00\xff" + q[:4]) + d.alimentar(q[4:] + montar_heartbeat(8) + b"\xAA\x55\x01\x09\x04\x00\x00\x00\x00\x12\x34")
    assert out[0] == (CMD_VEL, 7, struct.pack("<hh", 123, -500)), out
    assert out[1] == (HEARTBEAT, 8, b""), out
    assert d.crc_erros == 1, d.crc_erros
    tel = Decodificador().alimentar(montar_telemetria(9, 1234, -56, 101, 99, 11850, MODO_AUTONOMO, 0b0011, -4520, 215))
    t = decodificar_telemetria(tel[0][2])
    assert t["bateria_v"] == 11.85 and t["falhas"] == ["watchdog", "bateria_baixa"] and t["rumo_graus"] == -45.2, t
    assert t["tof_cm"] == 21.5 and t["enc_dir"] == -56, t
    print("autoteste ok: CRC, montagem, quadro partido ao meio, lixo antes, CRC errado descartado e telemetria")
