import re

from simes.arquivos.estruturas import ErroSA


class Bitmap:

    def __init__(self, total):
        self.total = total
        self.mapa = bytearray(total)
        self.ocupados = 0

    def livre(self, bloco):
        return self.mapa[bloco] == 0

    def ocupar(self, blocos):
        for b in blocos:
            if self.mapa[b]:
                raise RuntimeError("bloco %d já ocupado" % b)
            self.mapa[b] = 1
            self.ocupados += 1

    def liberar(self, blocos):
        for b in blocos:
            if self.mapa[b]:
                self.mapa[b] = 0
                self.ocupados -= 1

    def livres(self):
        return self.total - self.ocupados

    def intervalo_livre(self, inicio, n):
        if inicio + n > self.total:
            return False
        return self.mapa.find(1, inicio, inicio + n) == -1

    def primeiro_encaixe(self, n):
        if n <= 0:
            return 0
        pos = self.mapa.find(bytes(n))
        return None if pos < 0 else pos

    def primeiros_livres(self, n, a_partir_de=0):
        if n > self.livres():
            return None
        res, pos = [], a_partir_de
        while len(res) < n:
            pos = self.mapa.find(0, pos)
            if pos < 0:
                if a_partir_de:
                    return self.primeiros_livres(n, 0)
                return None
            res.append(pos)
            pos += 1
        return res

    def segmentos_livres(self):
        return [m.end() - m.start() for m in re.finditer(b"\x00+", bytes(self.mapa))]


class Alocador:
    nome = "?"

    def __init__(self, total_blocos):
        self.bitmap = Bitmap(total_blocos)
        self.realocacoes = 0
        self.falhas = 0

    def alocar(self, fcb, n):
        raise NotImplementedError

    def estender(self, fcb, n):
        raise NotImplementedError

    def liberar(self, fcb):
        self.bitmap.liberar(fcb.blocos)
        fcb.blocos = []

    def bloco_fisico(self, fcb, i):
        raise NotImplementedError

    def metricas(self):
        seg = self.bitmap.segmentos_livres()
        return {
            "blocos_livres": self.bitmap.livres(),
            "segmentos_livres": len(seg),
            "maior_segmento_livre": max(seg) if seg else 0,
            "realocacoes": self.realocacoes,
            "falhas_alocacao": self.falhas,
        }


class AlocadorContiguo(Alocador):
    nome = "contigua"

    def alocar(self, fcb, n):
        if n == 0:
            fcb.blocos = []
            return []
        inicio = self.bitmap.primeiro_encaixe(n)
        if inicio is None:
            self.falhas += 1
            raise ErroSA("ENOSPC", "sem %d blocos contíguos" % n)
        fcb.blocos = list(range(inicio, inicio + n))
        self.bitmap.ocupar(fcb.blocos)
        return []

    def estender(self, fcb, n):
        if n <= 0:
            return []
        if not fcb.blocos:
            return self.alocar(fcb, n)
        fim = fcb.blocos[-1] + 1
        if self.bitmap.intervalo_livre(fim, n):
            novos = list(range(fim, fim + n))
            self.bitmap.ocupar(novos)
            fcb.blocos.extend(novos)
            return []
        antigos = fcb.blocos
        total = len(antigos) + n
        inicio = self.bitmap.primeiro_encaixe(total)
        if inicio is None:
            self.falhas += 1
            raise ErroSA("ENOSPC", "sem %d blocos contíguos para realocar" % total)
        novos = list(range(inicio, inicio + total))
        self.bitmap.ocupar(novos)
        self.bitmap.liberar(antigos)
        fcb.blocos = novos
        self.realocacoes += 1
        extras = []
        for velho, novo in zip(antigos, novos):
            extras.append(("L", velho))
            extras.append(("E", novo))
        return extras

    def bloco_fisico(self, fcb, i):
        return fcb.blocos[i], []


class _AlocadorPorBlocosLivres(Alocador):

    def _reservar(self, n):
        blocos = self.bitmap.primeiros_livres(n)
        if blocos is None:
            self.falhas += 1
            raise ErroSA("ENOSPC", "sem %d blocos livres" % n)
        self.bitmap.ocupar(blocos)
        return blocos

    def alocar(self, fcb, n):
        fcb.blocos = self._reservar(n) if n > 0 else []
        fcb.extra["cursor"] = -1
        return []

    def _estender(self, fcb, n):
        if n <= 0:
            return None
        if not fcb.blocos:
            self.alocar(fcb, n)
            return None
        antigo_ultimo = fcb.blocos[-1]
        fcb.blocos.extend(self._reservar(n))
        return antigo_ultimo


class AlocadorEncadeado(_AlocadorPorBlocosLivres):
    nome = "encadeada"

    def estender(self, fcb, n):
        antigo_ultimo = self._estender(fcb, n)
        if antigo_ultimo is None:
            return []
        return [("E", antigo_ultimo)]

    def bloco_fisico(self, fcb, i):
        cursor = fcb.extra.get("cursor", -1)
        if i > cursor:
            percorrer = range(cursor + 1, i)
        else:
            percorrer = range(0, i)
        extras = [("L", fcb.blocos[j]) for j in percorrer]
        fcb.extra["cursor"] = i
        return fcb.blocos[i], extras


class AlocadorFAT(_AlocadorPorBlocosLivres):
    nome = "fat"

    def estender(self, fcb, n):
        self._estender(fcb, n)
        return []

    def bloco_fisico(self, fcb, i):
        return fcb.blocos[i], []


class AlocadorIndexado(Alocador):
    nome = "indexada"
    PONTEIROS = 1024

    @classmethod
    def blocos_de_indice(cls, n):
        if n <= cls.PONTEIROS:
            return 1
        return 1 + -(-n // cls.PONTEIROS)

    def _reservar(self, n):
        blocos = self.bitmap.primeiros_livres(n)
        if blocos is None:
            self.falhas += 1
            raise ErroSA("ENOSPC", "sem %d blocos livres" % n)
        self.bitmap.ocupar(blocos)
        return blocos

    def alocar(self, fcb, n):
        k = self.blocos_de_indice(n)
        blocos = self._reservar(k + n)
        fcb.extra["indices"] = blocos[:k]
        fcb.blocos = blocos[k:]
        return [("E", b) for b in fcb.extra["indices"]]

    def estender(self, fcb, n):
        if n <= 0:
            return []
        if "indices" not in fcb.extra:
            return self.alocar(fcb, n)
        indices = fcb.extra["indices"]
        faltam = self.blocos_de_indice(len(fcb.blocos) + n) - len(indices)
        novos = self._reservar(faltam + n)
        indices.extend(novos[:faltam])
        fcb.blocos.extend(novos[faltam:])
        return [("E", b) for b in indices]

    def liberar(self, fcb):
        self.bitmap.liberar(list(fcb.blocos) + list(fcb.extra.get("indices", [])))
        fcb.blocos = []
        fcb.extra["indices"] = []

    def bloco_fisico(self, fcb, i):
        indices = fcb.extra["indices"]
        if len(indices) == 1:
            extras = [("L", indices[0])]
        else:
            extras = [("L", indices[0]), ("L", indices[1 + i // self.PONTEIROS])]
        return fcb.blocos[i], extras


ALOCADORES = {c.nome: c for c in (AlocadorContiguo, AlocadorEncadeado, AlocadorFAT, AlocadorIndexado)}


def criar_alocador(nome, total_blocos):
    try:
        return ALOCADORES[nome](total_blocos)
    except KeyError:
        raise ValueError("alocador desconhecido: %s" % nome)
