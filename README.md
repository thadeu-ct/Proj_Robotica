# Proj_Robotica

Rover de resgate (ENG4461). **Comece pelo [`AGENTS.md`](AGENTS.md)**: arquitetura, portas, como ligar e armadilhas conhecidas.

- `arduino/pid_pwm/`: PID/PWM com leitura de RPM (aula 16/09)
- `arduino/cinematica_braco/`: cinemática direta e inversa do braço de 2 servos
- `servo_web/`: controle do servo pelo navegador (instruções no README da pasta)
- `visao/`: percepção. `visao/pi/` roda no Raspberry Pi (câmera, **AprilTag embarcada** com distância e ângulo, vídeo H.264/MJPEG); `visao/pc/` roda no Mac (**YOLO**, nomes de artefato do contrato e saída anotada para o dashboard). Guias em `visao/README.md`
- `eletronica/`: ESP32 (motores NXT com DRV8833, encoders, sensores I2C, servos): pinagem e testes por componente. Ver `eletronica/README.md`
- `dashboard/`: painel do robô para a tela (vista superior, câmera, modo, itens, tempo, bateria), atualizado por JSON. Ver `dashboard/README.md`
- `ground_station/`: relay de vídeo do Mac para o servidor da competição (WHIP) e câmera da arena no dashboard. Guia em `docs/ground_station.md`
- `docs/`: contrato de interface da competição (`contrato_interface.md`) e guia da ground station
