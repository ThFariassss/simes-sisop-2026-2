from collections import deque

from simes import parametros as P


def cilindro_do_bloco(bloco):
    return bloco // P.BLOCOS_POR_CILINDRO


class DiscoHDD:
    nome = "hdd"

    def __init__(self, busca_minima_ms=P.BUSCA_MINIMA_MS,
                 busca_por_cilindro_ms=P.BUSCA_POR_CILINDRO_MS,
                 latencia_rotacional_ms=P.LATENCIA_ROTACIONAL_MS,
                 transferencia_ms_por_bloco=P.TRANSFERENCIA_MS_POR_BLOCO,
                 cilindros=P.CILINDROS, cilindro_inicial=0, nome=None):
        self.busca_minima_ms = busca_minima_ms
        self.busca_por_cilindro_ms = busca_por_cilindro_ms
        self.latencia_rotacional_ms = latencia_rotacional_ms
        self.transferencia_ms_por_bloco = transferencia_ms_por_bloco
        self.cilindros = cilindros
        self.cilindro_atual = cilindro_inicial
        self.ultimo_bloco = None
        self.deslocamento_total = 0
        if nome:
            self.nome = nome

    def tempo_busca(self, distancia):
        if distancia == 0:
            return 0.0
        return self.busca_minima_ms + distancia * self.busca_por_cilindro_ms

    def mover(self, cilindro):
        distancia = abs(cilindro - self.cilindro_atual)
        duracao = self.tempo_busca(distancia)
        self.cilindro_atual = cilindro
        self.ultimo_bloco = None
        self.deslocamento_total += distancia
        return duracao, distancia

    def atender(self, bloco, n_blocos=1, tipo="L"):
        cilindro = cilindro_do_bloco(bloco)
        distancia = abs(cilindro - self.cilindro_atual)
        duracao = self.tempo_busca(distancia)
        em_sequencia = self.ultimo_bloco is not None and bloco == self.ultimo_bloco + 1
        if not em_sequencia:
            duracao += self.latencia_rotacional_ms
        duracao += n_blocos * self.transferencia_ms_por_bloco
        ultimo = bloco + n_blocos - 1
        self.cilindro_atual = cilindro_do_bloco(ultimo)
        self.ultimo_bloco = ultimo
        self.deslocamento_total += distancia
        return duracao, distancia


def criar_disco(nome):
    if nome == "hdd":
        return DiscoHDD()
    if nome == "rapido":
        return DiscoHDD(nome="rapido", **P.DISPOSITIVO_RAPIDO)
    if nome == "flash":
        return DiscoFlash()
    raise ValueError("dispositivo desconhecido: %s" % nome)


class DiscoFlash:
    nome = "flash"
    LIVRE = -1
    INVALIDA = -2
    BLOCOS_RESERVADOS_GC = 1

    def __init__(self, total_blocos_logicos=P.TOTAL_BLOCOS):
        ppb = P.FLASH_PAGINAS_POR_BLOCO_APAGAVEL
        self.cilindros = P.CILINDROS
        self.cilindro_atual = 0
        self.deslocamento_total = 0
        self.paginas_gravadas_host = 0
        self.paginas_programadas = 0
        self.apagamentos = 0
        self.copias_gc = 0
        self.total_logico = total_blocos_logicos
        self.ppb = ppb
        logicos_em_blocos = -(-total_blocos_logicos // ppb)
        n_blocos = max(-(-int(total_blocos_logicos * (1 + P.FLASH_AREA_RESERVA)) // ppb),
                       logicos_em_blocos + 3)
        self.n_blocos = n_blocos
        self.mapa = [self.LIVRE] * total_blocos_logicos
        self.reverso = [self.LIVRE] * (n_blocos * ppb)
        self.validas = [0] * n_blocos
        self.livres = deque(range(n_blocos))
        self.ativo = None
        self.proxima = ppb

    def mover(self, cilindro):
        return 0.0, 0

    def _abrir_bloco(self, para_gc=False):
        tempo = 0.0
        if not para_gc:
            while len(self.livres) <= self.BLOCOS_RESERVADOS_GC:
                tempo += self._coletar_lixo()
        self.ativo = self.livres.popleft()
        self.proxima = 0
        return tempo

    def _programar(self, logico, para_gc=False):
        tempo = 0.0
        if self.proxima >= self.ppb:
            tempo += self._abrir_bloco(para_gc)
        antiga = self.mapa[logico]
        if antiga >= 0:
            self.reverso[antiga] = self.INVALIDA
            self.validas[antiga // self.ppb] -= 1
        pagina = self.ativo * self.ppb + self.proxima
        self.proxima += 1
        self.reverso[pagina] = logico
        self.validas[self.ativo] += 1
        self.mapa[logico] = pagina
        self.paginas_programadas += 1
        return tempo + P.FLASH_PROGRAMACAO_MS

    def _coletar_lixo(self):
        livres = set(self.livres)
        candidatos = [b for b in range(self.n_blocos) if b != self.ativo and b not in livres]
        if not candidatos:
            raise RuntimeError("flash: nenhum bloco para coletar")
        vitima = min(candidatos, key=lambda b: (self.validas[b], b))
        if self.validas[vitima] >= self.ppb:
            raise RuntimeError("flash: sem páginas inválidas para recuperar")
        tempo = 0.0
        inicio = vitima * self.ppb
        for pagina in range(inicio, inicio + self.ppb):
            logico = self.reverso[pagina]
            if logico >= 0:
                tempo += P.FLASH_LEITURA_MS + self._programar(logico, para_gc=True)
                self.copias_gc += 1
        for pagina in range(inicio, inicio + self.ppb):
            self.reverso[pagina] = self.LIVRE
        self.validas[vitima] = 0
        self.apagamentos += 1
        self.livres.append(vitima)
        return tempo + P.FLASH_APAGAMENTO_MS

    def atender(self, bloco, n_blocos=1, tipo="L"):
        if bloco < 0 or bloco + n_blocos > self.total_logico:
            raise ValueError("bloco fora do dispositivo: %d (+%d)" % (bloco, n_blocos))
        if tipo == "L":
            return n_blocos * P.FLASH_LEITURA_MS, 0
        duracao = 0.0
        for logico in range(bloco, bloco + n_blocos):
            self.paginas_gravadas_host += 1
            duracao += self._programar(logico)
        return duracao, 0

    @property
    def amplificacao_escrita(self):
        return self.paginas_programadas / float(max(1, self.paginas_gravadas_host))
