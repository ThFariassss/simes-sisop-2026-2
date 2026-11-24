import math
from collections import Counter

from simes import parametros as P
from simes.arquivos.estruturas import FCB, EntradaGlobal, EntradaProcesso, ErroSA

MAX_LINKS_SIMBOLICOS = 8


class SistemaArquivos:

    def __init__(self, camada, alocador, bloqueio="consultivo"):
        self.camada = camada
        self.alocador = alocador
        self.bloqueio = bloqueio
        self.fcbs = {}
        self._proximo_id = 0
        self.agora_ms = 0.0
        self.tabela_global = {}
        self.tabelas_processo = {}
        self.usuarios = {}
        self.contadores = Counter()
        self._seguiu_simbolico = False
        self.raiz = self._novo_fcb("/", "diretorio", None)
        self.alocador.alocar(self.raiz, 1)

    def _novo_fcb(self, nome, tipo, pai, processo=None):
        fcb = FCB(self._proximo_id, nome, tipo, pai, self.agora_ms, self.usuario_de(processo))
        self.fcbs[fcb.id] = fcb
        self._proximo_id += 1
        return fcb

    def usuario_de(self, processo):
        return self.usuarios.get(processo, "root")

    def _ler(self, blocos):
        self.camada.ler(blocos)

    def _escrever(self, blocos):
        self.camada.escrever(blocos)

    def _aplicar(self, extras):
        for tipo, bloco in extras:
            if tipo == "L":
                self._ler([bloco])
            else:
                self._escrever([bloco])

    def _bloco_do_diretorio(self, diretorio):
        bloco, extras = self.alocador.bloco_fisico(diretorio, 0)
        self._aplicar(extras)
        return bloco

    def _ler_diretorio(self, diretorio):
        self._ler([self._bloco_do_diretorio(diretorio)])

    def _gravar_diretorio(self, diretorio):
        self._escrever([self._bloco_do_diretorio(diretorio)])

    def _blocos_do_intervalo(self, fcb, inicio, fim):
        if fim <= inicio:
            return []
        primeiro = inicio // P.TAMANHO_BLOCO
        ultimo = (fim - 1) // P.TAMANHO_BLOCO
        blocos = []
        for i in range(primeiro, ultimo + 1):
            bloco, extras = self.alocador.bloco_fisico(fcb, i)
            self._aplicar(extras)
            blocos.append(bloco)
        return blocos

    @staticmethod
    def _blocos_necessarios(tamanho):
        return int(math.ceil(tamanho / float(P.TAMANHO_BLOCO)))

    @staticmethod
    def _dividir(caminho):
        if not caminho.startswith("/"):
            raise ErroSA("EINVAL", "o caminho deve ser absoluto: %s" % caminho)
        return [p for p in caminho.split("/") if p]

    def _entrada(self, processo, handle):
        try:
            return self.tabelas_processo[processo][handle]
        except KeyError:
            raise ErroSA("EBADF", "%s não tem o handle %s" % (processo, handle))

    def _liberar_arquivo(self, fcb):
        self.alocador.liberar(fcb)
        del self.fcbs[fcb.id]


    _MODOS = ("r", "w", "rw", "a")

    def _pai_de(self, caminho):
        partes = self._dividir(caminho)
        if not partes:
            raise ErroSA("EEXIST", "a raiz já existe")
        pai = self.resolver("/" + "/".join(partes[:-1]))
        if pai.tipo != "diretorio":
            raise ErroSA("ENOTDIR", "%s não é diretório" % pai.nome)
        self._ler_diretorio(pai)
        return pai, partes[-1]

    def _novo_na_entrada(self, processo, pai, nome, tipo):
        if nome in pai.entradas:
            raise ErroSA("EEXIST", nome)
        return self._novo_fcb(nome, tipo, pai.id, processo)

    def _alocar(self, fcb, n):
        try:
            extras = self.alocador.alocar(fcb, n)
        except ErroSA:
            del self.fcbs[fcb.id]
            raise
        self._aplicar(extras)

    def resolver(self, caminho, seguir_simbolico=True):
        partes = self._dividir(caminho)
        self._seguiu_simbolico = False
        atual = self.raiz
        saltos = 0
        i = 0
        while i < len(partes):
            if atual.tipo != "diretorio":
                raise ErroSA("ENOTDIR", "%s não é diretório" % atual.nome)
            self._ler_diretorio(atual)
            nome = partes[i]
            if nome not in atual.entradas:
                raise ErroSA("ENOENT", caminho)
            proximo = self.fcbs[atual.entradas[nome]]
            ultimo = i == len(partes) - 1
            if proximo.tipo == "simbolico" and (seguir_simbolico or not ultimo):
                saltos += 1
                if saltos > MAX_LINKS_SIMBOLICOS:
                    raise ErroSA("ELOOP", caminho)
                self._seguiu_simbolico = True
                partes = self._dividir(proximo.alvo) + partes[i + 1:]
                atual, i = self.raiz, 0
                continue
            atual = proximo
            i += 1
        return atual

    def mkdir(self, processo, caminho):
        pai, nome = self._pai_de(caminho)
        novo = self._novo_na_entrada(processo, pai, nome, "diretorio")
        self._alocar(novo, 1)
        pai.entradas[nome] = novo.id
        self._gravar_diretorio(novo)
        self._gravar_diretorio(pai)

    def create(self, processo, caminho, tamanho):
        tamanho = int(tamanho)
        if tamanho < 0:
            raise ErroSA("EINVAL", "tamanho negativo")
        pai, nome = self._pai_de(caminho)
        novo = self._novo_na_entrada(processo, pai, nome, "arquivo")
        self._alocar(novo, self._blocos_necessarios(tamanho))
        novo.tamanho = tamanho
        pai.entradas[nome] = novo.id
        self._gravar_diretorio(pai)

    def open(self, processo, caminho, modo, handle):
        if modo not in self._MODOS:
            raise ErroSA("EINVAL", "modo inválido: %s" % modo)
        tabela = self.tabelas_processo.setdefault(processo, {})
        if handle in tabela:
            raise ErroSA("EBADF", "%s já usa o handle %s" % (processo, handle))
        try:
            fcb = self.resolver(caminho)
        except ErroSA as e:
            if e.codigo == "ENOENT" and self._seguiu_simbolico:
                self.contadores["links_pendentes"] += 1
            raise
        if fcb.tipo == "diretorio":
            raise ErroSA("EISDIR", caminho)
        self._verificar_permissao(processo, fcb, modo)
        entrada = self.tabela_global.get(fcb.id)
        if entrada is None:
            entrada = self.tabela_global[fcb.id] = EntradaGlobal(fcb.id)
        entrada.contador += 1
        tabela[handle] = EntradaProcesso(fcb.id, modo, 0)

    def read(self, processo, handle, n):
        e = self._entrada(processo, handle)
        if "r" not in e.modo:
            raise ErroSA("EBADF", "handle %s não foi aberto para leitura" % handle)
        n = int(n)
        if n < 0:
            raise ErroSA("EINVAL", "n negativo")
        fcb = self.fcbs[e.fcb_id]
        self._verificar_trava(processo, fcb)
        inicio = e.posicao
        fim = min(inicio + n, fcb.tamanho)
        if fim <= inicio:
            return 0
        self._ler(self._blocos_do_intervalo(fcb, inicio, fim))
        e.posicao = fim
        return fim - inicio

    def write(self, processo, handle, n):
        e = self._entrada(processo, handle)
        if "w" not in e.modo and e.modo != "a":
            raise ErroSA("EBADF", "handle %s não foi aberto para escrita" % handle)
        n = int(n)
        if n < 0:
            raise ErroSA("EINVAL", "n negativo")
        fcb = self.fcbs[e.fcb_id]
        self._verificar_trava(processo, fcb)
        if e.modo == "a":
            e.posicao = fcb.tamanho
        if n == 0:
            return 0
        inicio, fim = e.posicao, e.posicao + n
        faltam = self._blocos_necessarios(fim) - len(fcb.blocos)
        if faltam > 0:
            self._aplicar(self.alocador.estender(fcb, faltam))
        self._escrever(self._blocos_do_intervalo(fcb, inicio, fim))
        e.posicao = fim
        fcb.tamanho = max(fcb.tamanho, fim)
        fcb.modificado_ms = self.agora_ms
        fcb.sujo = True
        return n

    def seek(self, processo, handle, posicao):
        e = self._entrada(processo, handle)
        posicao = int(posicao)
        if posicao < 0:
            raise ErroSA("EINVAL", "posição negativa")
        e.posicao = posicao

    def close(self, processo, handle):
        e = self._entrada(processo, handle)
        fcb = self.fcbs[e.fcb_id]
        if fcb.sujo:
            self._gravar_diretorio(self.fcbs[fcb.pai])
            fcb.sujo = False
        del self.tabelas_processo[processo][handle]
        entrada = self.tabela_global[fcb.id]
        entrada.contador -= 1
        if entrada.trava == processo:
            entrada.trava = None
        if entrada.contador == 0:
            del self.tabela_global[fcb.id]
            if fcb.links == 0:
                self._liberar_arquivo(fcb)

    def unlink(self, processo, caminho):
        pai, nome = self._pai_de(caminho)
        if nome not in pai.entradas:
            raise ErroSA("ENOENT", caminho)
        fcb = self.fcbs[pai.entradas[nome]]
        if fcb.tipo == "diretorio":
            raise ErroSA("EISDIR", caminho)
        del pai.entradas[nome]
        fcb.links -= 1
        self._gravar_diretorio(pai)
        if fcb.links == 0 and fcb.id not in self.tabela_global:
            self._liberar_arquivo(fcb)

    def link(self, processo, existente, novo):
        fcb = self.resolver(existente, seguir_simbolico=False)
        if fcb.tipo == "diretorio":
            raise ErroSA("EPERM", "hard link para diretório")
        pai, nome = self._pai_de(novo)
        if nome in pai.entradas:
            raise ErroSA("EEXIST", novo)
        pai.entradas[nome] = fcb.id
        fcb.links += 1
        self._gravar_diretorio(pai)

    def symlink(self, processo, alvo, caminho):
        self._dividir(alvo)
        pai, nome = self._pai_de(caminho)
        fcb = self._novo_na_entrada(processo, pai, nome, "simbolico")
        fcb.alvo = alvo
        pai.entradas[nome] = fcb.id
        self._gravar_diretorio(pai)

    def percorrer(self, processo, caminho):
        inicio = self.resolver(caminho)
        visitados = set()
        pilha = [inicio]
        while pilha:
            fcb = pilha.pop()
            if fcb.tipo == "arquivo":
                self.contadores["percorrer_arquivos"] += 1
                continue
            if fcb.tipo != "diretorio":
                continue
            if fcb.id in visitados:
                self.contadores["ciclos_detectados"] += 1
                continue
            visitados.add(fcb.id)
            self._ler_diretorio(fcb)
            self.contadores["percorrer_diretorios"] += 1
            filhos = []
            for nome in sorted(fcb.entradas):
                filho = self.fcbs[fcb.entradas[nome]]
                if filho.tipo == "simbolico":
                    try:
                        filho = self.resolver(filho.alvo)
                    except ErroSA as e:
                        if e.codigo == "ENOENT":
                            self.contadores["links_pendentes"] += 1
                        elif e.codigo == "ELOOP":
                            self.contadores["links_em_laco"] += 1
                        continue
                filhos.append(filho)
            pilha.extend(reversed(filhos))

    _BITS = {"r": 4, "w": 2, "x": 1}
    _EXIGIDO = {"r": "r", "w": "w", "a": "w", "rw": "rw"}

    def _verificar_permissao(self, processo, fcb, modo):
        usuario = self.usuario_de(processo)
        exigido = self._EXIGIDO[modo]
        if usuario == "root":
            self.contadores["verificacoes"] += 1
            return None
        permitido = None
        for posicao, (dono_acl, perms) in enumerate(fcb.acl, start=1):
            if dono_acl == usuario:
                self.contadores["verificacoes"] += posicao
                permitido = all(p in perms for p in exigido)
                break
        if permitido is None:
            self.contadores["verificacoes"] += len(fcb.acl) + 1
            bits = (fcb.permissoes >> 6) & 7 if usuario == fcb.dono else fcb.permissoes & 7
            permitido = all(bits & self._BITS[p] for p in exigido)
        if not permitido:
            self.contadores["negados"] += 1
            raise ErroSA("EACCES", "%s não pode abrir %s em modo %s" % (usuario, fcb.nome, modo))
        return None

    def _verificar_trava(self, processo, fcb):
        entrada = self.tabela_global.get(fcb.id)
        if entrada is None or entrada.trava is None or entrada.trava == processo:
            return None
        if self.bloqueio == "mandatorio":
            self.contadores["bloqueados_mandatorio"] += 1
            raise ErroSA("EBUSY", "%s está travado por %s" % (fcb.nome, entrada.trava))
        self.contadores["violacoes_consultivo"] += 1
        return None

    def _alterar_protecao(self, processo, caminho):
        fcb = self.resolver(caminho)
        usuario = self.usuario_de(processo)
        if usuario != "root" and usuario != fcb.dono:
            raise ErroSA("EPERM", "%s não é dono de %s" % (usuario, caminho))
        return fcb

    def _gravar_pai(self, fcb):
        if fcb.pai is not None:
            self._gravar_diretorio(self.fcbs[fcb.pai])

    def usuario(self, processo, nome):
        self.usuarios[processo] = str(nome)

    def chmod(self, processo, caminho, permissoes):
        try:
            valor = int(str(permissoes), 8)
        except ValueError:
            raise ErroSA("EINVAL", "permissões inválidas: %s" % permissoes)
        if not 0 <= valor <= 0o777:
            raise ErroSA("EINVAL", "permissões inválidas: %s" % permissoes)
        fcb = self._alterar_protecao(processo, caminho)
        fcb.permissoes = valor
        self._gravar_pai(fcb)

    def setacl(self, processo, caminho, usuario, permissoes):
        permissoes = str(permissoes)
        if len(permissoes) != 3 or any(c not in "rwx-" for c in permissoes):
            raise ErroSA("EINVAL", "permissões inválidas: %s" % permissoes)
        fcb = self._alterar_protecao(processo, caminho)
        usuario = str(usuario)
        for k, (u, _) in enumerate(fcb.acl):
            if u == usuario:
                fcb.acl[k] = (usuario, permissoes)
                break
        else:
            fcb.acl.append((usuario, permissoes))
        self._gravar_pai(fcb)

    def lock(self, processo, handle):
        e = self._entrada(processo, handle)
        entrada = self.tabela_global[e.fcb_id]
        if entrada.trava is not None and entrada.trava != processo:
            self.contadores["travas_negadas"] += 1
            raise ErroSA("EBUSY", "trava pertence a %s" % entrada.trava)
        entrada.trava = processo
        self.contadores["travas_concedidas"] += 1

    def unlock(self, processo, handle):
        e = self._entrada(processo, handle)
        entrada = self.tabela_global[e.fcb_id]
        if entrada.trava == processo:
            entrada.trava = None
