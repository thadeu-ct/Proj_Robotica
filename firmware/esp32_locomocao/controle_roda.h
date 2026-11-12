// ============================================================================
// controle_roda.h — cinematica diferencial e PI de velocidade de UMA roda (relatorio, secoes 1.2 e 1.3).
// C++ puro, sem Arduino: testado no computador contra o modelo de 1a ordem da planta por
// firmware/teste_host/teste_controle.cpp.
// ============================================================================
#pragma once
#include <math.h>

namespace ctrl {

inline float limitar(float x, float lim) { return x > lim ? lim : (x < -lim ? -lim : x); }

// (v [m/s], w [rad/s], w > 0 = esquerda) -> velocidade angular de cada roda [rad/s]
inline void cinematica_inversa(float v, float w, float raio, float bitola, float* w_esq, float* w_dir) {
  *w_esq = (v - w * bitola / 2.0f) / raio;
  *w_dir = (v + w * bitola / 2.0f) / raio;
}

// PI com alimentacao direta, anti-windup por integracao condicional e rampa na referencia.
// Feedforward = inverso exato da planta de 1a ordem: u_ff = (ref + tau * dref/dt) / K. Sem o termo
// tau*dref/dt o integrador acumula o atraso da rampa e da sobressinal (~28% no teste do computador).
// O erro compara a medida com a referencia passada pelo MESMO filtro da medida (media na janela + EMA):
// a velocidade medida chega atrasada ~30 ms; comparar com a referencia "de agora" faz o PI ver um erro
// que e so atraso de medicao e acelerar demais (sobressinal de 19-42% no teste do computador).
// Saida u em [-1, 1] (fracao do PWM, sinal = sentido).
struct PIRoda {
  float kp, ki, k_planta, tau_planta, acel_max, ref_max, zona_morta;
  float fator_ff;        // fracao do feedforward (< 1: se o motor real for mais forte que o modelo, nao passa do ponto)
  int janela_medida;     // mesma janela do MedidorVelocidade
  float alfa_medida;     // mesmo alfa do MedidorVelocidade
  float ref = 0.0f;      // referencia ja com rampa [rad/s]
  float integ = 0.0f;    // integral do erro [rad]
  float u = 0.0f;
  static const int MAX_JANELA = 16;
  float hist_ref[MAX_JANELA] = {0};
  int i_ref = 0;
  float ref_filtrada = 0.0f;

  float passo(float ref_desejada, float w_medida, float dt) {
    ref_desejada = limitar(ref_desejada, ref_max);
    float dmax = acel_max * dt;                                    // rampa de aceleracao
    float dref = limitar(ref_desejada - ref, dmax);
    ref += dref;

    // referencia "como a medida a veria": media das ultimas N referencias + EMA
    int nj = janela_medida < 1 ? 1 : (janela_medida > MAX_JANELA ? MAX_JANELA : janela_medida);
    hist_ref[i_ref] = ref;
    i_ref = (i_ref + 1) % MAX_JANELA;
    float soma = 0.0f;
    for (int k = 1; k <= nj; k++) soma += hist_ref[(i_ref - k + MAX_JANELA) % MAX_JANELA];
    ref_filtrada = alfa_medida * (soma / nj) + (1.0f - alfa_medida) * ref_filtrada;

    if (fabsf(ref) < 1e-3f && fabsf(ref_desejada) < 1e-3f) {      // parado de verdade: zera tudo
      integ = 0.0f;
      u = 0.0f;
      return u;
    }
    float e = ref_filtrada - w_medida;
    float ff = fator_ff * (ref + tau_planta * dref / dt) / k_planta;
    float u_sem_i = ff + kp * e;
    float u_novo = u_sem_i + ki * (integ + e * dt);
    // integracao condicional: so integra se nao estiver saturado, ou se o erro tira da saturacao
    bool satura = fabsf(u_novo) > 1.0f;
    if (!satura || (u_novo > 0) != (e > 0)) integ += e * dt;
    u = limitar(u_sem_i + ki * integ, 1.0f);
    if (zona_morta > 0.0f && fabsf(u) > 1e-3f) u = copysignf(zona_morta + (1.0f - zona_morta) * fabsf(u), u);
    return u;
  }

  void zerar() {
    ref = integ = u = ref_filtrada = 0.0f;
    for (int k = 0; k < MAX_JANELA; k++) hist_ref[k] = 0.0f;
  }
};

// Velocidade da roda: diferenca de contagens numa JANELA das ultimas N amostras (resolucao N vezes
// melhor que so a ultima amostra: a 100 Hz e 720 contagens/volta, 1 contagem em 10 ms = 0,87 rad/s;
// em 50 ms = 0,17 rad/s) e filtro EMA por cima (secao 1.6). Atraso da janela: ~N/2 amostras.
struct MedidorVelocidade {
  static const int MAX_JANELA = 16;
  float contagens_por_volta, alfa;
  int janela;
  long hist[MAX_JANELA] = {0};
  int i = 0, n = 0;
  float w = 0.0f;
  float passo(long contagem_total, float dt) {
    int nj = janela < MAX_JANELA ? janela : MAX_JANELA;
    long antiga = n < nj ? (n ? hist[(i - n + MAX_JANELA) % MAX_JANELA] : contagem_total) : hist[(i - nj + MAX_JANELA) % MAX_JANELA];
    int amostras = n < nj ? n : nj;
    hist[i] = contagem_total;
    i = (i + 1) % MAX_JANELA;
    if (n < MAX_JANELA) n++;
    if (amostras == 0) return w;
    float bruto = (float)(contagem_total - antiga) * 2.0f * (float)M_PI / contagens_por_volta / (dt * amostras);
    w = alfa * bruto + (1.0f - alfa) * w;
    return w;
  }
};

}  // namespace ctrl
