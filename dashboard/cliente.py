"""
Envia dados para o dashboard sem travar quem chama (o envio roda numa thread).

    from cliente import Dashboard
    dash = Dashboard("http://localhost:8000")
    dash.enviar(bateria=87, modo="AUTONOMO")
    dash.enviar(deteccoes=[{"classe": "cilindro", "conf": 0.91, "caixa": [0.1, 0.2, 0.3, 0.5]}])
    dash.enviar(tempo="iniciar")

Se o dashboard estiver fora do ar, os dados sao descartados em silencio.
Chamadas seguidas antes do envio terminar sao juntadas (vale o valor mais novo).
"""
import json
import threading
import urllib.request


class Dashboard:
    def __init__(self, url="http://localhost:8000", timeout=0.5):
        self.url = url.rstrip("/") + "/api/estado"
        self.timeout = timeout
        self._pendente = {}
        self._cond = threading.Condition()
        threading.Thread(target=self._loop, daemon=True).start()

    def enviar(self, **campos):
        with self._cond:
            self._pendente.update(campos)
            self._cond.notify()

    def _loop(self):
        while True:
            with self._cond:
                while not self._pendente:
                    self._cond.wait()
                dados, self._pendente = self._pendente, {}
            req = urllib.request.Request(self.url, json.dumps(dados).encode(),
                                         {"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=self.timeout).close()
            except OSError:
                pass
