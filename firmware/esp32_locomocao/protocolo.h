// ============================================================================
// protocolo.h — protocolo Pi <-> ESP32 do relatorio (secao 5.5), igual ao controle/protocolo_esp32.py
// (que e a referencia; se mudar um, mude o outro). C++ puro, sem Arduino: testado no computador
// por firmware/teste_host/teste_protocolo.cpp.
//
// Quadro: 0xAA 0x55 | tipo | seq | len | carga (0..64) | crc16 (LE)
//         crc16 = CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) sobre tipo, seq, len e carga.
// CMD_VEL  0x01  carga: int16 v [mm/s], int16 w [mrad/s] (w > 0 = esquerda)
// MODE     0x03  carga: uint8 (0 ESPERA, 1 AUTONOMO, 2 MANUAL)
// HEARTBEAT 0x04 carga vazia
// TELEMETRIA 0x81 (ESP32 -> Pi), 20 bytes "<iihhHBBhH":
//         enc_esq, enc_dir (int32), v_esq, v_dir (int16 mm/s), bateria (uint16 mV),
//         modo (uint8), falhas (uint8: bit0 watchdog, bit1 bateria baixa, bit2 driver, bit3 i2c),
//         rumo (int16, centesimos de grau), tof (uint16 mm, 0 = sem leitura)
// ============================================================================
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace proto {

const uint8_t CMD_VEL = 0x01, ARM_POSE = 0x02, MODE = 0x03, HEARTBEAT = 0x04, TELEMETRIA = 0x81;
const uint8_t MODO_ESPERA = 0, MODO_AUTONOMO = 1, MODO_MANUAL = 2;
const uint8_t MAX_CARGA = 64;
const uint8_t FALHA_WATCHDOG = 1 << 0, FALHA_BATERIA = 1 << 1, FALHA_DRIVER = 1 << 2, FALHA_I2C = 1 << 3;
const size_t TAM_TELEMETRIA = 20;

inline uint16_t crc16(const uint8_t* d, size_t n, uint16_t crc = 0xFFFF) {
  for (size_t i = 0; i < n; i++) {
    crc ^= (uint16_t)d[i] << 8;
    for (int b = 0; b < 8; b++) crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}

inline int16_t le_i16(const uint8_t* p) { return (int16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8)); }
inline void poe_u16(uint8_t* p, uint16_t v) { p[0] = v & 0xFF; p[1] = v >> 8; }
inline void poe_i32(uint8_t* p, int32_t v) {
  uint32_t u = (uint32_t)v;
  p[0] = u & 0xFF; p[1] = (u >> 8) & 0xFF; p[2] = (u >> 16) & 0xFF; p[3] = u >> 24;
}

struct Quadro {
  uint8_t tipo, seq, len;
  uint8_t carga[MAX_CARGA];
};

// Le byte a byte (direto da serial) e avisa quando um quadro valido fica pronto.
class Leitor {
 public:
  // devolve true quando 'quadro()' tem um quadro novo com CRC certo
  bool alimentar(uint8_t b) {
    switch (est_) {
      case H1: if (b == 0xAA) est_ = H2; return false;
      case H2: est_ = (b == 0x55) ? TIPO : (b == 0xAA ? H2 : H1); return false;
      case TIPO: q_.tipo = b; est_ = SEQ; return false;
      case SEQ: q_.seq = b; est_ = LEN; return false;
      case LEN:
        if (b > MAX_CARGA) { est_ = H1; return false; }
        q_.len = b; i_ = 0; est_ = b ? CARGA : CRC1; return false;
      case CARGA: q_.carga[i_++] = b; if (i_ >= q_.len) est_ = CRC1; return false;
      case CRC1: crc_rx_ = b; est_ = CRC2; return false;
      case CRC2: {
        crc_rx_ |= (uint16_t)b << 8;
        est_ = H1;
        uint8_t cab[3] = {q_.tipo, q_.seq, q_.len};
        uint16_t c = crc16(q_.carga, q_.len, crc16(cab, 3));
        if (c == crc_rx_) return true;
        erros_crc_++;
        return false;
      }
    }
    est_ = H1;
    return false;
  }
  const Quadro& quadro() const { return q_; }
  uint32_t erros_crc() const { return erros_crc_; }

 private:
  enum Estado { H1, H2, TIPO, SEQ, LEN, CARGA, CRC1, CRC2 } est_ = H1;
  Quadro q_{};
  uint8_t i_ = 0;
  uint16_t crc_rx_ = 0;
  uint32_t erros_crc_ = 0;
};

// Monta um quadro em 'out' (precisa de 7 + len bytes). Devolve o tamanho.
inline size_t montar(uint8_t* out, uint8_t tipo, uint8_t seq, const uint8_t* carga, uint8_t len) {
  out[0] = 0xAA; out[1] = 0x55; out[2] = tipo; out[3] = seq; out[4] = len;
  for (uint8_t i = 0; i < len; i++) out[5 + i] = carga[i];
  uint16_t c = crc16(out + 2, 3u + len);
  out[5 + len] = c & 0xFF; out[6 + len] = c >> 8;
  return 7u + len;
}

inline int16_t sat16(float x) {
  if (x > 32767.0f) return 32767;
  if (x < -32768.0f) return -32768;
  return (int16_t)(x >= 0 ? x + 0.5f : x - 0.5f);
}

// Carga da TELEMETRIA (20 bytes, mesma ordem do struct "<iihhHBBhH" do Python).
inline void carga_telemetria(uint8_t* c, int32_t enc_esq, int32_t enc_dir, float v_esq_m_s, float v_dir_m_s,
                             uint16_t bateria_mv, uint8_t modo, uint8_t falhas, float rumo_graus, uint16_t tof_mm) {
  poe_i32(c + 0, enc_esq);
  poe_i32(c + 4, enc_dir);
  poe_u16(c + 8, (uint16_t)sat16(v_esq_m_s * 1000.0f));
  poe_u16(c + 10, (uint16_t)sat16(v_dir_m_s * 1000.0f));
  poe_u16(c + 12, bateria_mv);
  c[14] = modo;
  c[15] = falhas;
  poe_u16(c + 16, (uint16_t)sat16(rumo_graus * 100.0f));
  poe_u16(c + 18, tof_mm);
}

}  // namespace proto
