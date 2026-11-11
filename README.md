# Proj_Robotica

Códigos de apoio temporários.

- `arduino/pid_pwm/`: PID/PWM com leitura de RPM (aula 16/09)
- `arduino/cinematica_braco/`: cinemática direta e inversa do braço de 2 servos
- `servo_web/`: controle do servo pelo navegador (instruções no README da pasta)
- `visao/`: calibração da câmera, AprilTag e YOLO (vindos de [STimmy10/Robotica](https://github.com/STimmy10/Robotica))
- `visao/pc/`: **como usar hoje.** O Pi só transmite a câmera e o computador roda YOLO + AprilTag (com distância). Para ligar: `cd visao/pc && ./iniciar.sh`. Guia em `visao/pc/README.md`
- `visao/pi/`: servidor de câmera do Pi (`camera_server.py`) e o teste antigo com o YOLO rodando no próprio Pi (`teste_visao.py`, notas em `NOTAS_PI.md`). Os modelos (`visao/pi/models/`) ficam fora do git
- `dashboard/`: painel do robô para a tela (vista superior, câmera, modo, itens, tempo, bateria), atualizado por JSON. Ver `dashboard/README.md`
- `ground_station/`: relay de vídeo do Mac para o servidor da competição (WHIP) e câmera da arena no dashboard. Guia em `docs/ground_station.md`
- `docs/`: contrato de interface da competição (`contrato_interface.md`) e guia da ground station
