// Testa firmware/esp32_locomocao/protocolo.h contra quadros gerados pelo controle/protocolo_esp32.py.
// Rodar: firmware/teste_host/rodar_testes.sh
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include "../esp32_locomocao/protocolo.h"

static std::vector<uint8_t> hex(const char* s) {
  std::vector<uint8_t> v;
  for (size_t i = 0; s[i] && s[i + 1]; i += 2) {
    while (s[i] == ' ') i++;
    unsigned b; sscanf(s + i, "%2x", &b); v.push_back((uint8_t)b);
  }
  return v;
}

int main(int, char** argv) {
  int falhas = 0;
  // 1) CRC: vetor oficial do CRC-16/CCITT-FALSE
  const char* s = "123456789";
  if (proto::crc16((const uint8_t*)s, 9) != 0x29B1) { printf("FALHA crc\n"); falhas++; }
  // 2) quadros vindos do Python (argv[1]): lixo + CMD_VEL partido + HEARTBEAT + quadro com CRC errado + MODE
  std::vector<uint8_t> fluxo = hex(argv[1]);
  proto::Leitor L;
  std::string visto;
  for (uint8_t b : fluxo)
    if (L.alimentar(b)) {
      const proto::Quadro& q = L.quadro();
      char buf[96];
      if (q.tipo == proto::CMD_VEL) snprintf(buf, sizeof buf, "CMD_VEL seq=%d v=%d w=%d;", q.seq, proto::le_i16(q.carga), proto::le_i16(q.carga + 2));
      else snprintf(buf, sizeof buf, "tipo=%d seq=%d len=%d;", q.tipo, q.seq, q.len);
      visto += buf;
    }
  const char* esperado = "CMD_VEL seq=7 v=123 w=-500;tipo=4 seq=8 len=0;tipo=3 seq=9 len=1;";
  if (visto != esperado) { printf("FALHA leitor: '%s'\n", visto.c_str()); falhas++; }
  if (L.erros_crc() != 1) { printf("FALHA erros_crc=%u\n", L.erros_crc()); falhas++; }
  // 3) telemetria montada aqui -> impressa em hex para o Python decodificar
  uint8_t c[proto::TAM_TELEMETRIA], q[64];
  proto::carga_telemetria(c, 1234, -56, 0.101f, 0.099f, 11850, proto::MODO_AUTONOMO, 0b0011, -45.2f, 215);
  size_t n = proto::montar(q, proto::TELEMETRIA, 9, c, proto::TAM_TELEMETRIA);
  for (size_t i = 0; i < n; i++) printf("%02x", q[i]);
  printf("\n%s\n", falhas ? "FALHOU" : "ok");
  return falhas;
}
