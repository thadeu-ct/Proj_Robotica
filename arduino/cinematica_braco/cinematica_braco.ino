#include <Servo.h>
#include <math.h>

Servo servo1;
Servo servo2;

// Pinos dos servos
const int pinoServo1 = 5;
const int pinoServo2 = 3;

// Comprimento dos elos (em cm ou mm)
const float r1 = 15.0;
const float r2 = 13.0;

// Posições atuais dos servos (0 a 180 graus)
int posAtual1 = 90;
int posAtual2 = 90;

struct Ponto {
  float x;
  float y;
  int ang1;
  int ang2;
};

Ponto braco;

// Movimenta os dois servos juntos de forma gradual
void moverSuave(int dest1, int dest2) {
  const int tempoPorPasso = 15; // Velocidade (ms por grau)

  while (posAtual1 != dest1 || posAtual2 != dest2) {
    if (posAtual1 < dest1) posAtual1++;
    else if (posAtual1 > dest1) posAtual1--;

    if (posAtual2 < dest2) posAtual2++;
    else if (posAtual2 > dest2) posAtual2--;

    servo1.write(posAtual1);
    servo2.write(posAtual2);
    delay(tempoPorPasso);
  }
}

// Cinemática Direta: recebe ângulos (graus) e calcula a posição (x, y)
bool cinDireta(Ponto* p) {
  if (p->ang1 < 0 || p->ang1 > 180 || p->ang2 < 0 || p->ang2 > 180) {
    Serial.println("Erro: Angulos fora do limite fisico (0 a 180 graus)!");
    return false;
  }

  // Converte para radianos para as funcoes trigonometricas
  float rad1 = p->ang1 * DEG_TO_RAD;
  float rad2 = p->ang2 * DEG_TO_RAD;

  p->x = r1 * cos(rad1) + r2 * cos(rad1 + rad2);
  p->y = r1 * sin(rad1) + r2 * sin(rad1 + rad2);

  moverSuave(p->ang1, p->ang2);

  Serial.println("--- Cinematica Direta Executada ---");
  Serial.print("Angulos: Theta1 = "); Serial.print(p->ang1);
  Serial.print(" deg, Theta2 = "); Serial.print(p->ang2); Serial.println(" deg");
  Serial.print("Posicao resultante: X = "); Serial.print(p->x);
  Serial.print(" | Y = "); Serial.println(p->y);
  return true;
}

// Cinemática Inversa: recebe (x, y) e calcula os ângulos dos servos
bool cinInversa(Ponto* p) {
  float raio = sqrt(p->x * p->x + p->y * p->y);
  float r_max = r1 + r2;
  float r_min = fabs(r1 - r2);

  if (raio > (r_max - 0.5) || raio < (r_min + 0.5)) {
    Serial.println("Erro: Ponto fora da area de alcance do braco!");
    return false;
  }

  // Lei dos Cossenos para o angulo theta2
  float cos_theta2 = (p->x * p->x + p->y * p->y - r1 * r1 - r2 * r2) / (2.0
* r1 * r2);

  // Limita o valor entre -1 e 1 para evitar erros de ponto flutuante
  cos_theta2 = constrain(cos_theta2, -1.0, 1.0);

  float sen_theta2 = sqrt(1.0 - (cos_theta2 * cos_theta2));
  float theta2 = atan2(sen_theta2, cos_theta2);

  // Calculo de theta1
  float k1 = r1 + r2 * cos_theta2;
  float k2 = r2 * sen_theta2;
  float theta1 = atan2(p->y, p->x) - atan2(k2, k1);

  // Conversao para graus
  int ang1_calc = round(theta1 * RAD_TO_DEG);
  int ang2_calc = round(theta2 * RAD_TO_DEG);

  // Verificacao dos limites fisicos dos servos
  if (ang1_calc < 0 || ang1_calc > 180 || ang2_calc < 0 || ang2_calc > 180)
{
    Serial.print("Erro: Angulos calculados fora de alcance [0-180]: ");
    Serial.print("Ang1: "); Serial.print(ang1_calc);
    Serial.print(" | Ang2: "); Serial.println(ang2_calc);
    return false;
  }

  p->ang1 = ang1_calc;
  p->ang2 = ang2_calc;

  moverSuave(p->ang1, p->ang2);

  Serial.println("--- Cinematica Inversa Executada ---");
  Serial.print("Destino: X = "); Serial.print(p->x);
  Serial.print(" | Y = "); Serial.println(p->y);
  Serial.print("Angulos calculados: Theta1 = "); Serial.print(p->ang1);
  Serial.print(" deg, Theta2 = "); Serial.print(p->ang2); Serial.println(" deg");
  return true;
}

void setup() {
  Serial.begin(9600);
  servo1.attach(pinoServo1);
  servo2.attach(pinoServo2);

  // Inicializa servos na posicao inicial
  servo1.write(posAtual1);
  servo2.write(posAtual2);

  Serial.println("Pronto! Envie comandos no formato:");
  Serial.println("  Direta:  d;t1;t2  (Ex: d;45;60)");
  Serial.println("  Inversa: i;x;y    (Ex: i;18.5;10.2)");
}

void loop() {
  if (Serial.available() > 0) {
    String input = Serial.readStringUntil('\n');
    input.trim(); // Remove espacos extras e quebras de linha

    if (input.length() < 5) return;

    char modo = input.charAt(0);
    int primeiroPontoVirgula = input.indexOf(';');
    int segundoPontoVirgula = input.indexOf(';', primeiroPontoVirgula + 1);

    if (primeiroPontoVirgula == -1 || segundoPontoVirgula == -1) {
      Serial.println("Formato invalido! Use d;t1;t2 ou i;x;y");
      return;
    }

    float val1 = input.substring(primeiroPontoVirgula + 1,
segundoPontoVirgula).toFloat();
    float val2 = input.substring(segundoPontoVirgula + 1).toFloat();

    if (modo == 'd' || modo == 'D') {
      braco.ang1 = (int)val1;
      braco.ang2 = (int)val2;
      cinDireta(&braco);
    }
    else if (modo == 'i' || modo == 'I') {
      braco.x = val1;
      braco.y = val2;
      cinInversa(&braco);
    }
    else {
      Serial.println("Modo desconhecido. Use 'd' para direta ou 'i' para inversa.");
    }
  }
}
