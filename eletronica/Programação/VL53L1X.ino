/** Teste unitário do sensor de distância a laser Time-of-Flight (VL53L1X).
 *  Configura leitura contínua em Fast Mode (400 kHz) com alcance Long (até 4m)
 *  e exibe distância em milímetros junto à taxa de retorno de sinal (MCPS).
 */

#include <Wire.h>
#include <VL53L1X.h>

#define I2C_SDA 21
#define I2C_SCL 22

VL53L1X sensor;

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(1);

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000); // Fast Mode (400kHz)

  Serial.println("\n--- Inicializando ToF VL53L1X ---");

  sensor.setBus(&Wire);
  sensor.setTimeout(500);

  if (!sensor.init()) {
    Serial.println("Falha ao inicializar o VL53L1X!");
    while (1) delay(10);
  }

  // Modos disponiveis: Short (~1.3m), Medium (~3.0m), Long (~4.0m)
  sensor.setDistanceMode(VL53L1X::Long);

  // Tempo de integracao por medida em microssegundos (50000us = 50ms)
  sensor.setMeasurementTimingBudget(50000);

  // Inicia medicoes continuas a cada 50ms (20 Hz)
  sensor.startContinuous(50);

  Serial.println("Sensor VL53L1X pronto e operando!");
}

void loop() {
  sensor.read();

  if (sensor.timeoutOccurred()) {
    Serial.println("Timeout na comunicacao I2C!");
  } else {
    // Status 0 = leitura valida | 1 = Sigma fail | 2 = Signal fail | 4 = Out of bounds
    if (sensor.ranging_data.range_status == 0) {
      Serial.print("Distancia: ");
      Serial.print(sensor.ranging_data.range_mm);
      Serial.print(" mm | Sinal: ");
      Serial.print(sensor.ranging_data.peak_signal_count_rate_MCPS);
      Serial.println(" MCPS");
    } else {
      Serial.print("Alvo nao confiavel / Fora de alcance (Status: ");
      Serial.print(sensor.ranging_data.range_status);
      Serial.println(")");
    }
  }

  delay(50);
}