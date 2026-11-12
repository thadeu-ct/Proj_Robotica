#!/usr/bin/env bash
# Testes do firmware que rodam no computador (sem ESP32). Uso: firmware/teste_host/rodar_testes.sh
set -euo pipefail
cd "$(dirname "$0")"
RAIZ="$(cd ../.. && pwd)"
CXX="${CXX:-c++}"
"$CXX" -std=c++17 -O2 -Wall -Wextra -o /tmp/teste_protocolo teste_protocolo.cpp
"$CXX" -std=c++17 -O2 -Wall -Wextra -o /tmp/teste_controle teste_controle.cpp
# quadros gerados pelo lado do Pi (Python): lixo + CMD_VEL partido + HEARTBEAT + quadro com CRC errado + MODE
FLUXO=$(python3 - "$RAIZ" <<'PY'
import sys; sys.path.insert(0, sys.argv[1] + "/controle")
import protocolo_esp32 as p
q = b"\x00\xff" + p.montar_cmd_vel(0.123, -0.5, 7) + p.montar_heartbeat(8)
q += b"\xAA\x55\x01\x09\x04\x00\x00\x00\x00\x12\x34" + p.montar_modo(p.MODO_AUTONOMO, 9)
print(q.hex())
PY
)
SAIDA=$(/tmp/teste_protocolo "$FLUXO")
echo "protocolo C++ <- Python: $(echo "$SAIDA" | tail -1)"
TEL=$(echo "$SAIDA" | head -1)
python3 - "$RAIZ" "$TEL" <<'PY'
import sys; sys.path.insert(0, sys.argv[1] + "/controle")
import protocolo_esp32 as p
qs = p.Decodificador().alimentar(bytes.fromhex(sys.argv[2]))
t = p.decodificar_telemetria(qs[0][2])
ok = (t["enc_esq"], t["enc_dir"], t["v_esq_m_s"], t["bateria_v"], t["modo"], t["falhas"], t["rumo_graus"], t["tof_cm"]) == \
     (1234, -56, 0.101, 11.85, 1, ["watchdog", "bateria_baixa"], -45.2, 21.5)
print("telemetria C++ -> Python:", "ok" if ok else f"FALHOU {t}")
sys.exit(0 if ok else 1)
PY
/tmp/teste_controle
