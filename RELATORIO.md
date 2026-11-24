# Relatório — SimES: simulador de E/S e de sistema de arquivos

**Integrantes:** Thiago Farias dos Santos, Pedro Marques Sangali, Gabriel Kondach

**Repositório:** https://github.com/ThFariassss/simes-sisop-2026-2

## 0. Resumo

O grupo implementou o núcleo completo do SimES (SSTF, SCAN, custo de CPU por polling e por
interrupção e o sistema de arquivos com resolução de caminhos, tabelas de arquivos abertos e
unlink adiado), os seis módulos (M1 cache LRU/FIFO com write-through e write-back; M2 C-SCAN,
LOOK, C-LOOK e DEADLINE; M3 DMA e buffering; M4 hard links, links simbólicos e detecção de
ciclos; M5 rwx, ACL e travas consultivas/mandatórias; M6 alocação encadeada, FAT e indexada),
a integração ponta a ponta e o bônus de armazenamento flash (FTL em log com coleta de lixo gulosa).

Resultados mais importantes:

1. **Em disco rígido, a ordem de atendimento domina o desempenho.** Na carga sequencial, o SSTF reduziu a resposta média de 10,2 s (FCFS saturado) para 17 ms e aumentou a vazão de 61 para 167 req/s (X1). Com carga alta, o DEADLINE manteve a média do SSTF (28,4 contra 27,8 ms) e reduziu a espera máxima em 25% (de 327 para 245 ms) (X7).
2. **Polling, interrupção e DMA dependem do dispositivo.** No HDD, o polling consome de 86% a 100% da CPU, contra ~1% da interrupção. No dispositivo rápido a relação se inverte: 0,53% contra 1,12% (X2). O DMA custa 0,018 ms fixo por requisição e, com 256 blocos, é 241 vezes mais barato que a interrupção (X8).
3. **Contar acessos não basta.** Na alocação encadeada, um `read` faz 74,9 acessos e, na indexada, 6,1. Mesmo assim, a indexada é a mais lenta (19,0 contra 6,4 ms), porque alternar índice e dado destrói a leitura sequencial. Já a contígua falhou 113 vezes com `ENOSPC` tendo 2.052 blocos livres, porque o maior trecho livre tinha só 154 blocos (X11).

## 1. Escopo realizado

| Parte | Situação | Observação |
|---|---|---|
| N1 · Dispositivo (fornecido) | usado | — |
| N2 · Subsistema de E/S (SSTF, SCAN, polling, interrupção) | completo | conferido com o exemplo do livro (fila 98…67, cabeça em 53) |
| N3 · Sistema de arquivos | completo | regras de custo seguidas à risca; unlink de arquivo aberto libera no último close |
| N4 · Experimentos X1–X5 e relatório | completo | — |
| M1 · Cache de blocos | completo | LRU e FIFO sobre `OrderedDict`; write-through e write-back |
| M2 · Escalonamento avançado | completo | C-SCAN, LOOK, C-LOOK e DEADLINE (prazos de `parametros.py`) |
| M3 · DMA e buffering | completo | buffer simples, duplo e circular |
| M4 · Links e compartilhamento | completo | ELOOP após 8 links; ciclos detectados na travessia |
| M5 · Proteção e travas | completo | rwx do dono/outros, ACL com primeira entrada válida, travas exclusivas |
| M6 · Alocação em disco | completo | encadeada, FAT e indexada com índice de 2 níveis |
| Integração (grupos de 4) | completo | histórico da camada de blocos → requisições de E/S |
| Bônus flash | completo | FTL em log, coleta de lixo gulosa, 7% de área de reserva |

Resultado dos testes automáticos (`python3 executar.py testes`): `Ran 36 tests in 0.006s — OK` (26 testes fornecidos + 10 testes extras do grupo em `testes/test_extras.py`).

## 2. Implementação

### N2 — Subsistema de E/S

**Onde:** `simes/es/escalonadores.py` (classes `SSTF` e `SCAN` e as funções auxiliares `_mais_proxima`, `_a_frente` e `_direcao_do_movimento`); `simes/es/cpu.py` (`custo_cpu`, modos `polling` e `interrupcao`).

**Decisões:** Todos os escalonadores usam a mesma função `_mais_proxima`, que percorre a fila na ordem de chegada e só troca de candidata quando encontra uma distância *estritamente* menor; assim o desempate "vence a que chegou antes" sai de graça. O filtro `_a_frente` considera o mesmo cilindro da cabeça como "à frente" nas duas direções, como pede a regra comum. O SSTF devolve a direção do movimento feito (mantém a atual se o alvo está no mesmo cilindro). O SCAN, quando não há nada à frente, coloca a borda (0 ou `max_cilindro`) em `passar_por`, inverte e escolhe a mais próxima *a partir da borda*; se a cabeça já está na borda, `passar_por` fica vazio para não gerar um movimento de distância zero. O custo de CPU segue as fórmulas do arquivo: polling = toda a duração do atendimento (espera ocupada); interrupção = `n_blocos × (CUSTO_INTERRUPCAO_MS + CUSTO_COPIA_MS_POR_BLOCO)`.

**Dificuldades:** Conferir o SCAN subindo (deslocamento 331) exigiu entender que a ida até a borda conta no deslocamento mesmo sem atender nada; a separação entre `passar_por` e a requisição escolhida resolveu isso sem mexer no simulador.

### N3 — Sistema de arquivos

**Onde:** `simes/arquivos/sistema.py`: `resolver`, `mkdir`, `create`, `open`, `read`, `write`, `seek`, `close`, `unlink` e os auxiliares do grupo `_pai_de`, `_novo_na_entrada` e `_alocar`.

**Decisões:** `resolver` percorre os componentes lendo cada diretório antes de procurar o próximo nome, então `/a/b/c` custa exatamente 3 leituras e `/` custa zero. As operações que alteram entradas usam `_pai_de`, que chama `resolver` no caminho do pai e depois lê o próprio pai (o "até o pai, inclusive" das regras). `mkdir` grava o bloco do novo diretório e o do pai; `create` só aloca os blocos (sem gravar conteúdo) e grava o pai. Se o alocador lança `ENOSPC`, `_alocar` apaga o FCB recém-criado para não deixar lixo em `self.fcbs`. Tabelas: a global é `fcb_id → EntradaGlobal` com contador de aberturas; a de cada processo é `handle → EntradaProcesso` com modo e posição, de modo que dois processos lendo o mesmo arquivo compartilham a entrada global mas têm posições independentes. `read` limita o intervalo ao tamanho do arquivo e lê só os blocos que contêm `[posição, posição+n)`; `write` estende o arquivo pelo alocador quando necessário, grava os blocos do intervalo, atualiza o tamanho e marca o FCB como sujo; no modo `'a'` a posição vai para o fim antes de cada escrita. `close` grava o bloco do pai se o FCB está sujo, decrementa o contador e, se ele zera e o arquivo já não tem links, libera os blocos (unlink adiado, como no Unix). `unlink` de diretório devolve `EISDIR`. `open` com um handle já em uso devolve `EBADF` e com modo desconhecido devolve `EINVAL`.

**Dificuldades:** O ponto delicado foi o unlink de arquivo aberto: o FCB precisa continuar em `self.fcbs` (para o processo que ainda o lê) e só ser liberado no último close; isso ficou em dois lugares (`unlink` e `close`) que testam a mesma condição `links == 0` e "ninguém com o arquivo aberto".

### M1 — Cache de blocos

**Onde:** `simes/es/cache.py` (`Cache`, `CacheLRU`, `CacheFIFO`).

**Decisões:** As duas políticas compartilham a mesma classe base, com um `OrderedDict` `bloco → sujo`; a ordem do dicionário é a ordem de expulsão. A única diferença é o atributo `reordena_no_acesso`: a LRU faz `move_to_end` em cada acerto e em cada escrita (a escrita conta como acesso), a FIFO nunca reordena. Em write-through, `escrever` devolve o próprio bloco (e nunca há sujos); em write-back, marca o bloco como sujo e devolve apenas os sujos expulsos. `sincronizar` devolve e limpa todos os sujos.

**Dificuldades:** Decidir o que fazer quando `inserir` recebe um bloco que já está na cache (ex.: escrito enquanto a leitura estava na fila): tratamos como acesso, sem duplicar.

### M2 — Escalonamento avançado

**Onde:** `simes/es/escalonadores.py` (`CSCAN`, `LOOK`, `CLOOK`, `Deadline`).

**Decisões:** C-SCAN só sobe; sem nada à frente, `passar_por = [max_cilindro, 0]` (o retorno conta). LOOK inverte no próprio cilindro atual, sem ir à borda. C-LOOK salta para a requisição de menor cilindro (empate: a mais antiga). DEADLINE verifica primeiro se há requisição vencida (`agora >= prazo`) e atende a de prazo mais antigo; senão, age como o SSTF. Os quatro conferem com o livro: 382, 299 e 322 cilindros.

**Dificuldades:** Nenhuma relevante depois que as funções auxiliares do N2 ficaram prontas.

### M3 — DMA e buffering

**Onde:** `simes/es/cpu.py` (modo `dma`); `simes/es/buffering.py` (`simular_buffering`).

**Decisões:** DMA custa `CUSTO_PROGRAMAR_DMA_MS + CUSTO_INTERRUPCAO_MS`, independentemente do tamanho. O buffering é uma recorrência direta das regras: o bloco *k* começa a ser produzido em `max(fim da produção de k−1, fim da gravação de k−n)`, e a gravação começa em `max(fim da produção de k, fim da gravação de k−1)`; o tempo parado do produtor é somado em `bloqueio_produtor_ms`.

**Dificuldades:** Nenhuma; o teste do enunciado (1002 ms com buffer duplo) foi conferido à mão antes de escrever o código.

### M4 — Links e compartilhamento

**Onde:** `simes/arquivos/sistema.py`: `resolver` (parte dos links), `link`, `symlink`, `percorrer`.

**Decisões:** Ao encontrar um link simbólico (no meio do caminho, ou no fim com `seguir_simbolico=True`), `resolver` recomeça da raiz com `alvo + resto do caminho`, como faz o kernel com alvos absolutos; depois de 8 links devolve `ELOOP`. `open` conta `links_pendentes` quando o `ENOENT` aconteceu depois de seguir um link. `link` não segue o link simbólico final (como o `link(2)` do Linux) e devolve `EPERM` para diretórios. `percorrer` é uma busca em profundidade com pilha explícita e um conjunto de diretórios visitados: chegar de novo a um diretório conta `ciclos_detectados` e não desce. Links cujo alvo não existe contam `links_pendentes`; links em laço contam `links_em_laco` (contador extra do grupo).

**Dificuldades:** O contador `ciclos_detectados` conta toda *revisita*, inclusive quando o mesmo diretório é alcançado por dois caminhos sem formar um ciclo verdadeiro (por exemplo, `/dados` e `/atalhos/dados_simbolico`). Seguimos a regra literal do enunciado e discutimos isso na análise do X9.

### M5 — Proteção e travas

**Onde:** `simes/arquivos/sistema.py`: `_verificar_permissao`, `_verificar_trava`, `usuario`, `chmod`, `setacl`, `lock`, `unlock`.

**Decisões:** A permissão exigida vem de uma tabela (`r→r`, `w→w`, `a→w`, `rw→rw`). Root sempre passa (1 verificação); se o usuário aparece na ACL, vale a primeira entrada dele (custo = posição); senão, valem os bits do dono ou de "outros" (custo = `len(acl)+1`). `chmod` aceita o valor já convertido para inteiro pelo simulador (`'600'` vira `600`) e o reinterpreta em octal com `int(str(x), 8)`. `setacl` substitui a entrada do usuário mantendo a posição ou acrescenta no fim. As travas são exclusivas e ficam na entrada global; o close libera a trava se quem fecha é o dono; `unlock` sem ter a trava não faz nada (como `flock`/`fcntl`).

**Dificuldades:** Perceber que o simulador converte argumentos numéricos para `int`, o que quebraria a leitura em octal se usássemos `int(x, 8)` diretamente.

### M6 — Alocação em disco

**Onde:** `simes/arquivos/alocacao.py` (`_AlocadorPorBlocosLivres`, `AlocadorEncadeado`, `AlocadorFAT`, `AlocadorIndexado`).

**Decisões:** Encadeada e FAT compartilham a base `_AlocadorPorBlocosLivres` (primeiros blocos livres, sem exigir contiguidade). A encadeada devolve 1 escrita extra no antigo último bloco a cada extensão e, em `bloco_fisico`, usa o cursor guardado em `fcb.extra['cursor']` para ler só os ponteiros necessários (recomeçando do bloco 0 quando o alvo está atrás do cursor). A FAT tem a mesma disposição de blocos, mas sem extras, porque a tabela está em memória. A indexada calcula os blocos de índice com `1` (até 1024 dados) ou `1 + ceil(n/1024)`; reserva índices e dados nos primeiros blocos livres; ao estender, acrescenta só os índices que faltam e regrava todos; `bloco_fisico` custa 1 ou 2 leituras de índice.

**Dificuldades:** A transição de 1 para 2 níveis de índice durante uma extensão: mantivemos o índice antigo como bloco de 1º nível e acrescentamos os de 2º nível, o que preserva a ordem exigida (`indices[0]` é o de 1º nível).

### Integração ponta a ponta

**Onde:** `simes/integracao.py` (`historico_para_requisicoes`).

**Decisões:** Ordenação estável por instante (acessos da mesma operação mantêm a ordem em que foram feitos) e ids renumerados 0, 1, 2…, gerando `Requisicao` com o tipo e o tamanho de cada acesso.

**Dificuldades:** Nenhuma.

### Bônus — Armazenamento flash

**Onde:** `simes/disco.py` (`DiscoFlash`); testes próprios em `testes/test_extras.py`.

**Decisões:** FTL em log: o mapa lógico→físico (`mapa`) e o reverso (`reverso`) são listas; gravar um bloco lógico programa a próxima página livre do bloco ativo e invalida a cópia antiga. O dispositivo tem 7% de páginas extras (mínimo de 3 blocos apagáveis extras, para dispositivos pequenos nos testes). Sempre se guarda 1 bloco apagado de reserva para a coleta de lixo; quando só resta ele, a coleta gulosa escolhe o bloco cheio com menos páginas válidas, copia as válidas (leitura + programação, que contam em `paginas_programadas`), apaga o bloco e o devolve à lista de livres. Leitura custa `FLASH_LEITURA_MS` por página, sem busca nem rotação.

**Dificuldades:** Garantir que a coleta sempre termina: com a reserva de 1 bloco e pelo menos 3 blocos extras, a vítima gulosa sempre tem menos de 64 páginas válidas, então cada coleta recupera ao menos uma página.

## 3. Experimentos

### X1 — Escalonadores sob quatro padrões de carga

**Hipótese:** SSTF e SCAN terão deslocamento total e tempo de resposta médio bem menores que o FCFS nas cargas aleatória, concentrada e em rajadas, porque o tempo de busca domina o atendimento de um disco rígido. O SSTF terá a menor resposta média, mas a maior espera máxima (inanição de requisições distantes); o SCAN terá resposta média um pouco maior que o SSTF e espera máxima menor. Na carga sequencial, FCFS será especialmente ruim: alterna entre dois processos em regiões distantes (blocos 20.000 e 250.000) e paga uma busca longa a cada requisição, enquanto SSTF e SCAN atenderão vários pedidos seguidos da mesma região.

**Fundamentação:** Silberschatz, 10.4.1–10.4.3: FCFS é justo mas não otimiza o movimento do braço; SSTF é uma forma de SJF e pode causar inanição; SCAN limita a espera a cerca de duas varreduras do disco. Pelo modelo (`parametros.py`), uma busca aleatória média (~1.667 cilindros) custa ~6 ms, mais 4,17 ms de rotação: com chegadas a cada 12 ms, o FCFS trabalha perto da saturação, então pequenas economias de busca viram grandes reduções de fila.

**Configuração:** `experimentos.py`, função `x1`: cargas `es_sequencial`, `es_aleatoria`, `es_concentrada` e `es_rajadas` (2.000 requisições de 8 blocos, 70% leituras), disco rígido, CPU por interrupção, escalonadores FCFS, SSTF e SCAN. Indicadores: deslocamento total (cilindros), resposta média e p95 (ms), maior espera (ms) e vazão (req/s).

**Resultados:**

| Carga | Escalonador | Deslocamento (cil.) | Resposta média (ms) | p95 (ms) | Espera máx. (ms) | Vazão (req/s) |
|---|---|---:|---:|---:|---:|---:|
| sequencial | FCFS | 7.184.093 | 10.241,0 | 19.565,4 | 20.598,1 | 61,5 |
| sequencial | SSTF | 2.336.365 | 17,4 | 32,8 | 37,4 | 167,5 |
| sequencial | SCAN | 2.469.069 | 23,2 | 43,7 | 48,6 | 167,4 |
| aleatória | FCFS | 3.316.472 | 50,1 | 145,1 | 193,5 | 82,5 |
| aleatória | SSTF | 2.631.714 | 21,0 | 48,6 | 116,7 | 82,5 |
| aleatória | SCAN | 3.372.680 | 31,5 | 65,1 | 116,7 | 82,4 |
| concentrada | FCFS | 1.864.164 | 19,8 | 48,6 | 96,0 | 83,8 |
| concentrada | SSTF | 1.613.030 | 15,0 | 37,2 | 130,5 | 83,8 |
| concentrada | SCAN | 3.338.708 | 33,2 | 68,3 | 118,8 | 83,7 |
| rajadas | FCFS | 3.341.490 | 267,0 | 505,3 | 597,3 | 83,5 |
| rajadas | SSTF | 209.811 | 147,5 | 277,7 | 297,0 | 84,4 |
| rajadas | SCAN | 199.955 | 148,6 | 278,7 | 285,4 | 84,4 |

**Evidências:** resultados/es.csv (experimento=X1); graficos/X1_resposta_media_ms.png; graficos/X1_espera_max_ms.png; graficos/X1_deslocamento_total.png

**Análise:**

- **Sequencial:** foi onde a diferença ficou maior. O FCFS saturou o disco: o tempo total foi de 32,5 s, contra 11,9 s do SSTF e do SCAN. Por isso a resposta média do FCFS (10,2 s) é quase toda tempo de fila, e o SSTF a reduz para 17,4 ms (cerca de 590 vezes menos). O deslocamento caiu 3,1 vezes e a vazão subiu de 61 para 167 req/s. SSTF e SCAN atendem várias requisições seguidas do mesmo processo e aproveitam a regra de "bloco seguinte sem rotação"; o FCFS alterna entre as regiões dos dois processos e paga busca e rotação a cada pedido.
- **Rajadas:** as 50 requisições simultâneas dão aos escalonadores uma fila grande para ordenar. O deslocamento caiu cerca de 16 vezes (de 3,34 milhões para 0,21 milhão no SSTF e 0,20 milhão no SCAN) e a resposta média caiu 45% (de 267 para cerca de 148 ms). O SCAN teve o menor deslocamento e a menor espera máxima (285,4 ms).
- **Aleatória:** o SSTF reduziu a resposta média em 58% (de 50,1 para 21,0 ms) e o SCAN em 37% (para 31,5 ms). Mesmo assim, o deslocamento do SCAN ficou 1,7% *maior* que o do FCFS.
- **Concentrada:** o SCAN foi *pior* que o FCFS, com 79% mais deslocamento (3,34 contra 1,86 milhão) e resposta média de 33,2 contra 19,8 ms. A carga é leve: a vazão é a mesma nos três escalonadores e o tempo total, de cerca de 23,9 s, é definido pelas chegadas. Com fila curta, o SCAN vai até a borda (cilindro 0 ou 4.999) a cada varredura, embora 80% dos pedidos estejam em 20% do disco. É exatamente o defeito que o LOOK corrige: no X7, com a mesma carga, o LOOK tem resposta média de 13,8 ms e o SCAN de 29,5 ms.
- **Inanição do SSTF (10.4.2):** o SSTF teve a maior espera máxima na carga concentrada (130,5 ms, contra 118,8 do SCAN e 96,0 do FCFS) e nas rajadas (297,0 contra 285,4 do SCAN). Na carga aleatória empatou com o SCAN (116,65 contra 116,73 ms). Com carga leve, o FCFS teve espera máxima menor que a do SSTF: ele é lento, mas justo.

Os pontos que a hipótese previu se confirmaram: FCFS péssimo na carga sequencial, SSTF com a menor resposta média e a maior espera máxima, e SCAN entre os dois sob carga. O que a hipótese errou foi supor que o SCAN sempre reduziria o deslocamento: em carga leve, as idas até a borda custam mais do que a ordenação economiza.

**Veredito:** PARCIALMENTE CONFIRMADA

### X2 — Polling × interrupção conforme o dispositivo

**Hipótese:** No disco rígido, a interrupção gastará uma fração muito pequena da CPU (cerca de 1%) e o polling uma fração enorme (próxima da utilização do disco, dezenas de %), porque a CPU fica presa em espera ocupada durante milissegundos de busca e rotação. No dispositivo rápido a relação se inverte: o atendimento de 8 blocos leva só 0,064 ms, menos que as 8 interrupções (8 × 0,017 = 0,136 ms), então o polling gastará *menos* CPU que a interrupção.

**Fundamentação:** Silberschatz, 13.2.1 e 13.2.2: polling é eficiente quando o dispositivo responde rápido; interrupções evitam espera ocupada, mas têm custo fixo de tratamento e troca de contexto. Pelo modelo: polling = duração do atendimento; interrupção = `n × (0,015 + 0,002)` ms.

**Configuração:** `x2`: dispositivos `hdd` e `rapido`, cargas aleatória e sequencial, FCFS, modos polling e interrupção. Indicador: `cpu_es_pct` (% do tempo total gasto pela CPU com E/S).

**Resultados:**

| Dispositivo | Carga | Polling (% CPU) | Interrupção (% CPU) | Tempo total (ms) |
|---|---|---:|---:|---:|
| hdd | aleatória | 86,28 | 1,12 | 24.256,7 |
| hdd | sequencial | 99,998 | 0,84 | 32.533,0 |
| rápido | aleatória | 0,53 | 1,12 | 24.244,4 |
| rápido | sequencial | 1,07 | 2,28 | 11.918,8 |

**Evidências:** resultados/es.csv (experimento=X2); graficos/X2_cpu.png

**Análise:**

**HDD.** No polling, a CPU fica presa durante todo o atendimento, então `cpu_es_pct` é a própria utilização do disco: 86% na carga aleatória e 99,998% na sequencial. Na sequencial, o FCFS satura o disco (tempo total de 32,5 s) e a CPU passa praticamente todo o tempo em espera ocupada. A interrupção custa 2.000 × 8 × 0,017 = 272 ms de CPU, o que dá 272 / 24.257 = 1,12%, exatamente o valor medido.

**Dispositivo rápido.** A relação se inverte. O polling gasta 2.000 × 0,064 = 128 ms (0,53%) e a interrupção continua gastando 272 ms (1,12%), ou seja, 2,125 vezes mais. Na carga sequencial o tempo total cai para 11,9 s, então as duas porcentagens dobram, mas a razão continua 2,125.

O ponto de equilíbrio sai das fórmulas: o polling vence quando a duração do atendimento é menor que *n* × 0,017 ms, isto é, menos de 0,017 ms por bloco. O dispositivo rápido gasta 0,008 ms por bloco e o HDD, vários milissegundos. A resposta média é a mesma nos dois modos, porque no modelo a CPU não é um recurso disputado (seção 5).

**Veredito:** CONFIRMADA

### X3 — Acesso sequencial × direto

**Hipótese:** O número de acessos por operação de leitura será parecido nas duas cargas (cerca de 1 bloco por `read` de 4 KiB), mas o tempo por operação será muito maior no banco de dados: cada leitura direta cai numa posição aleatória de uma tabela de 64 MiB e paga busca + latência rotacional (~5 ms), enquanto no compilador as leituras de um arquivo pegam blocos consecutivos, que pelo modelo não pagam rotação (~0,04 ms).

**Fundamentação:** Silberschatz, 11.2 (métodos de acesso sequencial e direto) e 12.4.1 (alocação contígua favorece os dois, mas o sequencial ganha a leitura antecipada natural do disco). Regra do modelo: rotação = 0 se o bloco é exatamente o seguinte ao último transferido.

**Configuração:** `x3`: cargas `fs_compilador` (3 compilações de 40 fontes e 15 cabeçalhos) e `fs_banco_dados` (3.000 transações, 80% leituras, 20% escrita + registro no log), alocação contígua. Indicadores: acessos e tempo por operação (`arquivos.csv` e `arquivos_por_operacao.csv`).

**Resultados:**

| Operação | Compilador: n | acessos/op | ms/op | Banco de dados: n | acessos/op | ms/op |
|---|---:|---:|---:|---:|---:|---:|
| open | 600 | 3,00 | 8,20 | 2 | 2,00 | 4,25 |
| read | 1.719 | 1,00 | 1,48 | 2.405 | 1,00 | 5,49 |
| write | 726 | 1,00 | 0,91 | 1.190 | 1,00 | 5,54 |
| create | 175 | 4,00 | 11,95 | 2 | 3,00 | 8,46 |
| close | 600 | 0,20 | 1,05 | 2 | 1,00 | 4,84 |
| unlink | 80 | 4,00 | 13,69 | — | — | — |
| seek | — | — | — | 3.000 | 0,00 | 0,00 |
| **total** | 3.904 | 1,38 | 3,07 | 6.602 | 0,55 | 3,01 |

Tempo total: compilador 11.986,7 ms; banco de dados 19.847,0 ms.

**Evidências:** resultados/arquivos.csv (experimento=X3); resultados/arquivos_por_operacao.csv; graficos/X3_tempo_por_operacao.png

**Análise:**

Como previsto, cada `read` faz exatamente 1 acesso nas duas cargas, mas no banco de dados ele custa 3,7 vezes mais (5,49 contra 1,48 ms).

**Banco de dados.** Cada leitura direta cai numa posição aleatória da tabela de 16.384 blocos (256 cilindros). Isso custa a rotação (4,17 ms), mais uma busca curta (1 + 0,003 × ~85 cilindros ≈ 1,26 ms) e a transferência (0,04 ms), num total de ≈ 5,47 ms, quase igual aos 5,49 ms medidos. Os `write` (1.190, aproximadamente 2 por transação de escrita: o dado e o registro no log em modo `'a'`) custam o mesmo, porque alternam entre a tabela e o log e pagam rotação em todos.

**Compilador.** O valor de 1,48 ms é uma média. Cada arquivo aberto é lido cerca de 2,9 vezes (1.719 / 600). A primeira leitura paga rotação (~4,2 ms) e as seguintes pegam o "bloco seguinte" (0,04 ms): (600 × 4,2 + 1.119 × 0,04) / 1.719 ≈ 1,49 ms, o que bate com o medido. A estimativa de ~0,04 ms da hipótese vale para as leituras depois da primeira de cada arquivo.

O total de acessos por operação engana. O banco tem 0,55 acesso por operação, contra 1,38 do compilador, porque os 3.000 `seek` não acessam o disco. Mesmo assim, o tempo por operação é praticamente igual (3,01 contra 3,07 ms): o padrão de acesso pesa mais que o número de acessos.

**Veredito:** CONFIRMADA

### X4 — Custo de resolver caminhos conforme a profundidade

**Hipótese:** O número de acessos por `open` crescerá linearmente com a profundidade: um arquivo a *d* diretórios exige ler *d*+1 diretórios (a raiz e os *d* intermediários), ou seja, 2 acessos em *d*=1 e 11 em *d*=10. O tempo por `open`, porém, crescerá muito pouco: como os diretórios foram criados em sequência, seus blocos são vizinhos no disco (todos no cilindro 0), então só a primeira leitura paga a latência rotacional (4,17 ms) e as demais custam só a transferência (0,04 ms). Esperamos algo como 4,17 + 0,04·(*d*+1) ms, de ~4,25 ms a ~4,61 ms.

**Fundamentação:** Silberschatz, 11.3 e 12.3 (diretórios em árvore; cada componente do caminho exige ler um diretório) e a regra de custo do `resolver`. Pela alocação contígua first-fit, a raiz ocupa o bloco 0 e os diretórios `n1…nd` os blocos 1…*d*.

**Configuração:** `x4`: para *d* = 1…10, cria uma cadeia de *d* diretórios e um arquivo de 4 KiB, e abre/lê/fecha o arquivo 20 vezes. Indicadores: acessos e tempo médio por `open`.

**Resultados:**

| Profundidade *d* | Acessos por `open` | Tempo por `open` (ms) | Previsto 4,17 + 0,04·(*d*+1) |
|---:|---:|---:|---:|
| 1 | 2 | 4,25 | 4,25 |
| 2 | 3 | 4,29 | 4,29 |
| 3 | 4 | 4,33 | 4,33 |
| 4 | 5 | 4,37 | 4,37 |
| 5 | 6 | 4,41 | 4,41 |
| 6 | 7 | 4,45 | 4,45 |
| 7 | 8 | 4,49 | 4,49 |
| 8 | 9 | 4,53 | 4,53 |
| 9 | 10 | 4,57 | 4,57 |
| 10 | 11 | 4,61 | 4,61 |

**Evidências:** resultados/arquivos_por_operacao.csv (experimento=X4); graficos/X4_open_por_profundidade.png

**Análise:**

O número de acessos é exatamente *d* + 1 em todas as profundidades, e o tempo bate com a previsão até a segunda casa decimal. De *d* = 1 para *d* = 10, os acessos aumentam 5,5 vezes, mas o tempo só 8,5% (de 4,25 para 4,61 ms).

O motivo está na disposição dos diretórios. Eles foram criados em sequência, então a alocação contígua first-fit os colocou em blocos vizinhos (raiz no bloco 0, `n1…nd` nos blocos seguintes). Só a primeira leitura paga a meia volta (4,17 ms); as demais são "bloco seguinte" e custam só a transferência.

Num disco real envelhecido, os diretórios ficam espalhados e cada nível pagaria busca e rotação (~5 a 10 ms), de modo que o tempo cresceria quase tanto quanto os acessos. É por isso que os SOs reais mantêm cache de diretórios (o dentry cache do Linux; seção 4): um `open` repetido não lê diretório nenhum do disco.

**Veredito:** CONFIRMADA

### X5 — Tabelas de arquivos abertos com vários processos

**Hipótese:** Com 8 processos abrindo 3 das 12 páginas cada, a soma das tabelas por processo chegará a 24 entradas (8 × 3), a maior tabela de um processo terá 3 entradas, e a tabela global terá no máximo 12 entradas (uma por arquivo distinto aberto), menor que a soma, porque aberturas do mesmo arquivo compartilham a entrada global. O maior contador de aberturas ficará entre 4 e 8 (várias páginas populares abertas por vários processos ao mesmo tempo).

**Fundamentação:** Silberschatz, 11.1.2 (tabela de arquivos abertos por processo e tabela global do sistema, com contador de aberturas que só remove a entrada quando chega a zero).

**Configuração:** `x5`: carga `fs_servidor_web` (8 trabalhadores × 40 rodadas, cada um abrindo 3 páginas, lendo-as em blocos de 16 KiB e fechando). Indicadores: `max_tabela_global`, `max_tabela_processo`, `max_soma_tabelas_processo`, `max_contador_aberturas`.

**Resultados:**

| Indicador | Valor |
|---|---:|
| `max_tabela_global` | 12 |
| `max_tabela_processo` | 3 |
| `max_soma_tabelas_processo` | 24 |
| `max_contador_aberturas` | 6 |

Também foram feitos 960 `open` (2 acessos e 4,27 ms cada) e 4.346 `read` (3,65 acessos e 1,18 ms em média), sem nenhum erro.

**Evidências:** resultados/arquivos.csv (experimento=X5); graficos/X5_tabelas.png

**Análise:**

**Tabelas.** Os quatro indicadores ficaram dentro do previsto. No pico, os 8 processos tinham 24 entradas nas tabelas por processo, mas a tabela global tinha só 12, uma por página distinta e metade da soma. As aberturas do mesmo arquivo compartilham o FCB carregado na memória, e o contador de aberturas chegou a 6: seis trabalhadores com a mesma página popular aberta ao mesmo tempo. Cada entrada por processo guarda a própria posição, então um trabalhador começa a ler do início mesmo enquanto outro está no meio do mesmo arquivo.

**Linux e Windows.** É a mesma divisão do Linux: cada `open` cria uma `struct file` (posição e modo) que aponta para o mesmo `inode` em memória, com contador de referências. No Windows, os handles de cada processo apontam para objetos de arquivo do kernel. A entrada global só é removida quando o contador chega a zero, e é isso que permite o unlink adiado do N3.

**Leituras.** A média de 3,65 acessos por `read` de 16 KiB (até 4 blocos) é menor que 4 porque a última leitura de cada página pega só o resto do arquivo.

**Veredito:** CONFIRMADA

### X6 — Cache de blocos (M1)

**Hipótese:** A taxa de acerto crescerá com a capacidade, de forma côncava (ganhos grandes até algumas centenas de blocos e depois decrescentes), porque a carga de reúso segue uma distribuição de Zipf com poucos blocos muito populares. A LRU terá taxa de acerto igual ou maior que a FIFO em todas as capacidades, porque mantém os blocos populares no fim da fila. Write-back fará muito menos escritas físicas que write-through, mas perderá dados numa queda de energia (blocos sujos), enquanto write-through não perde nada.

**Fundamentação:** Silberschatz, 13.4.3 (caching) e 12.6.2 (buffer cache, cache unificado, write-through × gravação atrasada); localidade de referência; a gravação periódica (`INTERVALO_SYNC_MS` = 5 s) limita a janela de perda do write-back.

**Configuração:** `x6`: carga `es_reuso` (3.000 requisições de 1 bloco sobre 4.000 blocos com pesos 1/i, 65% leituras), FCFS, sem cache e com LRU/FIFO de 64 a 2.048 blocos, em write-through e write-back; queda simulada na metade da carga. Indicadores: `acerto_cache_pct`, `escritas_fisicas`, `perdidos_na_queda`, resposta média.

**Resultados:**

Taxa de acerto (%):

| Capacidade (blocos) | LRU write-through | LRU write-back | FIFO write-through | FIFO write-back |
|---:|---:|---:|---:|---:|
| 64 | 39,64 | 39,64 | 31,30 | 33,09 |
| 128 | 48,85 | 49,10 | 42,56 | 43,89 |
| 256 | 57,75 | 57,70 | 53,20 | 53,30 |
| 512 | 64,19 | 64,25 | 60,56 | 60,10 |
| 1.024 | 67,06 | 67,01 | 67,06 | 67,01 |
| 2.048 | 67,06 | 67,01 | 67,06 | 67,01 |

Escritas, perdas na queda e tempos (LRU; sem cache: 1.045 escritas físicas, resposta média de 3.500,0 ms e espera máxima de 6.936,3 ms):

| Capacidade | Escritas físicas WT | Perdidos WT | Escritas físicas WB | Perdidos WB | Resposta média WT / WB (ms) | Espera máx. WT / WB (ms) |
|---:|---:|---:|---:|---:|---:|---:|
| 64 | 1.045 | 0 | 715 | 28 | 74,7 / 23,4 | 257,8 / 418,4 |
| 128 | 1.045 | 0 | 630 | 51 | 33,4 / 26,2 | 158,0 / 690,2 |
| 256 | 1.045 | 0 | 543 | 55 | 22,0 / 47,6 | 151,8 / 1.271,1 |
| 512 | 1.045 | 0 | 541 | 55 | 18,2 / 45,4 | 151,8 / 1.416,8 |
| 1.024 | 1.045 | 0 | 541 | 55 | 16,9 / 38,9 | 151,8 / 1.416,8 |

**Evidências:** resultados/es.csv (experimento=X6); graficos/X6_acerto.png; graficos/X6_escrita.png

**Análise:**

**Acerto.** A curva é côncava, como previsto: +9 pontos de 64 para 128 blocos, +9 de 128 para 256, +6 de 256 para 512 e +3 de 512 para 1.024. Acima disso fica parada em 67,06%. A partir de 1.024 blocos, todos os blocos distintos da carga cabem na cache e as faltas restantes são compulsórias (o primeiro acesso a cada bloco), que nenhuma política evita.

**LRU × FIFO.** A LRU ganha da FIFO por 8,3 pontos com 64 blocos, 6,3 com 128, 4,5 com 256, 3,6 com 512 e 0 com 1.024. A vantagem diminui com a capacidade: numa cache pequena a FIFO expulsa os blocos populares da distribuição de Zipf só porque entraram há mais tempo; numa cache grande quase nada é expulso.

**Write-back × write-through.** O write-back reduziu as escritas físicas em 32% com 64 blocos e em 48% com 512 blocos ou mais. Com a cache grande sobram 541 escritas, aproximadamente uma por bloco distinto escrito, porque as regravações do mesmo bloco se juntam. O custo é a perda de 28 a 55 blocos na queda simulada, contra zero no write-through. A sincronização a cada 5 s limita essa janela.

**Efeito não previsto: a sincronização prejudica a latência.** Com caches de 256 blocos ou mais, o write-back teve espera máxima de até 1,4 s (contra 152 ms do write-through) e até resposta média pior (38,9 contra 16,9 ms com 1.024 blocos). A cada 5 s a sincronização despeja todos os blocos sujos de uma vez na fila FCFS, e as leituras que chegam ficam atrás dessa rajada. Por isso o Linux espalha o writeback em vez de gravar tudo de uma vez.

**Sem cache.** Sem cache, o FCFS fica saturado (resposta média de 3,5 s); qualquer cache tira o disco da saturação.

**Veredito:** CONFIRMADA

### X7 — Escalonadores avançados e inanição (M2)

**Hipótese:** Com o aumento da taxa de chegada, o FCFS satura primeiro (resposta explode). O SSTF terá boa resposta média, mas a maior espera máxima cresce rápido (inanição das requisições fora da região quente). SCAN, C-SCAN, LOOK e C-LOOK limitam a espera máxima; as versões circulares (C-SCAN, C-LOOK) terão espera mais uniforme (p95 menor em relação à média) ao custo de um pouco mais de deslocamento. O DEADLINE terá resposta média próxima à do SSTF e espera máxima limitada perto do prazo de leitura (200 ms) mais o tempo de esvaziar as vencidas.

**Fundamentação:** Silberschatz, 10.4.2–10.4.5; documentação do mq-deadline do Linux (prazos por tipo de requisição para evitar inanição).

**Configuração:** `x7`: carga concentrada (80% dos pedidos em 20% do disco), 2.000 requisições, intervalos médios de 14, 11, 9 e 8 ms; os 7 escalonadores. Indicadores: `resposta_p95_ms`, `espera_max_ms`, resposta média, deslocamento.

**Resultados:**

Resposta média (ms) por intervalo médio entre chegadas:

| Escalonador | 14 ms | 11 ms | 9 ms | 8 ms |
|---|---:|---:|---:|---:|
| FCFS | 15,8 | 26,5 | 61,2 | 281,6 |
| SSTF | 13,4 | 16,7 | 21,9 | 27,8 |
| SCAN | 29,5 | 34,6 | 41,8 | 49,0 |
| C-SCAN | 40,1 | 44,4 | 55,6 | 65,8 |
| LOOK | 13,8 | 17,1 | 24,6 | 31,4 |
| C-LOOK | 14,7 | 19,4 | 28,4 | 38,9 |
| DEADLINE | 13,4 | 16,7 | 22,0 | 28,4 |

Resposta p95 (ms):

| Escalonador | 14 ms | 11 ms | 9 ms | 8 ms |
|---|---:|---:|---:|---:|
| FCFS | 41,3 | 80,4 | 170,5 | 538,0 |
| SSTF | 32,0 | 44,4 | 59,4 | 79,6 |
| SCAN | 57,3 | 73,7 | 94,4 | 116,6 |
| C-SCAN | 78,9 | 89,4 | 113,5 | 142,1 |
| LOOK | 33,2 | 43,0 | 67,6 | 87,5 |
| C-LOOK | 37,0 | 52,3 | 77,6 | 100,2 |
| DEADLINE | 32,0 | 44,4 | 59,7 | 84,8 |

Espera máxima (ms):

| Escalonador | 14 ms | 11 ms | 9 ms | 8 ms |
|---|---:|---:|---:|---:|
| FCFS | 71,3 | 128,2 | 244,5 | 616,2 |
| SSTF | 86,6 | 129,0 | 265,5 | 327,1 |
| SCAN | 114,4 | 127,9 | 210,0 | 226,7 |
| C-SCAN | 116,9 | 146,0 | 172,1 | 223,1 |
| LOOK | 72,8 | 105,1 | 161,9 | 170,3 |
| C-LOOK | 102,8 | 97,4 | 139,4 | 163,8 |
| DEADLINE | 86,6 | 129,0 | 204,2 | 244,8 |

Deslocamento total no intervalo de 8 ms (cilindros): FCFS 1.811.896; SSTF 1.150.844; SCAN 1.529.626; C-SCAN 1.589.526; LOOK 1.165.152; C-LOOK 1.258.318; DEADLINE 1.148.860.

**Evidências:** resultados/es.csv (experimento=X7); graficos/X7_espera_max_ms.png; graficos/X7_resposta_p95_ms.png

**Análise:**

**Saturação.** O FCFS satura primeiro. Com intervalo de 9 ms, sua resposta média já é 2,8 vezes a do SSTF; com 8 ms, chega a 281,6 ms, 10 vezes a do SSTF, com espera máxima de 616 ms. Os escalonadores que ordenam por posição continuam estáveis: o SSTF passa de 13,4 para 27,8 ms.

**Inanição no SSTF.** A espera máxima do SSTF cresce 3,8 vezes (de 86,6 para 327,1 ms) e, a partir de 9 ms, é a maior entre os escalonadores que ordenam por posição. A média continua baixa porque quem fica esperando são as poucas requisições fora da região quente.

**DEADLINE.** Com 14 e 11 ms, o DEADLINE é idêntico ao SSTF, porque nenhuma requisição chegou a vencer o prazo de leitura de 200 ms. Com 9 ms, ele reduz a espera máxima do SSTF em 23% (de 265,5 para 204,2 ms) e, com 8 ms, em 25% (de 327,1 para 244,8 ms). O custo é pequeno: a resposta média sobe 0,4% e 2,3%. A espera máxima fica perto do prazo mais o tempo de esvaziar as requisições vencidas, como previsto.

**LOOK × SCAN.** LOOK e C-LOOK superam SCAN e C-SCAN em todos os indicadores. Com 8 ms, a média é de 31,4 ms contra 49,0 e a espera máxima de 170 contra 227 ms, porque eles não vão até a borda sem necessidade. O C-LOOK teve a menor espera máxima de todos com 9 e 8 ms (139,4 e 163,8 ms).

**Versões circulares.** As versões circulares têm espera mais uniforme. Com 8 ms, a razão p95/média é 2,16 no C-SCAN contra 2,38 no SCAN, e 2,57 no C-LOOK contra 2,79 no LOOK. Em troca, o deslocamento é 4% e 8% maior.

**Exceção em carga leve.** Com 14 ms, SCAN e C-SCAN têm espera máxima maior que FCFS e SSTF (114 e 117 ms, contra 71 e 87). Com fila curta, a ida até a borda é puro custo, o mesmo efeito que apareceu no X1. A hipótese tratava do comportamento à medida que a carga aumenta, e nessa faixa todas as previsões se confirmaram.

**Veredito:** CONFIRMADA

### X8 — DMA e buffering (M3)

**Hipótese:** CPU: o custo da interrupção cresce linearmente com o tamanho da requisição (0,017 ms por bloco), o do DMA é constante (0,018 ms), e o do polling acompanha a duração do atendimento. Com requisições de 1 bloco, o DMA será *ligeiramente mais caro* que a interrupção (0,018 × 0,017 ms); a partir de 2 blocos, o DMA vence, e com 256 blocos a diferença será de mais de 200 vezes. Buffering (produção 2 ms, 500 blocos): com 1 buffer o tempo total é 500 × (2 + consumo) — produtor e dispositivo nunca trabalham juntos; com 2 buffers o tempo cai para 500 × max(2, consumo) + min(2, consumo) (1.000,5 / 1.002 / 4.002 ms para consumo de 0,5 / 2 / 8 ms); 3 buffers não ganham nada sobre 2, porque produção e consumo têm duração constante (o buffer circular só ajuda com variações de ritmo).

**Fundamentação:** Silberschatz, 13.2.3 (DMA: a CPU só programa a transferência e recebe uma interrupção no fim) e 13.4.2 (buffering duplo desacopla produtor e consumidor; buffer circular para ritmos variáveis). Fórmulas do modelo em `cpu.py` e regras em `buffering.py`.

**Configuração:** `x8`: carga aleatória (1.000 requisições, intervalo 40 ms) com 1, 8, 64 e 256 blocos por requisição, modos polling, interrupção e DMA; buffering com 1, 2 e 3 buffers e consumo de 0,5, 2 e 8 ms. Indicadores: `cpu_es_pct`; `tempo_total_ms`, `bloqueio_produtor_ms`, `utilizacao_dispositivo_pct`.

**Resultados:**

CPU gasta com E/S (`cpu_es_pct`):

| Blocos por requisição | Polling | Interrupção | DMA |
|---:|---:|---:|---:|
| 1 | 25,17 | 0,0423 | 0,0448 |
| 8 | 25,87 | 0,338 | 0,0448 |
| 64 | 31,44 | 2,706 | 0,0448 |
| 256 | 50,52 | 10,82 | 0,0448 |

Buffering (produção de 2 ms, 500 blocos):

| Consumo (ms) | Buffers | Tempo total (ms) | Bloqueio do produtor (ms) | Utilização do dispositivo (%) |
|---:|---:|---:|---:|---:|
| 0,5 | 1 | 1.250,0 | 249,5 | 20,0 |
| 0,5 | 2 | 1.000,5 | 0,0 | 25,0 |
| 0,5 | 3 | 1.000,5 | 0,0 | 25,0 |
| 2 | 1 | 2.000,0 | 998,0 | 50,0 |
| 2 | 2 | 1.002,0 | 0,0 | 99,8 |
| 2 | 3 | 1.002,0 | 0,0 | 99,8 |
| 8 | 1 | 5.000,0 | 3.992,0 | 80,0 |
| 8 | 2 | 4.002,0 | 2.988,0 | 99,95 |
| 8 | 3 | 4.002,0 | 2.980,0 | 99,95 |

**Evidências:** resultados/es.csv (experimento=X8); resultados/buffering.csv; graficos/X8_cpu.png; graficos/X8_buffering.png

**Análise:**

**CPU.** O custo do DMA ficou constante em 0,0448%: 1.000 × 0,018 ms / 40.206 ms. Com requisições de 1 bloco, o DMA é ligeiramente mais caro que a interrupção (0,0448% contra 0,0423%, ou 0,018 contra 0,017 ms por requisição). Já com 8 blocos é 7,5 vezes mais barato, e com 256 blocos é 241 vezes mais barato (0,0448% contra 10,82%). O cruzamento acontece entre 1 e 2 blocos, como previsto. O polling vai de 25% para 50% porque a duração de cada atendimento cresce com o tamanho, e ele gasta CPU durante todo esse tempo.

**Buffering.** Os resultados batem com as fórmulas até a casa decimal. Com 1 buffer, o tempo total é 500 × (2 + consumo) = 1.250, 2.000 e 5.000 ms. Com 2 buffers, é 500 × max(2, consumo) + min(2, consumo) = 1.000,5, 1.002 e 4.002 ms. O buffer duplo deixa o recurso mais lento ocupado quase 100% do tempo: o dispositivo quando o consumo é de 2 ou 8 ms, e o produtor (sem bloqueio) quando o consumo é de 0,5 ms.

Um terceiro buffer não muda o tempo total. Produção e consumo têm duração constante, então depois do início a folga extra nunca é usada. O único efeito é 8 ms a menos de bloqueio do produtor (um bloco produzido antes). O buffer circular só ajudaria se os ritmos variassem.

**Veredito:** CONFIRMADA

### X9 — Links e compartilhamento (M4)

**Hipótese:** O arquivo acessado pelo hard link continuará legível depois do unlink do nome original (o FCB só é liberado quando `links` chega a 0), enquanto o link simbólico para `b.txt` ficará pendente (`ENOENT`). O laço `l1 → l2 → l1` dará `ELOOP`; o hard link para diretório dará `EPERM`. A travessia de `/` detectará ciclos (o link `/dados/sub/volta → /atalhos` fecha um laço com `/atalhos/dados_simbolico → /dados`) e terminará, sem entrar em laço infinito. Abrir pelo link simbólico custará mais leituras que pelo caminho real, porque a resolução recomeça da raiz.

**Fundamentação:** Silberschatz, 11.3.6–11.3.7 (diretórios em grafo acíclico e em grafo geral: hard links com contador de referências, links simbólicos, ponteiros pendentes, ciclos e a necessidade de evitá-los na travessia).

**Configuração:** `x9`: carga `fs_links`. Indicadores: erros por código (`erro_ENOENT`, `erro_ELOOP`, `erro_EPERM`), `ciclos_detectados`, `links_pendentes`, `links_em_laco`, `percorrer_diretorios`, `percorrer_arquivos` (coluna `extras` de `arquivos.csv`) e acessos médios do `open`.

**Resultados:**

| Indicador (coluna `extras`) | Valor |
|---|---:|
| `erro_ENOENT` | 1 |
| `erro_ELOOP` | 1 |
| `erro_EPERM` | 1 |
| `ciclos_detectados` | 5 |
| `links_pendentes` | 3 |
| `links_em_laco` | 6 |
| `percorrer_diretorios` | 11 |
| `percorrer_arquivos` | 9 |

Por operação: 13 `open` (2 erros), com média de 4,92 acessos; 14 `read`, sem erros; 3 `percorrer`, com média de 44 acessos; 2 `link` (1 erro).

Acessos de cada `open`, calculados pelas regras do `resolver` (a soma, 64 / 13 = 4,92, bate com a média do CSV):

| Caminho aberto | Leituras de diretório | Pelo caminho real |
|---|---:|---:|
| `/atalhos/a_rigido` (hard link) | 2 | 2 (`/dados/a.txt`) |
| `/atalhos/b_simbolico` | 4 | 2 (`/dados/b.txt`) |
| `/atalhos/dados_simbolico/sub/c.txt` | 5 | 3 (`/dados/sub/c.txt`) |
| `/atalhos/dados_simbolico/sub/volta/a_rigido` | 7 | 2 |
| `/atalhos/l1` (laço, ELOOP) | 18 | — |

**Evidências:** resultados/arquivos.csv (experimento=X9); resultados/arquivos_por_operacao.csv

**Análise:**

**Erros.** Cada erro aconteceu onde foi previsto:
- **`ENOENT`:** `open /atalhos/b_simbolico` depois do `unlink /dados/b.txt`. O link simbólico guarda só o nome do alvo, que deixou de existir (ponteiro pendente).
- **`ELOOP`:** `open /atalhos/l1`, que resolve `l1 → l2 → l1 …` e é interrompido no 9º link, depois de 18 leituras de diretório.
- **`EPERM`:** `link /dados /atalhos/dir_rigido`. Hard links para diretório são proibidos justamente para impedir ciclos que o contador de referências não detecta.

**Hard link.** O hard link sobreviveu ao `unlink` do nome original: as duas leituras por `/atalhos/a_rigido` depois do unlink (23,0 e 23,5 ms) não deram erro. O `unlink` só reduziu `links` de 2 para 1.

**Custo do link simbólico.** Abrir por um link simbólico custa mais, porque a resolução recomeça da raiz com o caminho do alvo: 4 leituras contra 2 pelo caminho real em `b_simbolico`, e 5 contra 3 em `c.txt`. O hard link não tem custo extra, porque é só outra entrada de diretório apontando para o mesmo FCB.

**Travessia.** As 3 travessias terminaram. Os 5 `ciclos_detectados` incluem o laço real `/dados/sub/volta → /atalhos → dados_simbolico → /dados`, mas também revisitas sem ciclo: o mesmo diretório alcançado por dois caminhos, por exemplo `/dados` e `/atalhos/dados_simbolico`. Seguimos a regra do enunciado (toda revisita conta), que é o que o `find -L` do GNU faz ao reconhecer diretórios pelo par (dispositivo, inode). Os 6 `links_em_laco` são `l1` e `l2` em cada uma das 3 travessias. Os 3 `links_pendentes` são o `b_simbolico` nas duas travessias depois do unlink, mais o `open` que falhou.

**Veredito:** CONFIRMADA

### X10 — Proteção e travas (M5)

**Hipótese:** As verificações de permissão de bob em `notas.txt` custarão 11 (a entrada dele é a 11ª da ACL, depois de 10 usuários), mostrando que uma ACL longa torna cada `open` mais caro que a checagem dos bits rwx (1 comparação). carol (fora da ACL, sem bits de outros) e o `open` de bob para escrita serão negados (`EACCES`). Nas travas, o modo consultivo deixará passar as escritas e leituras do processo sem a trava (contadas em `violacoes_consultivo`, sem erros), e o modo mandatório transformará exatamente essas operações em `EBUSY` (`bloqueados_mandatorio`), com o mesmo número de travas negadas nos dois modos.

**Fundamentação:** Silberschatz, 11.6.2 (listas de acesso × bits de dono/grupo/outros: ACLs são mais expressivas, porém mais longas e caras de consultar) e 11.1.2 (travas consultivas × mandatórias).

**Configuração:** `x10`: carga `fs_protecao`, rodada com bloqueio consultivo e mandatório. Indicadores: `verificacoes`, `negados`, `travas_concedidas`, `travas_negadas`, `violacoes_consultivo`, `bloqueados_mandatorio`, erros por código.

**Resultados:**

| Indicador | Consultivo | Mandatório |
|---|---:|---:|
| `verificacoes` | 961 | 961 |
| `negados` (`EACCES`) | 41 | 41 |
| `erro_EPERM` | 1 | 1 |
| `travas_concedidas` | 40 | 40 |
| `travas_negadas` | 20 | 20 |
| `violacoes_consultivo` | 40 | — |
| `bloqueados_mandatorio` | — | 40 |
| `erro_EBUSY` | 20 | 60 |
| erros (total) | 62 | 102 |
| acessos ao disco | 613 | 573 |
| tempo total (ms) | 1.092,0 | 923,6 |

Decomposição das 961 verificações, calculada pelas regras do M5 (o total bate exatamente com o CSV):

| Quem / arquivo | Aberturas | Custo de cada uma | Total |
|---|---:|---:|---:|
| bob, `notas.txt`, leitura (entrada 11 da ACL) | 20 | 11 | 220 |
| bob, `notas.txt`, escrita (negada pela entrada `r--`) | 20 | 11 | 220 |
| bob, `notas.txt`, antes da ACL (bits 600) | 1 | 1 | 1 |
| carol, `notas.txt` (fora da ACL; bits de outros) | 20 | 12 | 240 |
| alice, `notas.txt` (dona, fora da ACL) | 20 | 12 | 240 |
| alice e bob, `conta.txt` (sem ACL) | 40 | 1 | 40 |
| **total** | 121 | | **961** |

**Evidências:** resultados/arquivos.csv (experimento=X10); resultados/arquivos_por_operacao.csv

**Análise:**

**Custo da ACL.** Em `notas.txt`, com 11 entradas na ACL, cada abertura custa de 11 a 12 comparações. Em `conta.txt`, só com os bits rwx, custa 1. Até a dona paga 12, porque a ACL é percorrida antes dos bits. É o compromisso de 11.6.2: a ACL é mais expressiva, mas fica mais cara à medida que cresce. No NTFS, que só tem ACL, os descritores longos também são percorridos entrada por entrada.

**Negações.** Os 41 `EACCES` são 20 de carol (fora da ACL e sem bits de "outros", com `notas.txt` em 600), 20 de bob tentando escrever (a primeira entrada dele, `r--`, decide e nega) e 1 abertura de bob antes de a ACL existir. O `EPERM` é o `chmod` feito por bob, que não é o dono.

**Travas.** Nos dois modos, as travas concedidas e negadas são as mesmas (40 e 20), porque a disputa pela trava não depende do modo. A diferença está nas operações de quem não tem a trava. No modo consultivo, elas passam e são só contadas (40 violações, sem erro). No mandatório, exatamente essas 40 viram `EBUSY`: 20 `read` e 20 `write` com erro, o que dá 60 `EBUSY` no total com as 20 travas negadas. Por isso o modo mandatório faz 40 acessos a menos e termina antes.

**Sistemas reais.** As travas consultivas (`flock`/`fcntl` no Linux e no macOS) só protegem os processos que cooperam. As mandatórias (`LockFileEx` e os modos de compartilhamento do `CreateFile` no Windows) protegem de todos, mas fazem operações legítimas falharem.

**Veredito:** CONFIRMADA

### X11 — Métodos de alocação (M6)

**Hipótese:** A alocação contígua sofrerá fragmentação externa numa partição 85% cheia: haverá realocações ao estender arquivos (cada uma copia o arquivo inteiro) e possivelmente falhas `ENOSPC` mesmo com espaço livre total suficiente. Encadeada, FAT e indexada nunca falharão por fragmentação. Nos acessos diretos aos 5 maiores arquivos, a encadeada será de longe a pior (cada salto para trás relê a corrente desde o início, dezenas de leituras por `read`); a FAT terá o menor custo por acesso (ponteiros em memória); a indexada pagará 1 leitura extra de índice por bloco. A contígua terá o menor tempo de leitura sequencial, por ter blocos fisicamente vizinhos.

**Fundamentação:** Silberschatz, 12.4.1–12.4.3 (contígua: acesso direto barato, fragmentação externa; encadeada: sem fragmentação, acesso direto caro; FAT: ponteiros numa tabela cacheável; indexada: acesso direto com o custo do índice, índice multinível) e 12.5 (bitmap de espaço livre).

**Configuração:** `x11`: carga `fs_alocacao` (partição de 20.000 blocos enchida até 70%, 40% dos arquivos removidos, nova ocupação até 85%, 60 arquivos estendidos em modo acréscimo, 100 leituras diretas e uma leitura sequencial dos 5 maiores). Indicadores: `acessos_por_op`, `tempo_medio_op_ms`, `realocacoes`, `falhas_alocacao`, `segmentos_livres`, `maior_segmento_livre`, `descontinuidades_medias`.

**Resultados:**

| Alocador | Erros | Acessos/op | ms/op | Realocações | Falhas de alocação | Segmentos livres | Maior segmento livre | Blocos livres | Descontinuidades médias |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| contígua | 113 | 7,35 | 34,55 | 59 | 113 | 50 | 154 | 2.052 | 0,00 |
| encadeada | 0 | 77,52 | 12,73 | 0 | 0 | 1 | 1.939 | 1.939 | 0,64 |
| FAT | 0 | 1,90 | 4,01 | 0 | 0 | 1 | 1.939 | 1.939 | 0,64 |
| indexada | 0 | 4,29 | 14,32 | 0 | 0 | 1 | 1.805 | 1.805 | 0,65 |

`read` (580) e `write` (1.029) por alocador:

| Alocador | read: acessos | read: ms | write: acessos | write: ms |
|---|---:|---:|---:|---:|
| contígua | 3,04 | 4,41 | 15,10 | 77,98 |
| encadeada | 74,91 | 6,40 | 142,86 | 24,47 |
| FAT | 3,06 | 4,43 | 2,00 | 4,66 |
| indexada | 6,12 | 18,98 | 5,00 | 19,97 |

**Evidências:** resultados/arquivos.csv (experimento=X11); resultados/arquivos_por_operacao.csv; graficos/X11_alocacao.png; graficos/X11_fragmentacao.png

**Análise:**

**Contígua.** A fragmentação externa apareceu como previsto. Sobraram 2.052 blocos livres, mas em 50 pedaços, e o maior tem só 154 blocos. Resultado: 113 extensões falharam com `ENOSPC` mesmo com espaço livre total suficiente, e 59 realocações copiaram o arquivo inteiro. Por isso o `write` da contígua custa 15 acessos e 78 ms, e ela tem o *pior* tempo médio por operação (34,6 ms). Encadeada, FAT e indexada nunca falharam e deixaram o espaço livre num único trecho. Em troca, os arquivos ficaram com cerca de 64% dos blocos fora de sequência.

**Encadeada.** Em acessos, a encadeada foi de longe a pior: 74,9 por `read` e 142,9 por `write`, contra cerca de 3 da FAT. O cursor só ajuda para a frente; num acesso direto para trás ou numa extensão no fim, ela relê a corrente desde o primeiro bloco. Em tempo, porém, ela gasta só 6,4 ms por `read`. Os blocos da corrente foram alocados em sequência (primeiros livres), então quase todas as leituras de ponteiro são "bloco seguinte" e custam 0,04 ms. Num disco envelhecido, cada ponteiro pagaria busca e rotação e ela seria inviável.

**FAT.** Com a mesma disposição de blocos, mas com os ponteiros em memória, a FAT empata com a contígua na leitura (3,06 acessos e 4,43 ms, contra 3,04 e 4,41) e ganha dela na escrita. Teve o melhor resultado geral: 1,90 acesso e 4,01 ms por operação.

**Indexada.** Paga 1 leitura de índice por bloco de dados, exatamente o dobro dos acessos da FAT (6,12 contra 3,06), mas foi a mais lenta na leitura (18,98 ms). Alternar índice e dado quebra a regra do "bloco seguinte" e faz *cada* acesso pagar a rotação.

**Onde a hipótese errou.** Ela previu a encadeada como pior em custo e a contígua como melhor na leitura. Em acessos, a encadeada é mesmo a pior, mas em tempo a indexada foi pior, e a contígua só empatou com a FAT.

**Sistemas reais.** O resultado explica as escolhas dos sistemas reais. A FAT32 e a exFAT mantêm a tabela em cache. O ext2/ext3 mantinham os blocos de índice em cache, o que elimina o custo que medimos. O ext4, o NTFS e o APFS usam *extents*, que dão leitura sequencial como a contígua sem a fragmentação que derrubou a contígua aqui.

**Veredito:** PARCIALMENTE CONFIRMADA

### X12 — Integração ponta a ponta

**Hipótese:** As requisições do compilador (muitas leituras pequenas e vizinhas, com reúso de diretórios e cabeçalhos) terão alta taxa de acerto numa cache LRU de 1.024 blocos e tempo de resposta muito menor com cache. No banco de dados (leituras aleatórias numa tabela de 16.384 blocos), a cache ajudará pouco. Como a camada de arquivos emite as requisições em sequência, a fila do escalonador será quase sempre curta, e FCFS, SSTF e SCAN terão resultados parecidos; a diferença entre escalonadores aparecerá só onde uma operação gera vários acessos no mesmo instante. Com a alocação indexada, cada leitura de dado vem acompanhada da leitura do índice, o que aumenta o número de requisições, mas a cache deve absorver as leituras repetidas do índice.

**Fundamentação:** Silberschatz, 12.6 (eficiência e desempenho: buffer cache, leitura antecipada, localidade) e 10.4 (escalonamento só faz diferença com fila).

**Configuração:** `x12`: cargas `fs_compilador` e `fs_banco_dados` com alocação contígua e indexada; o histórico da camada de blocos vira requisições de E/S e passa por FCFS/SSTF/SCAN, sem cache e com LRU write-back de 1.024 blocos. Indicadores: resposta média, deslocamento, acerto de cache.

**Resultados:**

| Carga + alocador | Requisições | Escalonador | Sem cache: resposta média (ms) | Sem cache: deslocamento | LRU 1.024 WB: resposta média (ms) | LRU: deslocamento | LRU: acerto (%) |
|---|---:|---|---:|---:|---:|---:|---:|
| compilador + contígua | 5.400 | FCFS | 4.970,9 | 6.900 | 0,23 | 104 | 90,4 |
| | | SSTF | 5.170,1 | 10 | 0,15 | 80 | 90,4 |
| | | SCAN | 5.170,1 | 10 | 0,94 | 49.996 | 90,4 |
| compilador + indexada | 11.705 | FCFS | 14.961,0 | 10.772 | 0,17 | 116 | 95,7 |
| | | SSTF | 10.563,6 | 11 | 0,15 | 90 | 95,7 |
| | | SCAN | 10.563,6 | 11 | 0,75 | 39.998 | 95,7 |
| banco + contígua | 3.610 | FCFS | 6.158,8 | 360.570 | 2.151,3 | 260.926 | 4,9 |
| | | SSTF | 4.106,2 | 1.518 | 1.411,9 | 2.697 | 4,8 |
| | | SCAN | 4.055,1 | 19.996 | 1.382,4 | 40.249 | 5,1 |
| banco + indexada | 10.314 | FCFS | 22.146,1 | 924.508 | 765,1 | 263.584 | 74,5 |
| | | SSTF | 17.353,2 | 257 | 509,5 | 2.541 | 74,6 |
| | | SCAN | 17.353,2 | 257 | 493,2 | 40.249 | 74,5 |

Escritas físicas: compilador 1.109 (contígua) e 2.014 (indexada) sem cache, contra 0 com cache; banco 1.196 e 1.290 sem cache, contra 670 e 690 com cache.

**Evidências:** resultados/es.csv (experimento=X12); graficos/X12_resposta.png

**Análise:**

**A fila não é curta.** A hipótese errou ao supor fila curta. O instante de cada requisição é o da operação na carga (o simulador copia o tempo da carga para `camada.instante_ms`), e não o instante em que o acesso anterior terminou. O compilador emite 5.400 acessos em cerca de 1,95 s, mas o HDD precisa de 12 s para atendê-los. Por isso, sem cache, a resposta média fica entre 4 e 22 s: quase tudo é fila.

**Escalonadores.** Mesmo com fila longa, o efeito dos escalonadores depende da carga.
- **Compilador:** todos os blocos ficam em poucos cilindros (6.900 cilindros de deslocamento em 5.400 requisições no FCFS), então a busca é irrelevante. SSTF e SCAN reduzem o tempo total (de 12,0 para 10,0 s) porque agrupam pedidos do mesmo cilindro, mas a resposta média sobe 4%.
- **Banco de dados:** SSTF e SCAN cortam a resposta em 33% na contígua e 22% na indexada.
- **Com cache:** o SCAN fica *pior*, com 0,94 contra 0,23 ms e deslocamento de 49.996 cilindros. Com fila quase vazia, ele vai até o cilindro 4.999 e volta a cada varredura, o mesmo defeito do X1.

**Cache.** No compilador, os acertos chegam a 90,4% (contígua) e 95,7% (indexada), e a resposta cai quatro ordens de grandeza (de 4.971 para 0,23 ms). No banco com alocação contígua, o acerto é só 4,9%, como previsto, mas a resposta cai mesmo assim 65% (de 6.159 para 2.151 ms). Esse ganho vem do write-back absorver as escritas (de 1.196 para 670 físicas) e encurtar a fila, não dos acertos.

**Indexada.** A alocação indexada gera 2,2 vezes (compilador) e 2,9 vezes (banco) mais requisições e, sem cache, é 3 a 3,6 vezes mais lenta. Com cache, os blocos de índice são reaproveitados (acerto de 74,5% no banco), e o banco com indexada passa a ser *mais rápido* que com contígua (765 contra 2.151 ms). A cache esconde o índice, como previsto.

**Cuidado com a queda.** No compilador com write-back, nenhum bloco chegou ao disco (0 escritas físicas), porque a carga termina antes da primeira sincronização (5 s). Numa queda, tudo isso se perderia.

**Veredito:** PARCIALMENTE CONFIRMADA

### X13 — Armazenamento flash (bônus)

**Hipótese:** O dispositivo flash terá resposta média muitas vezes menor que a do disco rígido nas duas cargas, porque não tem busca nem latência rotacional (ler 8 páginas custa 0,2 ms; no HDD, uma requisição aleatória custa ~10 ms). As escritas serão mais lentas que as leituras no flash (0,2 ms por página × 0,025 ms). Nas cargas oficiais, a amplificação de escrita será 1,0 e não haverá apagamentos: elas gravam no máximo alguns milhares de páginas num dispositivo de ~342 mil páginas físicas, então a coleta de lixo nunca é acionada. Para mostrar a amplificação, o teste `test_regravacao_gera_coleta_e_amplificacao` usa um dispositivo pequeno e cheio, em que as regravações forçam a coleta de lixo e a amplificação fica acima de 1.

**Fundamentação:** Silberschatz, 10.1.2 (dispositivos NVM/SSD; na 10ª edição, 11.1.2: gravação por página, apagamento por bloco, FTL, coleta de lixo e área de reserva) e a regra do modelo em `disco.py`.

**Configuração:** `x13`: cargas `es_reuso` e `es_aleatoria`, FCFS, interrupção, dispositivos `hdd` e `flash`. Indicadores: resposta média, tempo total, `paginas_gravadas_host`, `paginas_programadas`, `apagamentos`, `amplificacao_escrita` (coluna extras).

**Resultados:**

| Carga | Dispositivo | Resposta média (ms) | p95 (ms) | Espera máx. (ms) | Tempo total (ms) | Deslocamento | Páginas do host | Páginas programadas | Apagamentos | Amplificação |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| reúso | hdd | 3.499,95 | 6.427,5 | 6.936,3 | 30.793,7 | 5.073.258 | — | — | — | — |
| reúso | flash | 0,087 | 0,200 | 0,185 | 23.849,4 | 0 | 1.045 | 1.045 | 0 | 1,0 |
| aleatória | hdd | 50,10 | 145,1 | 193,5 | 24.256,7 | 3.316.472 | — | — | — | — |
| aleatória | flash | 0,644 | 1,600 | 1,668 | 24.244,5 | 0 | 4.792 | 4.792 | 0 | 1,0 |

Teste extra `test_regravacao_gera_coleta_e_amplificacao` (dispositivo de 256 páginas, cheio e regravado): apagamentos > 0 e amplificação > 1, com o mapeamento consistente. O teste passou.

**Evidências:** resultados/es.csv (experimento=X13)

**Análise:**

**Resposta.** O flash respondeu 78 vezes mais rápido na carga aleatória (0,644 contra 50,1 ms) e cerca de 40.000 vezes mais rápido na de reúso. Na de reúso, o HDD com FCFS está saturado (3,5 s de fila), e o flash nem chega a formar fila.

**Leitura × escrita.** A assimetria entre leitura e escrita aparece nos números. Na carga de reúso, com 1 página e 65% de leituras, a média esperada é 0,65 × 0,025 + 0,35 × 0,2 = 0,086 ms, e o medido foi 0,087. Na aleatória, com 8 páginas, uma leitura custa 0,2 ms e uma escrita 1,6 ms, e o p95 de 1,6 ms é justamente o das escritas.

**Tempo total.** Na carga aleatória, o tempo total quase não muda (24.244 contra 24.257 ms), porque o HDD não estava saturado e o fim é ditado pela última chegada: o flash melhora a latência, não a vazão. Na de reúso, cai de 30,8 para 23,8 s, porque tira o disco da saturação.

**Amplificação e coleta.** A amplificação foi 1,0, sem nenhum apagamento: as cargas gravam no máximo 4.792 páginas num dispositivo de ~342 mil, então a coleta de lixo nunca roda. Num dispositivo cheio, o teste extra mostra o outro lado: cada coleta copia as páginas válidas da vítima antes de apagá-la e a amplificação passa de 1. É isso que o TRIM (que evita copiar dados já apagados) e a área de reserva de 7% reduzem nos SSDs reais.

**Veredito:** CONFIRMADA

## 4. Relação com sistemas reais

**Escalonamento de disco.** O Linux atual usa a camada de blocos multifila (blk-mq) com três escalonadores: `mq-deadline`, `bfq` e `none`. O `mq-deadline` é o que o nosso DEADLINE imita: ordena por setor para reduzir buscas, mas mantém filas FIFO por tipo com prazos (por padrão 500 ms para leituras e 5 s para escritas, que o simulador reduziu para 200 ms e 2 s), e atende uma requisição vencida antes de continuar a varredura — exatamente o compromisso entre desempenho do SSTF e ausência de inanição que o X7 mede. O BFQ distribui a banda do disco entre processos (justiça e baixa latência para aplicações interativas). Em SSDs NVMe o padrão é `none`: sem braço, reordenar não traz ganho, como mostra o X13. Windows e macOS não expõem um "elevador" configurável: o Windows usa prioridades de E/S no gerenciador de E/S e deixa a reordenação para o driver e para o firmware do disco (NCQ), e o macOS também confia no controlador. O NCQ dos discos SATA faz, no próprio disco, uma ordenação parecida com SSTF/SCAN que leva em conta também a posição rotacional.

**Polling, interrupção e DMA.** Todos os SOs usam DMA para disco e rede. O resultado do X2 (polling melhor num dispositivo rápido) é o motivo do polling de E/S do Linux para NVMe (`io_poll`, `io_uring` com `IORING_SETUP_IOPOLL`) e do NAPI em placas de rede: quando o dispositivo responde em microssegundos, o custo da interrupção e da troca de contexto passa a dominar. O "interrupt coalescing" (uma interrupção para vários pacotes) segue a mesma lógica do DMA do X8.

**Cache e gravação periódica.** Linux, Windows e macOS têm cache unificado de páginas com gravação atrasada (write-back). No Linux, a thread de writeback acorda a cada `dirty_writeback_centisecs` = 500 (5 s, o mesmo `INTERVALO_SYNC_MS` do modelo) e grava páginas sujas há mais de `dirty_expire_centisecs` = 3000 (30 s). Quem precisa de durabilidade chama `fsync`/`FlushFileBuffers`, ou abre com `O_SYNC`/`FILE_FLAG_WRITE_THROUGH` — o write-through do X6. A política de substituição real não é LRU pura: o Linux usa listas ativa/inativa (e, mais recentemente, MGLRU), aproximações de LRU resistentes a varreduras sequenciais, que na FIFO e na LRU pura expulsam os blocos quentes.

**Resolução de caminhos e tabelas de arquivos abertos.** O custo linear do X4 é o motivo do dentry cache do Linux (e dos caches de nomes do NTFS e do APFS): na prática, `open` de um caminho já visto não lê diretório nenhum do disco. A tabela global × por processo do X5 corresponde, no Linux, à `struct file` (posição e modo, por abertura) apontando para o `inode` em memória (compartilhado, com contador de referências); no Windows, aos handles da tabela do processo apontando para objetos de arquivo do kernel.

**Links.** Unix/Linux e macOS têm hard links (contador `st_nlink` no inode) e links simbólicos; o Linux desiste após 40 links simbólicos (`ELOOP`) e proíbe hard links para diretórios, como o modelo. O NTFS tem hard links, links simbólicos e junctions; o APFS tem hard links e links simbólicos. O `find` do GNU detecta laços pelo par (dispositivo, inode) dos diretórios já visitados, como o nosso `percorrer`.

**Proteção e travas.** Linux e macOS usam bits rwx de dono/grupo/outros com ACLs POSIX opcionais (Linux) ou ACLs no estilo NFSv4 (macOS); o Windows/NTFS usa apenas ACLs (descritores de segurança com ACEs), cuja avaliação percorre a lista como no M5. As travas `flock`/`fcntl` do Linux e do macOS são consultivas — o suporte do Linux a travas mandatórias foi removido no kernel 5.15. O Windows aplica travas mandatórias: `LockFileEx` trava faixas de bytes e os modos de compartilhamento de `CreateFile` impedem até a abertura, o que explica o famoso "o arquivo está aberto em outro programa".

**Alocação.** FAT32/exFAT (pendrives, cartões SD) são alocação encadeada com tabela, como o `AlocadorFAT`: simples e portáveis, com a tabela em cache na memória. O ext2/ext3 usavam a alocação indexada multinível do Unix (ponteiros diretos e indiretos); o ext4 troca isso por *extents* (trechos contíguos), assim como o NTFS (data runs na MFT) e o APFS (B-tree de extents) — um meio termo que mantém a leitura sequencial da contígua sem a fragmentação externa que o X11 mostra. Esses sistemas também atrasam a alocação (delayed allocation no ext4) para escolher trechos contíguos maiores.

**Flash.** SSDs e o armazenamento de celulares têm uma FTL no firmware com coleta de lixo e área de reserva (os ~7% do modelo correspondem à diferença entre GB e GiB que os fabricantes reservam). O comando TRIM/DISCARD (`fstrim` no Linux, "Otimizar unidades" no Windows, TRIM automático no APFS) informa as páginas que o SO apagou, para que a coleta não copie dados mortos — o que reduz a amplificação de escrita medida no teste do bônus.

## 5. Limitações do simulador

- **Disco simplificado:** busca linear na distância (discos reais aceleram e desaceleram o braço), latência rotacional fixa em meia volta (na realidade depende da posição angular) e cilindros com o mesmo número de blocos (discos reais usam *zoned bit recording*). Isso favorece o SSTF/SCAN no X1 e torna o tempo do X4 artificialmente previsível.
- **Sem cache no disco nem NCQ:** discos reais leem trilhas inteiras para o cache interno e reordenam pedidos no firmware, o que reduziria a vantagem dos escalonadores do SO.
- **Uma requisição por vez, sem preempção nem fusão:** o kernel real funde requisições vizinhas (merge) e tem várias filas por CPU; o modelo não, o que aumenta a fila e o número de buscas.
- **Sistema de arquivos sem cache:** cada `open` relê todos os diretórios; nos SOs reais o dentry cache e o page cache eliminariam quase todo o custo do X4 e do X12.
- **Diretórios de 1 bloco, sem journaling e sem metadados separados:** o FCB "mora" no bloco do pai; não há i-nodes nem tabela MFT, e o close grava só o pai. Sistemas com journaling fariam escritas adicionais.
- **Cargas sintéticas e determinísticas:** uma semente por carga; não medimos variância entre execuções. No buffering, produção e consumo constantes escondem o benefício do buffer circular.
- **Flash simplificado:** sem nivelamento de desgaste, sem paralelismo entre canais/chips e sem TRIM; as cargas oficiais não enchem o dispositivo, então a amplificação de escrita só aparece no teste extra.
- **CPU:** o custo de CPU é somado, mas a CPU não é um recurso disputado (não atrasa a E/S), então o polling não "rouba" tempo de outros processos no modelo.

## 6. Conclusões

1. **Escalonar pela posição só compensa quando há fila.** Sob carga, SSTF e SCAN cortaram a resposta do FCFS de 45% (rajadas) até 590 vezes (sequencial). Com fila curta, o SCAN chega a ser pior que o FCFS (33,2 contra 19,8 ms na carga concentrada), porque vai até a borda sem necessidade. LOOK e C-LOOK resolvem isso e tiveram as menores esperas máximas com carga alta (164 a 170 ms com intervalo de 8 ms).
2. **O DEADLINE combina desempenho e justiça.** Ele se comporta exatamente como o SSTF enquanto nenhuma requisição vence o prazo e, sob carga, limita a espera máxima perto do prazo de 200 ms. Pagou só +2,3% de resposta média por −25% de espera máxima, que é a escolha do `mq-deadline` do Linux.
3. **O mecanismo de E/S deve acompanhar a velocidade do dispositivo.** O polling desperdiça de 86% a 100% da CPU num HDD, mas gasta menos da metade da interrupção num dispositivo que responde em 0,064 ms. O DMA vence a interrupção a partir de 2 blocos. O buffer duplo reduz o tempo para 500 × max(p, c) + min(p, c), e um terceiro buffer não ajuda quando os ritmos são constantes.
4. **A cache é a otimização mais forte, mas tem custos escondidos.** Uma LRU de 1.024 blocos reduziu a resposta do compilador de 4,97 s para 0,23 ms (acerto de 90%) e escondeu a leitura extra do índice da alocação indexada (acerto de 74,5% no banco). O write-back reduziu as escritas físicas em até 48%, mas perdeu até 55 blocos na queda e, a cada sincronização de 5 s, criou rajadas que elevaram a espera máxima para 1,4 s.
5. **A alocação deve ser julgada pelo tempo e pela fragmentação, não só por acessos.** Abrir um arquivo custa *d* + 1 acessos, mas só 4,17 + 0,04·(*d*+1) ms quando os diretórios estão contíguos. A FAT foi a melhor alocação geral (1,90 acesso e 4,01 ms por operação). A contígua sofreu fragmentação externa (113 falhas e 59 realocações), e a indexada sem cache de índice foi a mais lenta na leitura.
6. **O flash elimina busca e rotação.** A resposta média foi 78 vezes menor na carga aleatória, e as escritas ficaram 8 vezes mais lentas que as leituras. A amplificação de escrita só aparece com o dispositivo cheio: 1,0 nas cargas oficiais e maior que 1 no teste extra.

## 7. Referências

- SILBERSCHATZ, A.; GALVIN, P. B.; GAGNE, G. *Operating System Concepts*. 9. ed. Wiley, 2013. Cap. 10 a 13.
- Linux kernel documentation. *Deadline IO scheduler tunables*. <https://docs.kernel.org/block/deadline-iosched.html>
- Linux kernel documentation. *BFQ (Budget Fair Queueing)*. <https://docs.kernel.org/block/bfq-iosched.html>
- Linux kernel documentation. *Documentation for /proc/sys/vm/* (dirty_writeback_centisecs, dirty_expire_centisecs). <https://docs.kernel.org/admin-guide/sysctl/vm.html>
- Linux kernel documentation. *ext4 Data Structures and Algorithms*. <https://docs.kernel.org/filesystems/ext4/index.html>
- Linux man-pages. *path_resolution(7)*, *symlink(7)*, *fcntl(2)*, *flock(2)*. <https://man7.org/linux/man-pages/>
- Microsoft Learn. *LockFileEx function (fileapi.h)*. <https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex>
- Microsoft Learn. *exFAT file system specification*. <https://learn.microsoft.com/en-us/windows/win32/fileio/exfat-specification>
- Microsoft Learn. *NTFS overview*. <https://learn.microsoft.com/en-us/windows-server/storage/file-server/ntfs-overview>
- Apple. *Apple File System Reference*. <https://developer.apple.com/support/downloads/Apple-File-System-Reference.pdf>
- Python Software Foundation. *collections.OrderedDict*. <https://docs.python.org/3/library/collections.html#collections.OrderedDict>

## 8. Declaração de autoria e uso de IA

**Quem fez o quê:** [preencher: divisão do trabalho entre os integrantes]

**Uso de ferramentas de IA:**

| Ferramenta | Finalidade | Onde foi usada (arquivos ou seções) |
|---|---|---|
| Claude (Claude Code, Anthropic) | Implementação do código marcado com TODO, testes extras e rascunho do relatório (hipóteses, fundamentação, implementação, resultados e análises a partir dos CSVs gerados pelo grupo, seções 4 a 6), revisados pelo grupo | `simes/es/escalonadores.py`, `simes/es/cpu.py`, `simes/es/cache.py`, `simes/es/buffering.py`, `simes/arquivos/sistema.py`, `simes/arquivos/alocacao.py`, `simes/integracao.py`, `simes/disco.py`, `testes/test_extras.py`, `RELATORIO.md` (seções 0 a 7) |
