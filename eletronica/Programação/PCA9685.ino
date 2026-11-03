/** Teste unitário do módulo PWM PCA9685 (16 canais I2C) com servomotores MG996R.
 *  Opera via I2C (400 kHz) no endereço 0x60 (Jumper A5 fechado).
 *  Conecta os 3 servos da garra nos canais 0, 1 e 2 do módulo.
 *  Permite modo de varredura automática ou envio de ângulo fixo via Monitor Serial.
 */

#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

#define I2C_SDA 21
#define I2C_SCL 22

// Endereço I2C do PCA9685 configurado por hardware em 0x60
#define PCA_ADDR 0x60

// Canais onde os 3 servos MG996R estão conectados no PCA9685 (0 a 15)
#define SERVO_GARRA_1 0
#define SERVO_GARRA_2 1
#define SERVO_GARRA_3 2

// Limites de largura de pulso em "ticks" de 12 bits (0 a 4095) a 50 Hz:
// Periodo de 50 Hz = 20000 us -> 1 tick ~= 4.88 us
// MG996R opera tipicamente entre ~500 us (0°) e ~2500 us (180°)
#define SERVO_MIN_TICKS 102 // ~500 us  (0 graus)
#define SERVO_MAX_TICKS 512 // ~2500 us (180 graus)

Adafruit_PWMServoDriver pca = Adafruit_PWMServoDriver(PCA_ADDR);

bool modoManual = false;

// Converte um ângulo em graus (0 a 180) para contagem PWM de 12 bits (0 a 4095)
uint16_t anguloParaTicks(int angulo) {
  angulo = constrain(angulo, 0, 180);
  return map(angulo, 0, 180, SERVO_MIN_TICKS, SERVO_MAX_TICKS);
}

// Aciona os 3 servos simultaneamente para o mesmo ângulo de teste
void moverServos(int angulo) {
  uint16_t pulso = anguloParaTicks(angulo);
  pca.setPWM(SERVO_GARRA_1, 0, pulso);
  pca.setPWM(SERVO_GARRA_2, 0, pulso);
  pca.setPWM(SERVO_GARRA_3, 0, pulso);

  Serial.print("Angulo: ");
  Serial.print(angulo);
  Serial.print(" graus | Ticks PWM (12-bit): ");
  Serial.println(pulso);
}

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(1);

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000); // Fast Mode (400 kHz)

  Serial.println("\n--- Inicializando Driver PWM PCA9685 (0x60) ---");

  if (!pca.begin()) {
    Serial.println("Falha ao comunicar com o PCA9685 em 0x60! Verifique VCC e SDA/SCL.");
    while (1) delay(10);
  }

  // Calibração do oscilador interno (27 MHz padrão na biblioteca Adafruit) e frequência de 50 Hz
  pca.setOscillatorFrequency(27000000);
  pca.setPWMFreq(50);
  delay(10);

  Serial.println("PCA9685 pronto e operando a 50 Hz!");
  Serial.println("DICA: Digite um valor de 0 a 180 no Monitor Serial para testar um angulo fixo,");
  Serial.println("      ou digite -1 para voltar ao modo de varredura automatica.\n");
}

void loop() {
  // Verifica se o usuário digitou algum ângulo no Monitor Serial
  if (Serial.available() > 0) {
    int entrada = Serial.parseInt();
    // Limpa caracteres extras do buffer serial (como \n ou \r)
    while (Serial.available() > 0) Serial.read();

    if (entrada == -1) {
      modoManual = false;
      Serial.println(">> Retornando ao modo de varredura automatica...");
    } else if (entrada >= 0 && entrada <= 180) {
      modoManual = true;
      Serial.print(">> [MODO MANUAL] ");
      moverServos(entrada);
    }
  }

  // Se não estiver travado em um ângulo manual, executa o ciclo de teste suave (0° -> 90° -> 180°)
  if (!modoManual) {
    moverServos(0);
    delay(1500);

    if (Serial.available() > 0) return;
    moverServos(90);
    delay(1500);

    if (Serial.available() > 0) return;
    moverServos(180);
    delay(1500);

    if (Serial.available() > 0) return;
    moverServos(90);
    delay(1500);
  } else {
    delay(50);
  }
}
