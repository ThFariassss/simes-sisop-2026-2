from simes.es.requisicao import Requisicao


def historico_para_requisicoes(historico):
    ordenado = sorted(enumerate(historico), key=lambda par: (float(par[1][0]), par[0]))
    requisicoes = []
    for novo_id, (_, (instante, processo, tipo, bloco, n_blocos)) in enumerate(ordenado):
        requisicoes.append(Requisicao(novo_id, instante, processo, tipo, bloco, n_blocos))
    return requisicoes
