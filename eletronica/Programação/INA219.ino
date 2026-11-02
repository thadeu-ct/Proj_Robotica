/** Teste unitário do monitor de corrente e tensão INA219.
 *  Configura a leitura em Fast Mode (400 kHz) no endereço padrão 0x40 e exibe
 *  tensão da bateria (V), corrente consumida (mA) e potência total (mW).
 *  Inclui fator de escala para a troca do resistor shunt original (R100 -> R200).
 */

#include <Wire.h>
#include <Adafruit_INA219.h>

#define I2C_SDA 21
#define I2C_SCL 22

Adafruit_INA219 ina219(0x40);

// Fator de correção do resistor Shunt:
// A biblioteca Adafruit assume o resistor original de 0.1 Ohm (marcação 100 / R100).
//- Se estiver usando o resistor original (100 / 0.1 Ohm), mantenha FATOR_SHUNT = 1.0
// - Se trocou para R200 (0.2 Ohm), use 0.5
// - Se trocou para R050 (0.05 Ohm para dobrar a corrente máxima), use 2.0
const float FATOR_SHUNT = 1.0; 

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(1);

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000); // Fast Mode (400kHz)

  Serial.println("\n--- Inicializando Monitor de Energia INA219 ---");

  if (!ina219.begin(&Wire)) {
    Serial.println("Falha ao encontrar o chip INA219! Verifique a fiacao I2C.");
    while (1) delay(10);
  }

  // Configura alcance máximo padrão (32V, 2A com resistor original)
  ina219.setCalibration_32V_2A();

  Serial.println("Sensor INA219 pronto e medindo!");
  Serial.println("--------------------------------------------------------------------------");
}

void loop() {
  float shuntVoltage_mV = ina219.getShuntVoltage_mV();
  float busVoltage_V    = ina219.getBusVoltage_V();
  
  // Aplica o fator de escala caso o resistor shunt físico tenha sido substituído
  float current_mA      = ina219.getCurrent_mA() * FATOR_SHUNT;
  float power_mW        = ina219.getPower_mW() * FATOR_SHUNT;
  
  // Tensão real na entrada (VIN+) = Tensão no barramento (VIN-) + queda no resistor shunt
  float loadVoltage_V   = busVoltage_V + (shuntVoltage_mV / 1000.0);

  Serial.print("Tensao Bateria: ");
  Serial.print(loadVoltage_V, 2);
  Serial.print(" V | Queda Shunt: ");
  Serial.print(shuntVoltage_mV, 2);
  Serial.print(" mV | Corrente: ");
  Serial.print(current_mA, 1);
  Serial.print(" mA | Potencia: ");
  Serial.print(power_mW, 1);
  Serial.println(" mW");

  delay(200); // Taxa de atualização de 5 Hz para facilitar leitura no monitor serial
}