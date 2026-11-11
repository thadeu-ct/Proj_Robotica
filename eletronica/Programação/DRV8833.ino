/** Teste de acionamento da Ponte H DRV8833 com leitura de Encoders (Motor Lego NXT).
 *  Executa rampa de aceleração e desaceleração via PWM enquanto contabiliza
 *  os pulsos de quadratura dos encoders ópticos por interrupção de hardware.
 */

#include <Arduino.h>

// Pinos da Ponte H DRV8833
#define MOTOR_A_IN1 12
#define MOTOR_A_IN2 13
#define MOTOR_B_IN1 18
#define MOTOR_B_IN2 19
#define DRV_STBY_1  32  // Pino nSLEEP / Enable
#define DRV_STBY_2  33  // Referência GND / Controle auxiliar

// Pinos dos Encoders (GPIOs 34-39 no ESP32 são Input-Only, ideais para sensores)
#define ENC_A_CH1   34
#define ENC_A_CH2   35
#define ENC_B_CH1   36
#define ENC_B_CH2   39

#define sleep   14

// 'volatile' garante leitura segura entre a ISR e o loop principal
volatile long cont = 0;

// 'IRAM_ATTR' mantém a rotina na RAM rápida do ESP32, evitando crash de cache
void IRAM_ATTR ISR() {
  cont++;
}

void setup() {
  Serial.begin(115200);

  // Configuração dos pinos de potência da Ponte H
  pinMode(MOTOR_A_IN1, OUTPUT);
  pinMode(MOTOR_A_IN2, OUTPUT);
  pinMode(MOTOR_B_IN1, OUTPUT);
  pinMode(MOTOR_B_IN2, OUTPUT);

  pinMode(sleep, OUTPUT);

  // Configuração dos pinos de leitura dos encoders
  // Nota: GPIOs 34 a 39 não possuem pull-up interno; requerem sinal ativo ou pull-up externo
  pinMode(ENC_A_CH1, INPUT);
  pinMode(ENC_A_CH2, INPUT);
  pinMode(ENC_B_CH1, INPUT);
  pinMode(ENC_B_CH2, INPUT);

  attachInterrupt(digitalPinToInterrupt(ENC_A_CH1), ISR, RISING);
  attachInterrupt(digitalPinToInterrupt(ENC_A_CH2), ISR, RISING);
  attachInterrupt(digitalPinToInterrupt(ENC_B_CH1), ISR, RISING);
  attachInterrupt(digitalPinToInterrupt(ENC_B_CH2), ISR, RISING);

  // Habilita o driver DRV8833
  pinMode(DRV_STBY_1, OUTPUT);
  pinMode(DRV_STBY_2, OUTPUT);
  digitalWrite(DRV_STBY_2, LOW);
  digitalWrite(DRV_STBY_1, HIGH);
}

void loop() {
  // Acelera o motor gradualmente (duty cycle de 80 a 255)
  for (int velocidade = 80; velocidade <= 255; velocidade++) {
    digitalWrite(MOTOR_A_IN2, HIGH);
    analogWrite(MOTOR_A_IN1, velocidade);
    // digitalWrite(MOTOR_B_IN1, HIGH);
    // analogWrite(MOTOR_B_IN2, velocidade);
    digitalWrite(sleep, HIGH);
    Serial.println(cont);
    delay(10);
  }

  // Desacelera o motor gradualmente (duty cycle de 255 a 80)
  for (int velocidade = 255; velocidade >= 80; velocidade--) {
    analogWrite(MOTOR_A_IN1, velocidade);
    digitalWrite(MOTOR_A_IN2, HIGH);
    // analogWrite(MOTOR_B_IN1, velocidade);
    // digitalWrite(MOTOR_B_IN2, HIGH);
    digitalWrite(sleep, HIGH);
    Serial.println(cont);
    delay(10);
  }
}
