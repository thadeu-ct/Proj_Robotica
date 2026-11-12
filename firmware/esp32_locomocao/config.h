// ============================================================================
// config.h — TUDO que se ajusta no firmware de locomocao fica aqui.
// Pinos: padrao da eletronica (eletronica/Programacao/DRV8833.ino, commit a5f0ba3, e
// eletronica/ESP32 DevKit/pinout.md). Itens marcados MEDIR/CONFIRMAR ainda sao estimativas.
// Depois de mudar, compile e grave de novo.
// ============================================================================
#pragma once

// ---------------------------------------------------------------- comunicacao com o Pi
// USB do ESP32 (aparece no Pi como /dev/ttyUSB0 ou /dev/ttyACM0). Precisa bater com
// controle/config_seguir.json ("serial", "baud").
#define BAUD_PI            460800
// 0 = USB (Serial). 1 = UART2 nos GPIO 16 (RX2) / 17 (TX2), como no relatorio (secao 5.2).
#define USAR_UART2         0
#define PINO_UART2_RX      16
#define PINO_UART2_TX      17

// ---------------------------------------------------------------- ponte H DRV8833
#define MOTOR_A_IN1        12   // atencao: GPIO 12 e pino de boot do ESP32; nao pode estar em HIGH ao ligar
#define MOTOR_A_IN2        13
#define MOTOR_B_IN1        18
#define MOTOR_B_IN2        19
#define PINO_NSLEEP        14   // HIGH = ponte H ligada; LOW = desligada (ESPERA)
#define PWM_FREQ_HZ        20000  // relatorio: 20 kHz (fora da faixa audivel)
#define PWM_BITS           10

// ---------------------------------------------------------------- encoders NXT
#define ENC_A_CH1          34   // GPIO 34-39: so entrada, sem pull-up interno
#define ENC_A_CH2          35
#define ENC_B_CH1          36
#define ENC_B_CH2          39
#define PINO_ENC_VCC       32   // pinout.md: alimentacao dos encoders por GPIO (HIGH)
#define PINO_ENC_GND       33   //            referencia (LOW)
// Contagens por volta da RODA em quadratura x4 (todas as bordas dos dois canais).
// arduino/pid_pwm/RPM.ino usa 180 pulsos/volta num canal (so subida) -> x4 = 720. MEDIR com
// MODO_BANCADA (gire a roda uma volta a mao) e confirme se nao ha reducao entre motor e roda.
#define CONTAGENS_POR_VOLTA  720.0f

// ---------------------------------------------------------------- qual motor e qual roda
// MOTOR_ESQ/MOTOR_DIR: 0 = motor A, 1 = motor B. CONFIRMAR com MODO_BANCADA.
#define MOTOR_ESQ          0
#define MOTOR_DIR          1
// Se a roda gira para tras com comando positivo, inverta o motor. Se o encoder conta negativo
// com a roda indo para frente, inverta o encoder. Indice = motor (0 = A, 1 = B).
static const bool INVERTE_MOTOR[2]   = {false, true};   // motores espelhados no chassi: um deles costuma ser invertido
static const bool INVERTE_ENCODER[2] = {false, true};

// ---------------------------------------------------------------- geometria (MEDIR)
#define RAIO_RODA_M        0.028f   // roda NXT de 56 mm de diametro (estimativa)
#define BITOLA_M           0.120f   // distancia entre os centros das rodas (estimativa)

// ---------------------------------------------------------------- controle PI por roda (relatorio, secao 1.3)
// Planta G(s) = K/(tau s + 1), u em [-1, 1] (fracao do PWM), saida em rad/s da roda.
#define K_PLANTA           17.8f    // rad/s por unidade de duty (relatorio; identificar no degrau)
#define TAU_PLANTA         0.10f    // constante de tempo [s] (relatorio; identificar no degrau)
#define FATOR_FF           0.8f     // fracao do feedforward: 0,8 tolera motor ate ~35% mais forte que o modelo sem passar do ponto
#define KP                 0.0375f  // s/rad
#define KI                 0.375f   // 1/rad
#define PERIODO_CONTROLE_MS 10      // 100 Hz
#define JANELA_VELOCIDADE  5        // amostras (5 x 10 ms = 50 ms) para medir a velocidade da roda
#define ALFA_EMA           0.5f     // filtro EMA por cima da janela (secao 1.6)
#define ACEL_MAX_RAD_S2    40.0f    // limite de variacao da referencia de cada roda (rampa)
#define W_RODA_MAX_RAD_S   16.0f    // satura a referencia (motor NXT a 9 V, ~150 rpm)
#define ZONA_MORTA_DUTY    0.0f     // duty minimo que vence o atrito (MEDIR; 0 = desligado)

// ---------------------------------------------------------------- seguranca
#define WATCHDOG_MS        250      // sem quadro valido por isso -> ESPERA (relatorio, secao 5.12)
#define PERIODO_TELEMETRIA_MS 20    // 50 Hz

// ---------------------------------------------------------------- teste de bancada
// 1 = em vez do protocolo, roda um teste em TEXTO no Monitor Serial (115200): gira cada motor
// para frente e para tras e mostra os encoders. Use com o robo SUSPENSO para conferir
// MOTOR_ESQ/DIR, INVERTE_MOTOR, INVERTE_ENCODER e CONTAGENS_POR_VOLTA.
#define MODO_BANCADA       0
