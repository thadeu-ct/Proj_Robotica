/** Escaneia de barramento I2C para validação física de conexões (SDA/SCL).
 *  Varre os endereços de 0x01 a 0x7E a 100 kHz para identificar sensores conectados.
 *          Endereços esperados no projeto: 
 *          - 0x29: Sensor ToF VL53L1X
 *          - 0x40: INA219
 *          - 0x68: IMU MPU-6050
 */

#include <Wire.h>

#define I2C_SDA 21
#define I2C_SCL 22

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(1);

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000); // Clock de 100kHz para teste espaçado e dar tempo de leitura
  Serial.println("\n--- Escaneando Barramento I2C ---");
}

void loop() {
  byte error, address;
  int nDevices = 0;

  for (address = 1; address < 127; address++) {
    Wire.beginTransmission(address);
    error = Wire.endTransmission();

    if (error == 0) {
      Serial.print("Dispositivo encontrado em: 0x");
      if (address < 16) Serial.print("0");
      Serial.println(address, HEX);
      nDevices++;
    }
  }

  if (nDevices == 0) {
    Serial.println("Nenhum dispositivo encontrado. Verifique alimentacao e pull-ups em SDA/SCL.");
  } else {
    Serial.println("Varredura concluida.\n");
  }
  
  delay(2000);
}