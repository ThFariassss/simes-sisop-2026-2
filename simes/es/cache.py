from collections import OrderedDict


class Cache:
    nome = "?"
    reordena_no_acesso = False

    def __init__(self, capacidade, escrita="write-back"):
        if escrita not in ("write-through", "write-back"):
            raise ValueError("escrita deve ser 'write-through' ou 'write-back'")
        if capacidade < 1:
            raise ValueError("a capacidade da cache deve ser de pelo menos 1 bloco")
        self.capacidade = capacidade
        self.escrita = escrita
        self.blocos = OrderedDict()
        self.expulsoes = 0

    def _tocar(self, bloco):
        if self.reordena_no_acesso:
            self.blocos.move_to_end(bloco)

    def _abrir_espaco(self):
        sujos = []
        while len(self.blocos) >= self.capacidade:
            vitima, sujo = self.blocos.popitem(last=False)
            self.expulsoes += 1
            if sujo:
                sujos.append(vitima)
        return sujos

    def ler(self, bloco):
        if bloco in self.blocos:
            self._tocar(bloco)
            return True
        return False

    def inserir(self, bloco):
        if bloco in self.blocos:
            self._tocar(bloco)
            return []
        sujos = self._abrir_espaco()
        self.blocos[bloco] = False
        return sujos

    def escrever(self, bloco):
        write_back = self.escrita == "write-back"
        if bloco in self.blocos:
            self._tocar(bloco)
            sujos = []
            if write_back:
                self.blocos[bloco] = True
        else:
            sujos = self._abrir_espaco()
            self.blocos[bloco] = write_back
        if write_back:
            return sujos
        return [bloco] + sujos

    def sincronizar(self):
        sujos = [b for b, sujo in self.blocos.items() if sujo]
        for b in sujos:
            self.blocos[b] = False
        return sujos

    def sujos(self):
        return sum(1 for sujo in self.blocos.values() if sujo)


class CacheLRU(Cache):
    nome = "LRU"
    reordena_no_acesso = True


class CacheFIFO(Cache):
    nome = "FIFO"
    reordena_no_acesso = False


CACHES = {"LRU": CacheLRU, "FIFO": CacheFIFO}


def criar_cache(nome, capacidade, escrita):
    try:
        return CACHES[nome](capacidade, escrita)
    except KeyError:
        raise ValueError("cache desconhecida: %s" % nome)
