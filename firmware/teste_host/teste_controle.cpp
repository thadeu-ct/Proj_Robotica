// Simula o PI de controle_roda.h contra a planta do relatorio G(s) = K/(tau s + 1) com encoder quantizado.
#include <cmath>
#include <cstdio>
#include "../esp32_locomocao/controle_roda.h"
#include "../esp32_locomocao/config.h"

struct Planta { float K, tau, w = 0, ang = 0; void passo(float u, float dt) { w += dt / tau * (K * u - w); ang += w * dt; } };

static int ensaio(float ref, float K_real, float tau_real, const char* nome) {
  ctrl::PIRoda pi{KP, KI, K_PLANTA, TAU_PLANTA, ACEL_MAX_RAD_S2, W_RODA_MAX_RAD_S, ZONA_MORTA_DUTY,
                  FATOR_FF, JANELA_VELOCIDADE, ALFA_EMA};
  ctrl::MedidorVelocidade med{CONTAGENS_POR_VOLTA, ALFA_EMA, JANELA_VELOCIDADE};
  Planta p{K_real, tau_real};
  const float dt = PERIODO_CONTROLE_MS / 1000.0f, sub = 0.0005f;
  float pico = 0, t95 = -1, soma_err = 0; int n_err = 0;
  for (int k = 0; k < 300; k++) {                       // 3 s
    long cont = (long)floorf(p.ang / (2 * M_PI) * CONTAGENS_POR_VOLTA);
    float w = med.passo(cont, dt);
    float u = pi.passo(ref, w, dt);
    for (long j = 0; j < lroundf(dt / sub); j++) p.passo(u, sub);   // lround: (int)(0.01/0.0005) dava 19
    float t = k * dt;
    if (fabsf(p.w) > fabsf(pico)) pico = p.w;
    if (t95 < 0 && fabsf(p.w - ref) < 0.05f * fabsf(ref)) t95 = t;
    if (t > 2.0f) { soma_err += fabsf(p.w - ref); n_err++; }
  }
  float erro = soma_err / n_err / fabsf(ref) * 100, sobre = (fabsf(pico) - fabsf(ref)) / fabsf(ref) * 100;
  bool ok = erro < 3.0f && sobre < 15.0f && t95 > 0 && t95 < 1.0f;
  printf("%-34s ref %+6.1f rad/s: 95%% em %.2f s, sobressinal %4.1f%%, erro em regime %.2f%%  %s\n",
         nome, ref, t95, sobre, erro, ok ? "ok" : "FALHOU");
  return ok ? 0 : 1;
}

int main() {
  int f = 0;
  f += ensaio(8.0f, 17.8f, 0.10f, "planta do relatorio");
  f += ensaio(-8.0f, 17.8f, 0.10f, "planta do relatorio, re");
  f += ensaio(3.0f, 17.8f, 0.10f, "baixa velocidade");
  f += ensaio(8.0f, 12.0f, 0.15f, "motor real 33% mais fraco/lento");
  f += ensaio(8.0f, 24.0f, 0.07f, "motor real 35% mais forte/rapido");
  float we, wd;
  ctrl::cinematica_inversa(0.10f, 0.5f, RAIO_RODA_M, BITOLA_M, &we, &wd);
  bool ck = fabsf(we - (0.10f - 0.5f * BITOLA_M / 2) / RAIO_RODA_M) < 1e-4f && wd > we;
  printf("cinematica v=0.10 w=+0.5 (esquerda): roda esq %.2f, dir %.2f rad/s  %s\n", we, wd, ck ? "ok" : "FALHOU");
  return f + !ck;
}
