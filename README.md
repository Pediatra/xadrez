# Treino de Xadrez Local

> **Stockfish não está instalado nesta máquina.** Ele não existe em
> `/usr/bin/stockfish` nem no `PATH`. Instale-o antes de usar a análise da
> partida seguindo uma das opções abaixo.

Aplicação web local de treino de xadrez: tabuleiro interativo, Stockfish para
análise e um treinador Gemini 4 Hydrogen via CLI `agy`. O backend é FastAPI puro;
o frontend é HTML, CSS e JavaScript vanilla carregado por CDN, sem build.

## 1. Instalar o Stockfish (Bazzite/Fedora Atomic)

O sistema atual é Bazzite/Fedora Atomic. Escolha **uma** das opções.

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
- Você pode perguntar qualquer coisa no chat a qualquer momento. O frontend
  anexa silenciosamente o FEN e o PGN atuais a cada mensagem.

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
- as avaliações antes, depois do lance do usuário e depois do bot;
- FENs, classificação, perda de centipawns e resultado da partida.

### `POST /chat` (SSE)

Recebe uma mensagem e o contexto opcional da partida e responde com
`text/event-stream`. O frontend usa `fetch` + `ReadableStream`, pois
`EventSource` não suporta POST.

Cada evento SSE é uma linha `data:` com JSON incremental:

```text
data: {"delta": "trecho da resposta"}

data: {"done": true, "status": "SUCCESS", "usage": {...}}
```

O backend executa o CLI local com `asyncio.create_subprocess_exec` (sem shell),
envia uma única linha NDJSON no `stdin` e converte o NDJSON do `stdout` em SSE.
O processo é encerrado no `finally` em caso de timeout ou desconexão do cliente.

## 7. Limitações

- **A análise depende do Stockfish instalado e funcional.** Sem o binário, ou se
  ele não responder a `uci`, o tabuleiro continua abrindo, mas
  `/move-and-analyze` retorna erro e a barra de avaliação não recebe análises.
- **O chat depende do `agy` autenticado na máquina.** Não há chave de API nem
  chamada HTTP a um provedor. Em outro host, sem o CLI e o login local, o
  `/chat` responde `agy não encontrado neste host` e o treinador não funciona.
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
├── requirements.txt
├── .env.example
├── README.md
└── static/
    ├── index.html     # layout semântico
    ├── style.css      # Pico CSS + ajustes
    └── app.js         # chess.js, Chessground, fetch/SSE
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

O `/health` continua mostrando `degraded` enquanto o Stockfish não estiver
instalado; o chat e o tabuleiro funcionam, mas a análise da partida não.
