// ============================================================================
// esp32_locomocao.ino — locomocao do rover: recebe (v, w) do Raspberry Pi e controla as duas rodas.
//
//   Pi (controle/seguir_tag.py ou mqtt/robo_agente.py) --CMD_VEL/MODE pela serial--> ESP32
//   ESP32: cinematica inversa -> PI de velocidade por roda a 100 Hz (encoders NXT) -> DRV8833
//   ESP32 --TELEMETRIA 50 Hz--> Pi (encoders, velocidades, modo, falhas)
//
// Seguranca: liga a ponte H (nSLEEP) so em modo AUTONOMO/MANUAL. Sem quadro valido por WATCHDOG_MS
// (250 ms) -> zera, desliga a ponte e vai para ESPERA; volta so quando o Pi mandar MODE de novo.
//
// Ajustes: config.h. Protocolo: protocolo.h (= controle/protocolo_esp32.py). PI: controle_roda.h.
// Base: eletronica/Programacao/DRV8833.ino (pinos e ligacao do padrao da eletronica).
// Placa: "ESP32 Dev Module" (core esp32 da Espressif 2.x ou 3.x).
// Primeiro uso: MODO_BANCADA 1 no config.h, robo suspenso, Monitor Serial a 115200.
// ============================================================================
#include <Arduino.h>
#include <soc/gpio_reg.h>
#include "esp_arduino_version.h"
#include "config.h"
#include "protocolo.h"
#include "controle_roda.h"

#if USAR_UART2
#define PORTA Serial2
#else
#define PORTA Serial
#endif

static const uint8_t PINO_IN1[2] = {MOTOR_A_IN1, MOTOR_B_IN1};
static const uint8_t PINO_IN2[2] = {MOTOR_A_IN2, MOTOR_B_IN2};
DRAM_ATTR static const uint8_t PINO_CH1[2] = {ENC_A_CH1, ENC_B_CH1};
DRAM_ATTR static const uint8_t PINO_CH2[2] = {ENC_A_CH2, ENC_B_CH2};
static const uint32_t PWM_MAX = (1u << PWM_BITS) - 1;

// ---------------------------------------------------------------- PWM (API do LEDC mudou no core 3.x)
static void pwm_iniciar(uint8_t pino, uint8_t canal) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  (void)canal;
  ledcAttach(pino, PWM_FREQ_HZ, PWM_BITS);
#else
  ledcSetup(canal, PWM_FREQ_HZ, PWM_BITS);
  ledcAttachPin(pino, canal);
#endif
}
static void pwm_escrever(uint8_t pino, uint8_t canal, uint32_t duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  (void)canal;
  ledcWrite(pino, duty);
#else
  (void)pino;
  ledcWrite(canal, duty);
#endif
}
// canal LEDC de cada pino (core 2.x): motor m -> IN1 = 2m, IN2 = 2m+1
static uint8_t canal(uint8_t m, uint8_t in) { return (uint8_t)(2 * m + in); }

// u em [-1, 1]. Slow decay (freio entre pulsos), como no DRV8833.ino: melhor controle em baixa velocidade.
// DRV8833: IN1=1, IN2=0 -> frente; IN1=1, IN2=1 -> freio. Frente com duty u: IN1 fixo em 1 e IN2 em 1 durante (1-u).
static void acionar(uint8_t m, float u) {
  if (INVERTE_MOTOR[m]) u = -u;
  u = ctrl::limitar(u, 1.0f);
  uint32_t d = (uint32_t)(fabsf(u) * PWM_MAX + 0.5f);
  if (u >= 0) {
    pwm_escrever(PINO_IN1[m], canal(m, 0), PWM_MAX);
    pwm_escrever(PINO_IN2[m], canal(m, 1), PWM_MAX - d);
  } else {
    pwm_escrever(PINO_IN1[m], canal(m, 0), PWM_MAX - d);
    pwm_escrever(PINO_IN2[m], canal(m, 1), PWM_MAX);
  }
}
static void soltar_motores() {          // IN1 = IN2 = 0: roda livre (com nSLEEP baixo, ponte desligada)
  for (uint8_t m = 0; m < 2; m++) {
    pwm_escrever(PINO_IN1[m], canal(m, 0), 0);
    pwm_escrever(PINO_IN2[m], canal(m, 1), 0);
  }
}

// ---------------------------------------------------------------- encoders: quadratura x4 por interrupcao
volatile int32_t contagem[2] = {0, 0};
volatile uint8_t estado_enc[2] = {0, 0};
// tabela [estado_anterior<<2 | estado_novo] -> +1, -1 ou 0 (transicao invalida/sem mudanca)
DRAM_ATTR static const int8_t TAB_QUAD[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};

// GPIO 32-39 ficam no registrador GPIO_IN1; leitura direta e segura dentro da interrupcao
static inline uint8_t IRAM_ATTR ler_pino_alto(uint8_t pino) { return (REG_READ(GPIO_IN1_REG) >> (pino - 32)) & 1; }

static void IRAM_ATTR isr_encoder(uint8_t m) {
  uint8_t s = (uint8_t)((ler_pino_alto(PINO_CH1[m]) << 1) | ler_pino_alto(PINO_CH2[m]));
  contagem[m] += TAB_QUAD[(estado_enc[m] << 2) | s];
  estado_enc[m] = s;
}
static void IRAM_ATTR isr_enc_a() { isr_encoder(0); }
static void IRAM_ATTR isr_enc_b() { isr_encoder(1); }

static int32_t ler_contagem(uint8_t m) {
  noInterrupts();
  int32_t c = contagem[m];
  interrupts();
  return INVERTE_ENCODER[m] ? -c : c;
}

// ---------------------------------------------------------------- estado
static proto::Leitor leitor;
static ctrl::PIRoda pi[2];
static ctrl::MedidorVelocidade vel[2];
static uint8_t modo = proto::MODO_ESPERA;
static uint8_t falhas = 0;
static float v_cmd = 0.0f, w_cmd = 0.0f;       // ultimo CMD_VEL [m/s, rad/s]
static uint32_t t_ultimo_quadro = 0;
static uint8_t seq_tx = 0;

static void entrar_espera() {
  modo = proto::MODO_ESPERA;
  v_cmd = w_cmd = 0.0f;
  for (uint8_t m = 0; m < 2; m++) pi[m].zerar();
  soltar_motores();
  digitalWrite(PINO_NSLEEP, LOW);
}

static void tratar_quadro(const proto::Quadro& q) {
  t_ultimo_quadro = millis();
  falhas &= (uint8_t)~proto::FALHA_WATCHDOG;
  switch (q.tipo) {
    case proto::CMD_VEL:
      if (q.len >= 4 && modo != proto::MODO_ESPERA) {   // em ESPERA o comando e ignorado
        v_cmd = proto::le_i16(q.carga) / 1000.0f;
        w_cmd = proto::le_i16(q.carga + 2) / 1000.0f;
      }
      break;
    case proto::MODE:
      if (q.len >= 1) {
        if (q.carga[0] == proto::MODO_ESPERA) {
          entrar_espera();
        } else if (q.carga[0] == proto::MODO_AUTONOMO || q.carga[0] == proto::MODO_MANUAL) {
          if (modo == proto::MODO_ESPERA) { for (uint8_t m = 0; m < 2; m++) pi[m].zerar(); }
          modo = q.carga[0];
          digitalWrite(PINO_NSLEEP, HIGH);
        }
      }
      break;
    default:                                            // HEARTBEAT e outros: so alimentam o watchdog
      break;
  }
}

static void enviar_telemetria() {
  uint8_t carga[proto::TAM_TELEMETRIA];
  uint8_t quadro[7 + proto::TAM_TELEMETRIA];
  proto::carga_telemetria(carga, ler_contagem(MOTOR_ESQ), ler_contagem(MOTOR_DIR),
                          vel[MOTOR_ESQ].w * RAIO_RODA_M, vel[MOTOR_DIR].w * RAIO_RODA_M,
                          0 /* bateria: INA219 ainda nao integrado */, modo, falhas, 0.0f, 0);
  size_t n = proto::montar(quadro, proto::TELEMETRIA, seq_tx++, carga, proto::TAM_TELEMETRIA);
  if ((size_t)PORTA.availableForWrite() >= n) PORTA.write(quadro, n);   // nunca bloqueia o controle
}

// ---------------------------------------------------------------- teste de bancada (MODO_BANCADA 1)
#if MODO_BANCADA
static void teste_bancada() {
  Serial.begin(115200);
  delay(500);
  digitalWrite(PINO_NSLEEP, HIGH);
  const char* nome[2] = {"A (IN 12/13, enc 34/35)", "B (IN 18/19, enc 36/39)"};
  for (;;) {
    for (uint8_t m = 0; m < 2; m++) {
      for (int sentido = 1; sentido >= -1; sentido -= 2) {
        int32_t c0 = ler_contagem(m);
        Serial.printf("\nMotor %s, comando %s 40%% por 2 s (roda %s)...\n", nome[m], sentido > 0 ? "+" : "-",
                      m == MOTOR_ESQ ? "ESQUERDA" : "DIREITA");
        uint32_t t0 = millis();
        while (millis() - t0 < 2000) { acionar(m, 0.4f * sentido); delay(10); }
        acionar(m, 0.0f);
        delay(300);
        int32_t d = ler_contagem(m) - c0;
        Serial.printf("  contagens: %ld  (%.2f voltas com %.0f contagens/volta)\n", (long)d, d / CONTAGENS_POR_VOLTA,
                      CONTAGENS_POR_VOLTA);
        Serial.println(sentido > 0 ? "  -> com '+' a roda deve ir para FRENTE e as contagens devem ser POSITIVAS"
                                   : "  -> com '-' a roda deve ir para TRAS e as contagens devem ser NEGATIVAS");
      }
    }
    soltar_motores();
    Serial.println("\nGire cada roda UMA volta para frente, a mao, nos proximos 10 s:");
    int32_t a0 = ler_contagem(0), b0 = ler_contagem(1);
    delay(10000);
    Serial.printf("  motor A: %ld contagens | motor B: %ld contagens (use em CONTAGENS_POR_VOLTA)\n",
                  (long)(ler_contagem(0) - a0), (long)(ler_contagem(1) - b0));
    Serial.println("\nRepetindo em 5 s...");
    delay(5000);
  }
}
#endif

// ---------------------------------------------------------------- setup / loop
void setup() {
  pinMode(PINO_NSLEEP, OUTPUT);
  digitalWrite(PINO_NSLEEP, LOW);            // ponte desligada ate o Pi mandar MODE
  pinMode(PINO_ENC_VCC, OUTPUT);
  pinMode(PINO_ENC_GND, OUTPUT);
  digitalWrite(PINO_ENC_GND, LOW);
  digitalWrite(PINO_ENC_VCC, HIGH);

  for (uint8_t m = 0; m < 2; m++) {
    pwm_iniciar(PINO_IN1[m], canal(m, 0));
    pwm_iniciar(PINO_IN2[m], canal(m, 1));
    pinMode(PINO_CH1[m], INPUT);
    pinMode(PINO_CH2[m], INPUT);
    estado_enc[m] = (uint8_t)((digitalRead(PINO_CH1[m]) << 1) | digitalRead(PINO_CH2[m]));
    pi[m] = ctrl::PIRoda{KP, KI, K_PLANTA, TAU_PLANTA, ACEL_MAX_RAD_S2, W_RODA_MAX_RAD_S, ZONA_MORTA_DUTY,
                         FATOR_FF, JANELA_VELOCIDADE, ALFA_EMA};
    vel[m] = ctrl::MedidorVelocidade{CONTAGENS_POR_VOLTA, ALFA_EMA, JANELA_VELOCIDADE};
  }
  soltar_motores();
  attachInterrupt(digitalPinToInterrupt(ENC_A_CH1), isr_enc_a, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENC_A_CH2), isr_enc_a, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENC_B_CH1), isr_enc_b, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENC_B_CH2), isr_enc_b, CHANGE);

#if MODO_BANCADA
  teste_bancada();                            // nao volta
#endif

#if USAR_UART2
  PORTA.begin(BAUD_PI, SERIAL_8N1, PINO_UART2_RX, PINO_UART2_TX);
#else
  PORTA.begin(BAUD_PI);
#endif
  entrar_espera();
  t_ultimo_quadro = millis();
}

void loop() {
  // 1) protocolo: le tudo que chegou
  while (PORTA.available()) {
    if (leitor.alimentar((uint8_t)PORTA.read())) tratar_quadro(leitor.quadro());
  }

  uint32_t agora = millis();

  // 2) watchdog: sem quadro valido -> ESPERA
  if (agora - t_ultimo_quadro > WATCHDOG_MS) {
    if (modo != proto::MODO_ESPERA) entrar_espera();
    falhas |= proto::FALHA_WATCHDOG;
  }

  // 3) controle a 100 Hz
  static uint32_t t_ctrl = 0;
  if (agora - t_ctrl >= PERIODO_CONTROLE_MS) {
    float dt = (agora - t_ctrl) / 1000.0f;
    if (t_ctrl == 0 || dt > 0.1f) dt = PERIODO_CONTROLE_MS / 1000.0f;
    t_ctrl = agora;
    float ref[2];
    ctrl::cinematica_inversa(v_cmd, w_cmd, RAIO_RODA_M, BITOLA_M, &ref[MOTOR_ESQ], &ref[MOTOR_DIR]);
    for (uint8_t m = 0; m < 2; m++) {
      float w = vel[m].passo(ler_contagem(m), dt);
      if (modo == proto::MODO_ESPERA) continue;
      acionar(m, pi[m].passo(ref[m], w, dt));
    }
  }

  // 4) telemetria a 50 Hz
  static uint32_t t_tel = 0;
  if (agora - t_tel >= PERIODO_TELEMETRIA_MS) {
    t_tel = agora;
    enviar_telemetria();
  }
}
