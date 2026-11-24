from simes import parametros as P

MODOS = ("polling", "interrupcao", "dma")


def custo_cpu(modo, n_blocos, duracao_ms):
    if modo == "polling":
        return float(duracao_ms)
    if modo == "interrupcao":
        return n_blocos * (P.CUSTO_INTERRUPCAO_MS + P.CUSTO_COPIA_MS_POR_BLOCO)
    if modo == "dma":
        return P.CUSTO_PROGRAMAR_DMA_MS + P.CUSTO_INTERRUPCAO_MS
    raise ValueError("modo desconhecido: %s" % modo)
