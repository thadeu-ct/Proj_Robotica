# Robot Arm Control — Arduino Uno + FastAPI

Projeto para controlar dois servos por navegador, na mesma máquina ou por outro computador da rede local.

## Arquivos
- `servo.ino`: firmware Arduino; servo 1 no D6 e servo 2 no D4.
- `main.py`: API FastAPI e comunicação serial USB.
- `index.html`: interface com cinemática direta (alavancas) e inversa (joystick XY).
- `requirements.txt`: dependências Python.

## Instalação (Windows / PowerShell)
1. Conecte os servos a uma **fonte de 5 V com corrente suficiente**, ligando o GND da fonte ao GND do Arduino. Não alimente dois servos diretamente pelo pino 5 V do Uno; não una o positivo da fonte externa ao 5 V do Uno alimentado via USB.
2. Carregue `servo.ino` pela Arduino IDE. Se necessário, salve-o em uma pasta chamada `servo` para abrir como sketch. Confira os pinos D6 e D4.
3. Feche o Monitor Serial.
4. Na pasta deste projeto, execute:

```powershell
python -m pip install -r requirements.txt
$env:ARDUINO_PORT="COM18"
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

5. Abra http://127.0.0.1:8000. Em outro computador na mesma rede use `http://IP_DO_SERVIDOR:8000` (libere a porta 8000 apenas para a rede privada).

## Protocolo
- `1,90,45\n`: cinemática direta (ângulos das juntas em graus).
- `0,15,10\n`: cinemática inversa (posição XY nas unidades dos segmentos).

A interface XY calcula os ângulos e envia comandos diretos para manter visual e movimento sincronizados. A API e o Arduino também aceitam diretamente comandos no modo inverso.

## Cuidados e limitações
- Protótipo para rede local confiável, sem autenticação: **não exponha a API à internet**.
- A mensagem `enviado=true` significa que a escrita na porta serial foi concluída; não comprova movimento físico.
- Cinemática pressupõe zero mecânico, sentido e deslocamento angular compatíveis com `Servo.write()`. Ajuste offsets e limites conforme sua montagem antes de permitir movimento completo.
- O firmware utiliza um ramo da solução inversa (cotovelo positivo), com ângulos válidos 0–180 e raio 12–27.
