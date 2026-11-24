def simular_buffering(n_buffers, producao_ms, consumo_ms, blocos):
    if n_buffers < 1:
        raise ValueError("é preciso pelo menos 1 buffer")
    fim_producao = 0.0
    fim_gravacao = []
    bloqueio = 0.0
    for k in range(1, blocos + 1):
        buffer_livre = fim_gravacao[k - n_buffers - 1] if k > n_buffers else 0.0
        inicio = max(fim_producao, buffer_livre)
        bloqueio += inicio - fim_producao
        fim_producao = inicio + producao_ms
        anterior = fim_gravacao[-1] if fim_gravacao else 0.0
        fim_gravacao.append(max(fim_producao, anterior) + consumo_ms)
    total = fim_gravacao[-1] if fim_gravacao else 0.0
    return {
        "tempo_total_ms": total,
        "bloqueio_produtor_ms": bloqueio,
        "utilizacao_dispositivo_pct": 100.0 * blocos * consumo_ms / total if total > 0 else 0.0,
    }
