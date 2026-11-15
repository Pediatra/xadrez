# Treino de Xadrez Local

> **Nesta máquina o Stockfish já está instalado** em
> `~/.local/bin/stockfish` (Stockfish 19, com UCI validado). O `.env` aponta
> para esse caminho e a análise do tabuleiro está funcionando. As instruções
> abaixo ficam como referência para reinstalação ou para outro host.

Aplicação web local de treino de xadrez: tabuleiro interativo, Stockfish para
análise e um treinador Gemini 4 Hydrogen via CLI `agy`. O backend é FastAPI puro;
o frontend é HTML, CSS e JavaScript vanilla carregado por CDN, sem build.

## 1. Stockfish (instalação/reinstalação)

O Stockfish já está instalado em `~/.local/bin/stockfish`. Se precisar
reinstalar ou instalar em outro host Bazzite/Fedora Atomic, escolha **uma** das
opções abaixo.

### Opção A — binário oficial em `~/.local/bin` (recomendada, sem reboot)

O asset oficial atual é `stockfish-linux-x86-64-universal.tar.gz` (release
`sf_19`). Os comandos abaixo baixam, extraem e instalam o binário:

```bash
mkdir -p ~/.local/bin
cd /tmp
curl -L --fail -o stockfish.tar.gz \
  https://github.com/official-stockfish/Stockfish/releases/latest/download/stockfish-linux-x86-64-universal.tar.gz
tar -xzf stockfish.tar.gz
install -m 755 stockfish/stockfish-linux-x86-64-universal ~/.local/bin/stockfish
chmod +x ~/.local/bin/stockfish
```

Confirme que o binário executa e responde ao protocolo UCI:

```bash
~/.local/bin/stockfish
```

No prompt do Stockfish, digite:

```text
uci
quit
```

A saída deve começar com uma linha parecida com:

```text
id name Stockfish 19
...
uciok
```

Para confirmar o comando pelo nome, verifique o `PATH`:

```bash
command -v stockfish
stockfish
```

Se `command -v stockfish` não imprimir nada, adicione `~/.local/bin` ao `PATH`
do seu shell (no Bash):

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

Depois, confirme novamente com `stockfish` e o comando `uci` acima.

### Opção B — pacote do sistema com `rpm-ostree` (exige reboot)

```bash
sudo rpm-ostree install stockfish
systemctl reboot
```

Depois do reboot, confirme:

```bash
stockfish
```

E, no prompt do Stockfish:

```text
uci
quit
```

A resposta deve conter `uciok` antes de você sair com `quit`.

> A aplicação também procura automaticamente por `/usr/bin/stockfish`,
> `~/.local/bin/stockfish` e `stockfish` no `PATH`. Você pode apontar outro
> caminho em `STOCKFISH_PATH` no `.env`.

## 2. Ambiente Python e dependências

Na pasta do projeto:

```bash
cd /var/home/gabriel/Projects/xadrez
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

As únicas dependências de backend são `fastapi`, `uvicorn`, `python-chess` e
`python-dotenv`.

## 3. Configurar o `.env`

```bash
cp .env.example .env
```

Edite `.env`. O mínimo para esta máquina é:

```dotenv
STOCKFISH_PATH=~/.local/bin/stockfish
AGY_BIN=~/.local/bin/agy
AGY_MODEL=gemini-4-hydrogen-high
AGY_EFFORT=high
AGY_TIMEOUT_SECONDS=120
AGY_MAX_CONCURRENCY=1
```

Variáveis disponíveis:

| Variável | Padrão | Função |
| --- | --- | --- |
| `STOCKFISH_PATH` | vazio → fallbacks | Caminho do executável do Stockfish |
| `AGY_BIN` | `~/.local/bin/agy` | Caminho do CLI local `agy` |
| `AGY_MODEL` | `gemini-4-hydrogen-high` | `gemini-4-hydrogen-low`, `-medium`, `-high` ou `-xhigh` |
| `AGY_EFFORT` | `high` | `low`, `medium`, `high`, `xhigh` ou `max` |
| `AGY_TIMEOUT_SECONDS` | `120` | Limite total de cada chamada ao treinador |
| `AGY_MAX_CONCURRENCY` | `1` | Chamadas simultâneas ao CLI; a fila curta responde 429 quando lotada |
| `AGY_SANDBOX` | `1` | Use `AGY_SANDBOX=0` se o ambiente recusar o sandbox |
| `DATABASE_PATH` | `./xadrez.db` | Arquivo SQLite do histórico e do revisor |

O `agy` usa o **login local da máquina**; não existe chave de API no projeto.
Confira que o CLI está presente e autenticado neste host:

```bash
ls -l ~/.local/bin/agy
~/.local/bin/agy models
```

O comando `models` deve listar a família `gemini-4-hydrogen-*`.

## 4. Subir a aplicação

```bash
source .venv/bin/activate
uvicorn main:app --reload
```

Abra no navegador:

<http://127.0.0.1:8000>

Para checar o diagnóstico sem abrir o navegador:

```bash
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

O `/health` mostra o caminho do Stockfish, se ele respondeu ao comando `uci`,
o caminho do `agy`, se ele existe e qual `AGY_MODEL` está em uso.

## 5. Como usar

- Você joga com as brancas contra o Stockfish. O seletor **Nível do Bot** vai
  de 1 (Iniciante) a 5 (Forte).
- **Novo Jogo**, **Inverter Tabuleiro** e **Desfazer Lance** controlam a partida.
- A barra vertical mostra a avaliação da posição; o painel esquerdo lista os
  lances jogados.
- Quando seu lance for classificado como **Imprecisão**, **Erro** ou **Gafe**,
  o tabuleiro desenha uma seta vermelha no lance jogado e uma verde no melhor
  lance do Stockfish. A explicação automática do treinador começa em seguida.
- Nesses lances, a **Lousa do treinador** abre à esquerda do tabuleiro principal,
  com o chat à direita. Ela tem um tabuleiro virtual e duas abas: **Sua jogada**
  mostra seu lance e a continuação que o adversário pode forçar; **Melhor linha**
  mostra a sequência que o Stockfish preferia. Os controles permitem avançar,
  voltar e reproduzir a sequência. Cada lance tem uma descrição em palavras,
  como “cavalo branco de f4 captura peão preto em d5”. Em telas estreitas, os
  painéis se empilham.
- O seletor no topo do chat define o nível da explicação: **Iniciante** (padrão),
  **Intermediário** ou **Avançado**. No modo iniciante, o treinador evita
  notação solta e explica a peça, as casas e a ideia por trás do lance.
- Você pode perguntar qualquer coisa no chat a qualquer momento. O frontend
  anexa silenciosamente o FEN e o PGN atuais a cada mensagem.
- Quando o treinador cita uma casa ou um lance, ela vira uma referência clicável
  no texto. Passar o mouse (ou focar pelo teclado) destaca a casa no tabuleiro;
  se for um lance, a interface desenha a seta de origem para destino. Clicar
  fixa o destaque e clicar de novo o remove. O treinador marca lances com
  `[[e2>e4]]`, casas com `[[c4]]` e roques com `[[O-O]]` ou `[[O-O-O]]`; a
  interface também reconhece coordenadas soltas, como `c4`, e expressões como
  `e2 para e4`. Se o roque não for legal na posição atual, a referência fica em
  vermelho e o treinador explica o bloqueio.
- Para fazer roque na interface, arraste o **rei duas casas** na direção da
  torre; a torre se move junto. Arrastar a torre não faz o roque.
- Ao selecionar o rei, se houver roque disponível, aparece um aviso logo abaixo
  do status, por exemplo `Roque disponível (O-O: clique em g1)`. Isso mostra
  qual lado está legal na posição atual.
- A partida atual sobrevive a um **F5**: posição, lista de lances, avaliação,
  orientação, nível do bot, nível da explicação, lousa aberta e conversa do chat
  são restaurados do `localStorage` do navegador. Se você atualizar no meio da
  análise de um lance, a página tenta recuperar o lance e a resposta do bot
  direto do SQLite antes de liberar o tabuleiro.
- A partida atual também fica registrada no SQLite. Se você abrir a aplicação em
  outro navegador ou limpar os dados do site, ela é retomada do servidor, com
  tabuleiro, lances, avaliação e nível do bot.
- **Desistir** encerra a partida com derrota (resultado `0-1` quando você joga de
  brancas), registra o evento `resign` no histórico e começa outra partida do
  zero. É diferente de **Novo Jogo**, que encerra a anterior sem resultado.
- A aba **Revisor** mostra o histórico salvo em SQLite: partidas, todos os
  lances com classificação e avaliação, lances desfeitos, estatísticas de
  precisão/perda média, fases e peças mais frágeis, aberturas repetidas e
  tendência entre partidas. Ela também gera feedback com o `agy` para o
  histórico geral ou para uma partida específica.

Classificação usada (perda em centipawns em relação ao melhor lance):

| Classificação | Critério |
| --- | --- |
| Excelente | melhor lance, xeque-mate ou perda ≤ 20 cp |
| Bom | perda ≤ 50 cp |
| Imprecisão | perda ≤ 100 cp |
| Erro | perda ≤ 200 cp |
| Gafe | perda > 200 cp |

A análise do lance do usuário roda na profundidade 12. O nível do bot controla
a profundidade e o `Skill Level` do Stockfish usados para escolher a resposta.

## 6. Endpoints

### `GET /health`

Diagnóstico do Stockfish e do `agy`.

### `POST /move-and-analyze`

Recebe o FEN anterior, o lance do usuário em UCI e o nível do bot:

```json
{
  "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "move": "e2e4",
  "level": 3
}
```

Valida o lance com `python-chess`, analisa a posição antes e depois, calcula a
perda de centipawns, classifica o lance e devolve, na mesma resposta:

- o lance do bot;
- o melhor lance que o usuário deveria ter jogado;
- a linha principal (PV), em UCI e SAN;
- a linha de refutação depois do lance do usuário, em UCI e SAN;
- as avaliações antes, depois do lance do usuário e depois do bot;
- FENs, classificação, perda de centipawns e resultado da partida.

### `POST /chat` (SSE)

Recebe uma mensagem e o contexto opcional da partida e responde com
`text/event-stream`. O frontend usa `fetch` + `ReadableStream`, pois
`EventSource` não suporta POST. O campo `student_level` aceita `beginner`
(padrão), `intermediate` ou `advanced`; o contexto `analysis` pode incluir a
`refutation_pv` para o treinador explicar a sequência da lousa.

Cada evento SSE é uma linha `data:` com JSON incremental:

```text
data: {"delta": "trecho da resposta"}

data: {"done": true, "status": "SUCCESS", "usage": {...}}
```

O backend executa o CLI local com `asyncio.create_subprocess_exec` (sem shell),
envia uma única linha NDJSON no `stdin` e converte o NDJSON do `stdout` em SSE.
O processo é encerrado no `finally` em caso de timeout ou desconexão do cliente.

### Histórico e revisor

- `POST /games` cria uma partida no SQLite e a marca como atual.
- `GET /session/current` devolve a partida atual com todos os lances.
- `POST /session/current` reivindica uma partida existente como atual.
- `POST /session/resign` encerra a partida atual com derrota e cria outra.
- `GET /games` lista as partidas com resumo de precisão e classificações.
- `GET /games/{id}` devolve todos os lances, lances desfeitos, eventos, PGN e
  estatísticas da partida.
- `POST /games/{id}/undo` registra o desfazer; os lances não são apagados, são
  marcados como desfeitos.
- `POST /games/{id}/end` encerra a partida quando você começa outra.
- `POST /games/{id}/event` registra ações como inverter tabuleiro e trocar nível.
- `GET /review/stats` agrega o histórico para o revisor.
- `POST /review/feedback` gera o parecer geral via SSE.
- `POST /review/game/{id}/feedback` gera a análise completa de uma partida via
  SSE, incluindo os lances desfeitos.

## 7. Limitações

- **A análise depende do Stockfish instalado e funcional.** Sem o binário, ou se
  ele não responder a `uci`, o tabuleiro continua abrindo, mas
  `/move-and-analyze` retorna erro e a barra de avaliação não recebe análises.
- **O chat e o revisor dependem do `agy` autenticado na máquina.** Não há chave
  de API nem chamada HTTP a um provedor. Em outro host, sem o CLI e o login
  local, o `/chat` e os endpoints `/review/*` respondem
  `agy não encontrado neste host` e não funcionam.
- **O histórico é local e não é sincronizado.** Fica no SQLite configurado em
  `DATABASE_PATH`; para levar o histórico a outra máquina, copie o arquivo do
  banco. As conversas do chat não entram no SQLite: a sessão atual e o texto do
  chat ficam no `localStorage` do navegador para sobreviver ao F5. Limpar os
  dados do site apaga essa cópia local e a retomada da partida.
- **A primeira resposta do treinador leva alguns segundos** porque o CLI inicia
  um processo por chamada. Não há daemon persistente; cada mensagem paga o custo
  de inicialização do `agy`. O limite padrão é de 120 segundos.
- O layout usa Pico CSS, Chessground e chess.js via CDN; o primeiro carregamento
  do frontend precisa de acesso à internet. Depois disso, a análise e o chat
  continuam locais.

## 8. Estrutura

```text
.
├── main.py            # FastAPI, Stockfish, SSE do agy, /health
├── storage.py         # SQLite: partidas, lances, eventos e revisor
├── requirements.txt
├── .env.example
├── README.md
└── static/
    ├── index.html     # layout semântico
    ├── style.css      # Pico CSS + ajustes
    └── app.js         # chess.js, Chessground, fetch/SSE

xadrez.db               # criado em runtime; fica fora do Git
```

Não há React, Node.js, jQuery, npm nem etapa de build. O servidor inteiro sobe
com um único comando depois do ambiente configurado:

```bash
uvicorn main:app
```

## 9. Serviço systemd e Tailscale (este host)

A aplicação está instalada como serviço **de usuário** do systemd, com `linger`
habilitado. Assim, ela inicia no boot mesmo antes de você abrir a sessão gráfica:

```text
~/.config/systemd/user/xadrez.service   # enabled
```

O `tailscaled` também já está habilitado no boot. A aplicação escuta somente em
`127.0.0.1:8000`; o Tailscale Serve publica essa porta, apenas dentro da tailnet,
em:

<https://bazzite.tailc0cd32.ts.net:8443/>

O `tailscale serve` existente na porta 443 (proxy para `127.0.0.1:3081`) foi
preservado. A configuração do Serve fica persistida pelo `tailscaled`, e o
serviço da aplicação também a reaplica no `ExecStartPost` a cada início.

Comandos úteis:

```bash
systemctl --user status xadrez.service
systemctl --user cat xadrez.service
systemctl --user restart xadrez.service
journalctl --user -u xadrez.service -f
tailscale serve status
```

Para recriar a exposição no Tailscale manualmente:

```bash
tailscale serve --bg --yes --https=8443 http://127.0.0.1:8000
```

A URL é privada da tailnet (não é Funnel e não fica pública na internet). Se o
nome da máquina no Tailscale mudar, gere a nova URL com `tailscale serve status`.

Depois de instalar o Stockfish ou alterar o `.env`, reinicie o serviço:

```bash
systemctl --user restart xadrez.service
```

O `/health` deve mostrar `status: "ok"` e o Stockfish 19 em
`stockfish.name`, com `uci: true`. Se aparecer `degraded`, confira o caminho em
`STOCKFISH_PATH`, a permissão de execução do binário e reinicie o serviço.

## 10. Histórico SQLite e Revisor

O arquivo padrão é `./xadrez.db` (configurável em `DATABASE_PATH`) e fica fora do
Git. Ele guarda:

- partidas: início, fim, nível do bot, FEN inicial/atual e resultado;
- lances do aluno e do Stockfish, com FEN antes/depois, classificação, perda de
  centipawns, avaliações, melhor lance, PV e linha de refutação;
- lances desfeitos, marcados com `undone = 1` em vez de apagados;
- eventos de jogo: início, fim, desistência, desfazer, inverter tabuleiro e
  troca de nível;
- em `app_state`, o ponteiro para a partida atual, usado para retomar em outro
  navegador ou depois de limpar o `localStorage`.

A aba **Revisor** lê esse banco e mostra:

- cartões de partidas, lances analisados, precisão estimada, perda média,
  erros/gafes e tendência recente;
- fase mais frágil, peça que mais erra e aberturas mais repetidas;
- a lista completa de lances da partida selecionada, com classificação,
  avaliação e melhor lance;
- os lances desfeitos da partida;
- **Feedback geral** (histórico agregado) e **Analisar esta partida** (todos os
  lances ativos + desfeitos), ambos com streaming do `agy`.

A **precisão estimada** é uma métrica interna, não a precisão oficial do
Lichess/Chess.com: Excelente = 1,0; Bom = 0,8; Imprecisão = 0,5; Erro = 0,2;
Gafe = 0,0, ponderada pela quantidade de lances analisados. A **perda média**
é a média simples dos centipawns perdidos por lance analisado.

Para fazer backup com o serviço parado:

```bash
systemctl --user stop xadrez.service
cp xadrez.db xadrez-backup.db
systemctl --user start xadrez.service
```

Para começar um histórico limpo, pare o serviço, mova `xadrez.db` e os arquivos
`xadrez.db-wal`/`xadrez.db-shm` para outro lugar e inicie de novo.

O conteúdo das conversas do treinador não entra no SQLite; ele fica apenas no
`localStorage` do navegador, junto com a sessão atual, para permitir a retomada
após F5. Ao pedir um parecer, o backend envia ao `agy` as estatísticas agregadas
e, no caso de uma partida, a lista de lances/classificações; o banco em si
permanece local.
