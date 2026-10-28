int contagemA = 0;
int contagemB = 0;
float PPR = 180;
float voltapS;

float velocidade;

void funcaoA () {
  contagemA++;
}

void funcaoB () {
  contagemB++;
}

void setup() {
  Serial.begin(9600);
  int pinoM1A = 21;
  int pinoM1B = 20;
  pinMode(17, OUTPUT);
  pinMode(16, OUTPUT);
  digitalWrite(17, HIGH);
  digitalWrite(16, LOW);

  attachInterrupt(digitalPinToInterrupt(pinoM1A), funcaoA, RISING);
  attachInterrupt(digitalPinToInterrupt(pinoM1B), funcaoB, RISING);

  int E_motorA = 8;
  pinMode(E_motorA, OUTPUT);


  int pin1 = 10;
  int pin2 = 11;
  pinMode(pin1, OUTPUT); //E1
  pinMode(pin2, OUTPUT); //E2
  digitalWrite(pin1, HIGH);
  digitalWrite(pin2, LOW);
}

unsigned long tempoAnterior = 0;
const long deltaT = 1000;

int contagemAtual = 0;
int contagemAnterior = 0;
int deltaContagem = 0;
unsigned long tempoAtual;
float kI = 0.05;
float kP = 0.01;
float soma;
float u = 0;
float e;
float pwm;



void loop() {
  tempoAtual = millis();
  if (tempoAtual - tempoAnterior >= deltaT) {
    tempoAnterior = tempoAtual;
    contagemAtual = contagemA;
    deltaContagem = contagemAtual - contagemAnterior;
    contagemAnterior = contagemAtual;
    voltapS = (deltaContagem/PPR)*60;
    }

  Serial.print("RPM: ");
    Serial.print(voltapS);
      e = 120 - voltapS;
      soma = soma + e;
      u = kP * e + kI * soma;
      //pwm = map(u, fromLow, fromHigh, toLow, toHigh)

      //if (u < 0) u=0;

    analogWrite(8, u);

    Serial.print(",u: ");
    Serial.print(u);
    Serial.print(",e: ");
    Serial.print(e);
    Serial.print(",soma: ");
    Serial.println(soma);

}
