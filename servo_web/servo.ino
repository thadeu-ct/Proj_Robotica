#include <Servo.h>
#include <math.h>

Servo servo1;
Servo servo2;

struct Ponto { float x, y; int ang1, ang2; };

const float r1 = 15.0;
const float r2 = 13.0;
const float R_MIN = 12.0;
const float R_MAX = 27.0;
const int PINO1 = 6;
const int PINO2 = 4;

char buffer[40];
int indice = 0;

float rad(float graus) { return graus * PI / 180.0; }

bool cinDireta(Ponto *p) {
  if (p->ang1 < 0 || p->ang1 > 180 || p->ang2 < 0 || p->ang2 > 180) {
    Serial.println("ERRO: angulos invalidos");
    return false;
  }
  float t1 = rad(p->ang1), t2 = rad(p->ang2);
  p->x = r1 * cos(t1) + r2 * cos(t1 + t2);
  p->y = r1 * sin(t1) + r2 * sin(t1 + t2);
  servo1.write(p->ang1);
  servo2.write(p->ang2);
  return true;
}

bool cinInversa(Ponto *p) {
  float x = p->x, y = p->y;
  float raio = sqrt(x*x + y*y);
  if (raio < R_MIN || raio > R_MAX) {
    Serial.println("ERRO: fora do alcance");
    return false;
  }
  float c2 = (x*x + y*y - r1*r1 - r2*r2) / (2.0 * r1 * r2);
  c2 = constrain(c2, -1.0, 1.0);
  float s2 = sqrt(max(0.0f, 1.0f - c2*c2));
  float t2 = atan2(s2, c2);
  float t1 = atan2(y, x) - atan2(r2*s2, r1+r2*c2);
  p->ang1 = round(t1 * 180.0 / PI);
  p->ang2 = round(t2 * 180.0 / PI);
  if (p->ang1 < 0 || p->ang1 > 180 || p->ang2 < 0 || p->ang2 > 180) {
    Serial.println("ERRO: limites das juntas");
    return false;
  }
  servo1.write(p->ang1);
  servo2.write(p->ang2);
  return true;
}

bool movimenta(bool direta, Ponto *p) {
  return direta ? cinDireta(p) : cinInversa(p);
}

void processar(char *linha) {
  int modo;
  float v1, v2;
  char extra;
  if (sscanf(linha, " %d , %f , %f %c", &modo, &v1, &v2, &extra) != 3 ||
      (modo != 0 && modo != 1) || !isfinite(v1) || !isfinite(v2)) {
    Serial.println("ERRO: formato");
    return;
  }
  Ponto p = {0, 0, 0, 0};
  if (modo == 1) {
    if (v1 < 0 || v1 > 180 || v2 < 0 || v2 > 180) {
      Serial.println("ERRO: angulos invalidos");
      return;
    }
    p.ang1 = round(v1);
    p.ang2 = round(v2);
  } else {
    p.x = v1;
    p.y = v2;
  }
  if (movimenta(modo == 1, &p)) {
    Serial.print("OK: ");
    Serial.print(p.ang1); Serial.print(",");
    Serial.print(p.ang2); Serial.print(",");
    Serial.print(p.x, 2); Serial.print(",");
    Serial.println(p.y, 2);
  }
}

void setup() {
  Serial.begin(9600);
  servo1.attach(PINO1);
  servo2.attach(PINO2);
  servo1.write(90);
  servo2.write(90);
  Serial.println("Arduino pronto");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      buffer[indice] = '\0';
      processar(buffer);
      indice = 0;
    } else if (c != '\r') {
      if (indice < sizeof(buffer) - 1) buffer[indice++] = c;
      else { indice = 0; Serial.println("ERRO: comando longo"); }
    }
  }
}
