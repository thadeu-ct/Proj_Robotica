/** Teste unitário do sensor giroscópico e acelerômetro.
 *  Configura a leitura em Fast Mode (400 kHz). A leitura do MPU-6050 é via
 *          registradores I2C (burst read de 14 bytes) para minimizar overhead.
 */

#include <Wire.h>

#define I2C_SDA 21
#define I2C_SCL 22
#define MPU_ADDR 0x68


// Variáveis para dados convertidos do MPU-6050
float ax, ay, az;
float gx, gy, gz;
float tempC;

void lerMPU6050();

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(1);

  // Inicializa barramento I2C com 400 kHz (Fast Mode suportado por ambos)
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000);
  delay(100);

  Serial.println("\n--- Inicializando Sensores I2C ---");

  // 1. Inicializa o MPU-6050 (Acorda o sensor tirando do modo Sleep)
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B); // Registrador PWR_MGMT_1
  Wire.write(0x00); // 0 acorda o MPU
  byte errMpu = Wire.endTransmission();

  if (errMpu != 0) {
    Serial.println("Falha ao comunicar com o MPU-6050! Verifique fiacao.");
    while (1) delay(10);
  }
  Serial.println("MPU-6050 acordado e pronto!");
}

void loop() {
  lerMPU6050();
  tof.read();

  Serial.print("ACC [g]: ");
  Serial.print(ax, 2); Serial.print(", ");
  Serial.print(ay, 2); Serial.print(", ");
  Serial.print(az, 2);

  Serial.print(" | GYRO [deg/s]: ");
  Serial.print(gx, 1); Serial.print(", ");
  Serial.print(gy, 1); Serial.print(", ");
  Serial.print(gz, 1);

  delay(50); // Taxa de atualização ~20Hz
}

void lerMPU6050() {
  // Aponta para o início dos registradores de dados (ACCEL_XOUT_H: 0x3B)
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  Wire.endTransmission(false);

  // Requisita 14 bytes consecutivos: Accel (6) + Temp (2) + Gyro (6)
  Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)14);

  if (Wire.available() >= 14) {
    int16_t rawAx = Wire.read() << 8 | Wire.read();
    int16_t rawAy = Wire.read() << 8 | Wire.read();
    int16_t rawAz = Wire.read() << 8 | Wire.read();
    int16_t rawTemp = Wire.read() << 8 | Wire.read();
    int16_t rawGx = Wire.read() << 8 | Wire.read();
    int16_t rawGy = Wire.read() << 8 | Wire.read();
    int16_t rawGz = Wire.read() << 8 | Wire.read();

    // Escalas padrão: Accel +/- 2g (16384 LSB/g) | Gyro +/- 250 deg/s (131 LSB/(deg/s))
    ax = rawAx / 16384.0;
    ay = rawAy / 16384.0;
    az = rawAz / 16384.0;

    gx = rawGx / 131.0;
    gy = rawGy / 131.0;
    gz = rawGz / 131.0;

    tempC = (rawTemp / 340.0) + 36.53;
  }
}