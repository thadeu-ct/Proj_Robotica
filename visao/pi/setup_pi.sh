#!/usr/bin/env bash
# Checagem rapida do Raspberry Pi antes do teste de visao. Rode no Pi: bash setup_pi.sh
set -u
echo "== Sistema =="
uname -m; grep -m1 Model /proc/cpuinfo 2>/dev/null; free -h | head -2
python3 --version
[ "$(uname -m)" = "aarch64" ] || echo "!! SO 32 bits: torch/ultralytics/ncnn nao tem wheel oficial. Use Raspberry Pi OS 64-bit."

echo; echo "== Temperatura / throttling =="
vcgencmd measure_temp 2>/dev/null; vcgencmd get_throttled 2>/dev/null   # 0x0 = ok; 0x50005 = subtensao

echo; echo "== Camera =="
ls /dev/video* 2>/dev/null || echo "!! nenhuma /dev/video*"
command -v v4l2-ctl >/dev/null || sudo apt-get install -y v4l-utils >/dev/null
v4l2-ctl --list-devices 2>/dev/null
v4l2-ctl -d /dev/video0 --list-formats-ext 2>/dev/null | grep -E "MJPG|YUYV|Size" | head -12

echo; echo "== Pacotes Python =="
python3 - <<'EOF'
import importlib
for m in ["cv2", "numpy", "ultralytics", "ncnn", "pupil_apriltags", "onnxruntime"]:
    try:
        mod = importlib.import_module(m); print(f"ok   {m} {getattr(mod, '__version__', '')}")
    except Exception as e:
        print(f"FALTA {m}  ({e.__class__.__name__})")
EOF
echo
echo "Se faltar ncnn:            pip install ncnn            (adicione --break-system-packages se der erro de 'externally managed')"
echo "Se faltar pupil_apriltags: pip install pupil-apriltags"
echo "Desempenho: sudo sh -c 'echo performance > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor'"
