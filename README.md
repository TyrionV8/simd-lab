# SIMD Lab

Uma oficina visual para aprender como pesos int4 percorrem operações escalares e vetoriais até chegar a uma resposta. Conecte blocos, leia todos os dados, examine o C gerado e confira a execução no processador real.

![Circuito e inspeção dos dados](preview.jpg)

## Executar

Requisitos: **Linux x86-64, Python 3.12 ou posterior e GCC**. `objdump`, do pacote binutils, habilita a inspeção do assembly. O frontend não tem dependências externas nem etapa de construção.

```bash
# Entre no diretório em que o projeto foi clonado.
cd simd-lab

# Inicie o servidor, acessível somente neste computador.
./start.sh
```

Abra **http://127.0.0.1:8765/**. `Ctrl+C` encerra o servidor. Para outra porta, use `./start.sh --port 8766`.

## Explore a bancada

- **Roda do mouse:** zoom ancorado no cursor.
- **Arrastar o fundo**, botão do meio ou Espaço + arrastar: navegar.
- **Blocos:** arrastar da paleta ou clicar para adicionar.
- **Conexões:** arrastar da saída para a entrada, ou clicar nas duas portas. Enter/Espaço também acionam as portas.
- **Dados completos:** todos os elementos aparecem nos blocos e no inspetor. A opção “Dados nos blocos” permite alternar para uma visão estrutural.
- **Tipos:** veja a quantidade de números, bits por número, família numérica e capacidade dos grupos visuais de 128/256 bits.
- **Elemento:** clique em um valor para ler decimal, representação binária completa, bytes e posições lógicas. FP32 também tem decimal exato e decomposição em sinal, expoente e fração.
- **Operação e C:** leia o trecho gerado, as funções auxiliares e as instruções solicitadas. O assembly efetivo fica na área de execução real.
- **Desfazer/refazer:** Ctrl+Z e Ctrl+Shift+Z. Circuitos podem ser salvos como JSON e reabertos.

O projeto recupera o último circuito usando somente o armazenamento local do navegador. Não usa CDN, telemetria ou serviços externos. Os JSONs não são enviados a um serviço remoto.

## Meu circuito, gabarito e C executado

Estas três respostas ficam separadas:

1. **Meu circuito:** resultado das conexões feitas pelo usuário, com arredondamento explícito de FP32 e somas inteiras verificadas.
2. **Gabarito independente:** uma conta definida pelas entradas originais e pela regra escolhida, sem seguir as conexões do circuito.
3. **C no processador:** resultado e valores intermediários obtidos do executável gerado.

C e simulação concordarem comprova que o executável seguiu o circuito; isso não demonstra, por si só, que o circuito calcula a resposta pretendida.

Os gabaritos disponíveis são: soma matemática exata com uma única rodada para FP32, FP32 sequencial, oito vias do núcleo SSE histórico, FP32 com FMA da dev, int8 por linha da dev e referência A32. O gabarito matemático usa frações exatas e arredondamento direto para FP32 com empate para par.

Escolha entre **igualdade dos bits** e **tolerância numérica**. A interface mostra erro absoluto, erro relativo, distância entre FP32 adjacentes em ULP (*Unit in the Last Place*) e igualdade dos bits. As tolerâncias não são quantizadas para FP32.

O conjunto de validação cobre a entrada atual, zeros, extremos, sinais alternados, cancelamento, entrada unitária, frações, magnitudes diferentes e até 32 casos aleatórios adicionais, com semente reproduzível. Na execução nativa, o **mesmo binário recebe as diferentes entradas em tempo de execução** e confere todas as etapas de cada caso.

## Entradas

As grades mostram os 64 pesos e as 64 ativações, com erros por posição. É possível editar, colar listas/CSV/JSON ou gerar padrões e números aleatórios. Há seleção explícita para vírgula decimal. As edições ficam separadas das entradas aplicadas; uma medição não usa silenciosamente rascunhos.

Os pesos são inteiros de −8 a 7. As ativações e a escala dos pesos são convertidas para FP32. Os dados de entrada são limitados para manter os experimentos pequenos e verificáveis; transbordamento, valores não finitos, conexões incompatíveis e ciclos são reportados.

## Execução real

![Medições dentro do processo C](docs/execucao.jpg)

A execução é um trabalho assíncrono com progresso e cancelamento. O GCC compila apenas o programa gerado de operações cadastradas; o servidor não aceita C arbitrário. Há cache de compilação, aquecimento, repetições, várias amostras e escolha de afinidade entre as CPUs lógicas disponíveis.

Os tempos são medidos **dentro do processo C**. Não incluem compilar ou abrir o processo. O núcleo recebe entradas em tempo de execução e usa `noinline`/`noipa`, sem permitir ao compilador especializar todo o circuito para os números do exemplo.

Métricas:

- Mediana, mínimo, máximo e variação de nanossegundos por circuito.
- Tempo de CPU e amostras completas.
- Ticks do **TSC — Time Stamp Counter**, quando suportado.
- Ciclos de núcleo pela **PMU — Performance Monitoring Unit**, se o Linux permitir `perf_event_open`.
- Loop vazio medido separadamente, sem subtração automática.
- Identificação da CPU lógica e detecção de migração durante a amostra.
- Assembly do executável e histórico das medições da sessão.

**TSC não é o contador de ciclos efetivos do núcleo.** Se a PMU estiver bloqueada, o campo fica indisponível; o aplicativo não modifica as permissões do sistema. Os contadores de hardware são normalizados por tempo habilitado/ativo quando necessário.

Essas medições usam dados reutilizados e um circuito didático com arrays intermediários. Incluem chamadas, cópias e escrita da saída; não são latências isoladas nem previsão de tokens por segundo de um modelo. A afinidade não elimina interrupções do sistema. Compare as mesmas entradas e condições.

Referências: [Intel sobre o contador TSC](https://www.intel.com/content/www/us/en/docs/trace-analyzer-collector/user-guide-reference/2021-10/cpu-cycle-counter.html), [Linux perf_event_open](https://man7.org/linux/man-pages/man2/perf_event_open.2.html).

## Clock didático

O modelo lógico permite escolher clock em GHz (gigahertz) e custo em ciclos por lote de resultados. Os pais precisam terminar antes de um bloco iniciar; ramos independentes podem se sobrepor. São mostrados o caminho crítico, a soma serial e uma linha do tempo por bloco.

É um modelo **configurado**, separado das medições. Não simula portas de execução, cache, leituras, loops ou latências reais. Tempo em nanossegundos = ciclos do modelo ÷ clock configurado em GHz.

## Escopo dos exemplos

Há 35 blocos e 15 exemplos: expansão int4 para int8 e núcleo SSE histórico; referências da dev em FP32/int8; variantes AVX2; A32; int4+SSE; SSE4.1+AVX; SSSE3 com contrato i8 ou correção inteira; ajuste de sinal; produtos int16; construção F16C/FP32; tabelas com SSSE3.

Os exemplos estudam **um produto escalar e um grupo de 64 pesos**. São decomposições didáticas dos contratos numéricos, não cópias literais de todos os núcleos otimizados. A32 cobre uma linha; a organização R16 de 16 linhas, múltiplos grupos, a rede completa, GPU e paralelismo não são implementados. Os agrupamentos de registradores são visuais, não uma captura do estado físico da CPU.

Extensões indisponíveis bloqueiam a execução nativa, mas permitem simulação, geração de C e validação simulada. Os fluxos foram inspirados nas implementações de [Colibri](https://github.com/JustVugg/colibri); branches upstream podem mudar.

## Código C exportado

```bash
# -std=c11: padrão C11; -O2: otimizações comuns; -g: informação de depuração.
# -ffp-contract=off: evita fundir multiplicação/soma implicitamente.
# -fno-tree-vectorize: evita vetorização automática; intrínsecos continuam ativos.
# -lm: biblioteca matemática, incluindo fmaf.
gcc -std=c11 -O2 -g -ffp-contract=off -fno-tree-vectorize circuito.c -lm -o circuito

# Confira as extensões exigidas antes de executar em outro computador.
./circuito

# 10000 repetições, 7 amostras, CPU lógica 0 e 500 execuções de aquecimento.
# Substitua 0 por uma CPU disponível, ou use -1 para deixar o agendador escolher.
./circuito 10000 7 0 500
```

O programa imprime o rastro dos valores em JSON. Com argumentos de medição, imprime uma segunda linha JSON com as amostras. As funções `target(...)` são específicas das extensões indicadas. O código exportado é independente do aplicativo; o aplicativo verifica compatibilidade antes de executar, mas um arquivo exportado exige essa conferência pelo usuário.

## Testes

```bash
# Testes numéricos, gabaritos, geração de C, servidor e medições nativas.
# -B evita criar __pycache__.
python3 -B -m unittest -v test_engine.py test_validation.py test_http.py

# Opcional: testes JavaScript de zoom, tipos, vetores, CSV, bits e serialização.
node tests/model.test.js
```

Os testes incluem arredondamento exato/subnormais, sinal do zero, independência do gabarito, igualdade por contrato, origens dos resultados parciais, entradas inválidas, proteção por origem HTTP, trabalhos assíncronos, cancelamento, cache e execução nativa dos circuitos suportados. Caminhos indisponíveis são simulados sem executar instruções incompatíveis.

## Organização

| Arquivo | Responsabilidade |
| --- | --- |
| `engine.py`, `presets.py` | Grafo tipado, simulação, C e exemplos |
| `numerics.py`, `validation.py` | FP32/FMA, gabaritos independentes e casos |
| `inspection.py` | Funções C, tipos e escalonamento didático |
| `native.h`, `benchmark.h` | Operações e instrumentação C |
| `runner.py`, `server.py` | Compilação, fila, processos e API local |
| `web/canvas.js` | Navegação e editor de conexões |
| `web/model.js`, `web/app.js` | Representação dos dados e interface |

O servidor escuta apenas em `127.0.0.1`, limita o tamanho dos circuitos e rejeita POSTs de outra origem. Binários e medições usam diretórios temporários. Este é um laboratório individual; não exponha o servidor como serviço público.

## Licença

[Apache License 2.0](LICENSE). Veja também [NOTICE](NOTICE) para a atribuição ao projeto Colibri.
