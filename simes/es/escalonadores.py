def _mais_proxima(fila, cabeca, filtro=None):
    melhor, melhor_dist = None, None
    for i, r in enumerate(fila):
        if filtro is not None and not filtro(r):
            continue
        dist = abs(r.cilindro - cabeca)
        if melhor is None or dist < melhor_dist:
            melhor, melhor_dist = i, dist
    return melhor


def _a_frente(cabeca, direcao):
    if direcao >= 0:
        return lambda r: r.cilindro >= cabeca
    return lambda r: r.cilindro <= cabeca


def _direcao_do_movimento(origem, destino, atual):
    if destino > origem:
        return +1
    if destino < origem:
        return -1
    return atual


class Escalonador:
    nome = "?"

    def escolher(self, fila, cabeca, direcao, agora, max_cilindro):
        raise NotImplementedError


class FCFS(Escalonador):
    nome = "FCFS"

    def escolher(self, fila, cabeca, direcao, agora, max_cilindro):
        return 0, direcao, []


class SSTF(Escalonador):
    nome = "SSTF"

    def escolher(self, fila, cabeca, direcao, agora, max_cilindro):
        i = _mais_proxima(fila, cabeca)
        return i, _direcao_do_movimento(cabeca, fila[i].cilindro, direcao), []


class SCAN(Escalonador):
    nome = "SCAN"

    def escolher(self, fila, cabeca, direcao, agora, max_cilindro):
        direcao = direcao if direcao in (+1, -1) else +1
        i = _mais_proxima(fila, cabeca, _a_frente(cabeca, direcao))
        if i is not None:
            return i, direcao, []
        borda = max_cilindro if direcao > 0 else 0
        passar = [borda] if borda != cabeca else []
        nova = -direcao
        i = _mais_proxima(fila, borda, _a_frente(borda, nova))
        return i, nova, passar


class CSCAN(Escalonador):
    nome = "C-SCAN"

    def escolher(self, fila, cabeca, direcao, agora, max_cilindro):
        i = _mais_proxima(fila, cabeca, _a_frente(cabeca, +1))
        if i is not None:
            return i, +1, []
        passar = [max_cilindro] if cabeca != max_cilindro else []
        passar.append(0)
        i = _mais_proxima(fila, 0, _a_frente(0, +1))
        return i, +1, passar


class LOOK(Escalonador):
    nome = "LOOK"

    def escolher(self, fila, cabeca, direcao, agora, max_cilindro):
        direcao = direcao if direcao in (+1, -1) else +1
        i = _mais_proxima(fila, cabeca, _a_frente(cabeca, direcao))
        if i is not None:
            return i, direcao, []
        nova = -direcao
        i = _mais_proxima(fila, cabeca, _a_frente(cabeca, nova))
        return i, nova, []


class CLOOK(Escalonador):
    nome = "C-LOOK"

    def escolher(self, fila, cabeca, direcao, agora, max_cilindro):
        i = _mais_proxima(fila, cabeca, _a_frente(cabeca, +1))
        if i is not None:
            return i, +1, []
        menor = min(range(len(fila)), key=lambda k: (fila[k].cilindro, k))
        return menor, +1, []


class Deadline(Escalonador):
    nome = "DEADLINE"

    def escolher(self, fila, cabeca, direcao, agora, max_cilindro):
        vencidas = [k for k, r in enumerate(fila) if agora >= r.prazo]
        if vencidas:
            i = min(vencidas, key=lambda k: (fila[k].prazo, k))
            return i, direcao, []
        i = _mais_proxima(fila, cabeca)
        return i, _direcao_do_movimento(cabeca, fila[i].cilindro, direcao), []


ESCALONADORES = {c.nome: c for c in (FCFS, SSTF, SCAN, CSCAN, LOOK, CLOOK, Deadline)}


def criar_escalonador(nome):
    try:
        return ESCALONADORES[nome]()
    except KeyError:
        raise ValueError("escalonador desconhecido: %s (opções: %s)" % (nome, ", ".join(ESCALONADORES)))


def percorrer(escalonador, cilindros, cabeca, direcao=+1, max_cilindro=199):
    from simes.es.requisicao import Requisicao
    from simes import parametros as P
    fila = [Requisicao(i, 0.0, "P1", "L", c * P.BLOCOS_POR_CILINDRO) for i, c in enumerate(cilindros)]
    ordem, total = [], 0
    while fila:
        i, direcao, passar = escalonador.escolher(fila, cabeca, direcao, 0.0, max_cilindro)
        for c in passar:
            total += abs(c - cabeca)
            cabeca = c
        r = fila.pop(i)
        total += abs(r.cilindro - cabeca)
        cabeca = r.cilindro
        ordem.append(r.cilindro)
    return ordem, total
