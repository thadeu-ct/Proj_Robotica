/** Teste de Integração I2C (Fusion)
 *  Agrupa ToF (0x29), INA219 (0x40), PCA9685 (0x60) e MPU-6050 (0x68).
 *  Fase 1: Scanner do barramento (roda 1 vez).
 *  Fase 2: Inicialização de todos os módulos detectados.
 *  Fase 3: Loop de leitura e relatório na porta serial (a cada 500 ms).
 */

#include <Wire.h>
#include <VL53L1X.h>
#include <Adafruit_INA219.h>
#include <Adafruit_PWMServoDriver.h>

#define I2C_SDA 21
#define I2C_SCL 22

// Endereços I2C
#define TOF_ADDR  0x29
#define INA_ADDR  0x40
#define PCA_ADDR  0x60
#define MPU_ADDR  0x68

// Objetos
VL53L1X tof;
Adafruit_INA219 ina219(INA_ADDR);
Adafruit_PWMServoDriver pca = Adafruit_PWMServoDriver(PCA_ADDR);

// Flags de Status
bool hasToF = false;
bool hasINA = false;
bool hasPCA = false;
bool hasMPU = false;

// Variáveis MPU-6050
float ax, ay, az, gx, gy, gz;

// Variáveis de tempo para o relatório não travar o loop
unsigned long ultimaAtualizacao = 0;
const unsigned long INTERVALO_RELATORIO = 500; // 500 ms = 2 Hz

// Prototipação
void escanearBarramento();
void inicializarSensores();
void lerMPU6050();

void setup() {
  Serial.begin(115200);
  delay(1000); // Aguarda Monitor Serial

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000); // Começa a 100 kHz para escaneamento seguro

  Serial.println("\n=============================================");
  Serial.println("    SISTEMA DE DIAGNOSTICO I2C UNIFICADO");
  Serial.println("=============================================\n");

  escanearBarramento();

  Wire.setClock(400000); // Sobe para Fast Mode (400 kHz) para operação real
  
  inicializarSensores();
  
  Serial.println("\n[SISTEMA] Iniciando ciclo de leituras...\n");
}

void loop() {
  // O loop roda na máxima velocidade possível, atualizando os sensores rápidos
  if (hasMPU) lerMPU6050();
  if (hasToF) tof.read();

  // Movimento suave do PCA9685 baseado no tempo (varredura contínua independente do delay)
  if (hasPCA) {
    // Oscila entre ~102 e ~512 baseado no millis()
    uint16_t pulso = map(sin(millis() / 500.0) * 100, -100, 100, 205, 410); 
    pca.setPWM(0, 0, pulso);
    pca.setPWM(1, 0, pulso);
    pca.setPWM(2, 0, pulso);
  }

  // Gera o relatório a cada 500 ms
  if (millis() - ultimaAtualizacao >= INTERVALO_RELATORIO) {
    ultimaAtualizacao = millis();
    
    Serial.println("---------------- RELATORIO ----------------");

    if (hasINA) {
      float shunt_mV = ina219.getShuntVoltage_mV();
      float bus_V    = ina219.getBusVoltage_V();
      float curr_mA  = ina219.getCurrent_mA();
      float pwr_mW   = ina219.getPower_mW();
      Serial.print("[INA219]  Tensao: "); Serial.print(bus_V, 2);
      Serial.print("V | Corrente: "); Serial.print(curr_mA, 1);
      Serial.print("mA | Potencia: "); Serial.print(pwr_mW, 1);
      Serial.println("mW");
    } else {
      Serial.println("[INA219]  Desconectado.");
    }

    if (hasToF) {
      Serial.print("[VL53L1X] ");
      if (tof.timeoutOccurred()) {
        Serial.println("Timeout!");
      } else if (tof.ranging_data.range_status == 0) {
        Serial.print("Distancia: "); Serial.print(tof.ranging_data.range_mm); Serial.println(" mm");
      } else {
        Serial.print("Leitura Invalida (Status "); Serial.print(tof.ranging_data.range_status); Serial.println(")");
      }
    } else {
      Serial.println("[VL53L1X] Desconectado.");
    }

    if (hasMPU) {
      Serial.print("[MPU6050] Acc[g]: "); Serial.print(ax, 2); Serial.print(", "); Serial.print(ay, 2); Serial.print(", "); Serial.print(az, 2);
      Serial.print(" | Gyr[deg/s]: "); Serial.print(gx, 1); Serial.print(", "); Serial.print(gy, 1); Serial.print(", "); Serial.println(gz, 1);
    } else {
      Serial.println("[MPU6050] Desconectado.");
    }
    
    if (hasPCA) {
      Serial.println("[PCA9685] Canais 0, 1 e 2 oscilando em modo varredura contínua.");
    } else {
      Serial.println("[PCA9685] Desconectado.");
    }

    Serial.println();
  }
}

// ---------------------------------------------------------
// FUNCOES AUXILIARES
// ---------------------------------------------------------

void escanearBarramento() {
  Serial.println(">>> FASE 1: Escaneando Barramento I2C...");
  byte error, address;
  int nDevices = 0;

  for (address = 1; address < 127; address++) {
    Wire.beginTransmission(address);
    error = Wire.endTransmission();

    if (error == 0) {
      Serial.print("    -> Dispositivo encontrado em: 0x");
      if (address < 16) Serial.print("0");
      Serial.print(address, HEX);

      if (address == TOF_ADDR) { Serial.print(" (VL53L1X ToF)"); hasToF = true; }
      else if (address == INA_ADDR) { Serial.print(" (INA219)"); hasINA = true; }
      else if (address == PCA_ADDR) { Serial.print(" (PCA9685)"); hasPCA = true; }
      else if (address == MPU_ADDR) { Serial.print(" (MPU-6050)"); hasMPU = true; }
      
      Serial.println();
      nDevices++;
    }
  }

  if (nDevices == 0) {
    Serial.println("    [!] Nenhum dispositivo encontrado!");
  } else {
    Serial.print("    => Total de "); Serial.print(nDevices); Serial.println(" dispositivos I2C detectados.\n");
  }
}

void inicializarSensores() {
  Serial.println(">>> FASE 2: Inicializando Sensores...");

  if (hasINA) {
    if (ina219.begin(&Wire)) {
      ina219.setCalibration_32V_2A();
      Serial.println("    [OK] INA219 inicializado.");
    } else {
      Serial.println("    [ERRO] INA219 falhou ao iniciar!");
      hasINA = false;
    }
  }

  if (hasPCA) {
    if (pca.begin()) {
      pca.setOscillatorFrequency(27000000);
      pca.setPWMFreq(50);
      Serial.println("    [OK] PCA9685 inicializado a 50Hz.");
    } else {
      Serial.println("    [ERRO] PCA9685 falhou ao iniciar!");
      hasPCA = false;
    }
  }

  if (hasToF) {
    tof.setBus(&Wire);
    tof.setTimeout(500);
    if (tof.init()) {
      tof.setDistanceMode(VL53L1X::Long);
      tof.setMeasurementTimingBudget(50000);
      tof.startContinuous(50);
      Serial.println("    [OK] VL53L1X inicializado no modo Longo (50ms).");
    } else {
      Serial.println("    [ERRO] VL53L1X falhou ao iniciar!");
      hasToF = false;
    }
  }

  if (hasMPU) {
    Wire.beginTransmission(MPU_ADDR);
    Wire.write(0x6B); // Registrador PWR_MGMT_1
    Wire.write(0x00); // 0 acorda o MPU
    if (Wire.endTransmission() == 0) {
      Serial.println("    [OK] MPU-6050 acordado e pronto.");
    } else {
      Serial.println("    [ERRO] MPU-6050 falhou ao acordar!");
      hasMPU = false;
    }
  }
}

void lerMPU6050() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  Wire.endTransmission(false);
  Wire.requestFrom((uint8_t)MPU_ADDR, (uint8_t)14);

  if (Wire.available() >= 14) {
    int16_t rawAx = Wire.read() << 8 | Wire.read();
    int16_t rawAy = Wire.read() << 8 | Wire.read();
    int16_t rawAz = Wire.read() << 8 | Wire.read();
    Wire.read(); Wire.read(); // Ignora Temp
    int16_t rawGx = Wire.read() << 8 | Wire.read();
    int16_t rawGy = Wire.read() << 8 | Wire.read();
    int16_t rawGz = Wire.read() << 8 | Wire.read();

    ax = rawAx / 16384.0; ay = rawAy / 16384.0; az = rawAz / 16384.0;
    gx = rawGx / 131.0; gy = rawGy / 131.0; gz = rawGz / 131.0;
  }
}
