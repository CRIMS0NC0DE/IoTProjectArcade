# Estrutura de pastas

O repositório é dividido em duas metades que rodam em máquinas diferentes:

- **`backend/`** roda em um computador ou servidor: recebe os dados, grava no banco e serve o dashboard web.
- **`edge/`** é a "borda" (IoT): o código que roda no Raspberry Pi do fliperama.

As duas metades não se importam uma à outra. Elas só conversam por mensagens MQTT, descritas em
[contratos.md](contratos.md).

## Visão geral

```
.
├── README.md                  Visão geral, instalação e como rodar
├── requirements.txt           Dependências Python do back-end e do publisher
├── .env.example               Modelo de configuração (o .env real não é versionado)
├── .gitignore                 Deixa fora do git: .venv/, __pycache__/, .env, *.db
├── .gitattributes             Diz ao GitHub que os arquivos .pygame são Python
│
├── docs/                      Documentação técnica (esta pasta)
│
├── backend/                   SERVIDOR — Python (FastAPI)
│   ├── main.py                Ponto de entrada: MQTT, REST, WebSocket
│   ├── auth.py                Contas, sessões e login do fliperama
│   ├── db.py                  Comunicação com o PostgreSQL
│   ├── ra.py                  Cliente da API do RetroAchievements
│   ├── migrations/            Esquema do banco — SQL
│   │   ├── 001_initial.sql
│   │   ├── 002_auth_tokens.sql
│   │   ├── 003_device_sessions.sql
│   │   ├── 004_achievements.sql
│   │   └── 005_saves.sql
│   └── static/                FRONT-END — HTML, JavaScript, CSS
│       ├── index.html         Ranking ao vivo
│       ├── login.html         Entrar / criar conta
│       ├── perfil.html        Perfil do jogador
│       ├── arcade.js          Funções compartilhadas pelas páginas
│       └── arcade.css         Efeitos visuais de fliperama
│
└── edge/                      BORDA (IoT) — Python e Shell
    ├── publisher.py           Publicador manual, para testar sem o Pi
    └── batocera/              Cliente instalado no Raspberry Pi
        ├── install.sh         Instalador via SSH
        ├── arcaded.py         Serviço: MQTT, fila, leitura de score, saves
        ├── hook.py            Gancho de início/fim de jogo
        ├── achievement.py     Gancho de conquista
        ├── saves.py           Troca de saves por jogador
        ├── common.py          Caminhos e utilidades compartilhados
        ├── jogadores.pygame   Tela "Login do Arcade"
        ├── memory_maps.json   Endereços de memória do score de cada jogo
        ├── arcade_score       Script do serviço (start/stop/status)
        ├── arcade-score.sh    Atalho que o Batocera chama → hook.py
        └── arcade-achievement.sh   Atalho que o Batocera chama → achievement.py
```

## Raiz

| Arquivo | Tipo | Para que serve |
|---|---|---|
| `README.md` | Markdown | Porta de entrada: o que é o projeto, pré-requisitos, instalação e execução. |
| `requirements.txt` | Texto (pip) | Bibliotecas Python do back-end e do `edge/publisher.py`: `fastapi`, `uvicorn[standard]`, `paho-mqtt`, `python-dotenv`, `psycopg[binary,pool]`. |
| `.env.example` | Texto (dotenv) | Modelo das variáveis de ambiente. Copie para `.env` e preencha. |
| `.gitignore` | Texto | Impede que segredos (`.env`) e arquivos gerados (`.venv/`, `__pycache__/`) entrem no git. |
| `.gitattributes` | Texto | Classifica `*.pygame` como Python na barra de linguagens do GitHub. |

## `backend/` — servidor

Tudo em **Python**. É um único processo que faz três papéis: assinante MQTT, API web e servidor de arquivos estáticos.

| Arquivo | Linguagem | Responsabilidade |
|---|---|---|
| `main.py` | Python (FastAPI, Pydantic, paho-mqtt) | Ponto de entrada. Carrega o `.env`, abre o banco e aplica as migrações na subida, conecta ao broker MQTT e se inscreve nos tópicos. Define os modelos que validam cada mensagem MQTT e um tratador por tópico (score manual, login, partida, conquista, save). Expõe `GET /api/leaderboard` e o WebSocket `/ws`, e serve a pasta `static/`. |
| `auth.py` | Python (FastAPI, `hashlib`, `hmac`) | Contas de jogador. Rotas `/api/auth/*` (cadastro, login, logout) e `/api/me/*` (perfil, conquistas, saves). Hash de senha com scrypt, sessão por cookie, e a lógica de desafio-resposta usada no login pelo fliperama. |
| `db.py` | Python + SQL (psycopg 3) | **Única camada que fala com o banco.** Pool de conexões, aplicação das migrações e todas as consultas SQL do projeto (ranking, perfil, partidas, conquistas, saves). Nenhum outro arquivo escreve SQL. |
| `ra.py` | Python (`urllib`) | Cliente da API web do RetroAchievements: descobre qual jogo corresponde a uma ROM e baixa a lista de conquistas, com cache no banco. Opcional — só age se `RA_API_KEY` estiver definida. |

### `backend/migrations/` — esquema do banco

Arquivos **SQL** (PostgreSQL). São aplicados em ordem alfabética quando o back-end sobe, cada um uma única vez.
Detalhes em [banco-de-dados.md](banco-de-dados.md).

| Arquivo | O que cria |
|---|---|
| `001_initial.sql` | Função e gatilhos de `updated_at`; tabelas `users`, `devices`, `games`, `plays`, `scores`. |
| `002_auth_tokens.sql` | Tabela `auth_tokens` (sessões do dashboard). |
| `003_device_sessions.sql` | Tabela `device_sessions` (quem está logado em cada fliperama). |
| `004_achievements.sql` | Colunas do RetroAchievements em `games`; tabelas `ra_hashes`, `achievements`, `user_achievements`. |
| `005_saves.sql` | Tabela `saves` (saves dos jogadores na nuvem). |

### `backend/static/` — front-end

**HTML, JavaScript e CSS** sem etapa de build: os arquivos são servidos como estão pelo FastAPI. O estilo usa
Tailwind CSS carregado por CDN.

| Arquivo | Linguagem | Responsabilidade |
|---|---|---|
| `index.html` | HTML + JavaScript | Página inicial. Tabela **HIGH SCORES** atualizada ao vivo por WebSocket, com indicador de conexão e reconexão automática. |
| `login.html` | HTML + JavaScript | Abas **ENTRAR** e **CRIAR CONTA**. Valida os campos no navegador com as mesmas regras do servidor. |
| `perfil.html` | HTML + JavaScript | Perfil do jogador logado: estatísticas, conquistas por jogo, recordes, últimas partidas e saves na nuvem com download. |
| `arcade.js` | JavaScript | Funções usadas pelas três páginas: escape de HTML (`esc`), formatação de score, data e duração, `api()` (fetch com tratamento de erro) e `renderNav()` (canto do cabeçalho). |
| `arcade.css` | CSS | Dois efeitos: linhas de varredura de monitor CRT no fundo e o brilho dos títulos. |

## `edge/` — borda (IoT)

| Arquivo | Linguagem | Responsabilidade |
|---|---|---|
| `publisher.py` | Python (paho-mqtt) | Ferramenta de linha de comando que publica um score no tópico `arcade/scores/submit`. Serve para testar o back-end e o dashboard sem ter o Raspberry Pi. Roda em qualquer computador com o `.env`. |

### `edge/batocera/` — cliente do Raspberry Pi

Código que o `install.sh` copia para o Pi. **Python** para a lógica, **Shell** para a integração com o Batocera
e **JSON** para dados. Onde cada arquivo vai parar no Pi está em [raspberry-pi.md](raspberry-pi.md).

| Arquivo | Linguagem | Responsabilidade |
|---|---|---|
| `install.sh` | Bash | Roda no computador de desenvolvimento. Copia tudo para o Pi por SSH, gera a configuração a partir do `.env`, registra os ganchos e a entrada "Login do Arcade", ajusta o RetroArch e ativa o serviço. |
| `arcaded.py` | Python (paho-mqtt) | O serviço que fica sempre rodando. Mantém a conexão MQTT, envia a fila local, recebe as respostas do servidor, lê o score da memória do jogo e sincroniza os saves com a nuvem. **É o único arquivo do Pi que usa a rede externa.** |
| `hook.py` | Python | Chamado pelo Batocera quando um jogo abre e quando fecha. Monta os dados da partida (jogo, hash da ROM, jogador) e grava na fila local. |
| `achievement.py` | Python | Chamado pelo Batocera a cada conquista desbloqueada no RetroAchievements. Grava o desbloqueio na fila local. |
| `saves.py` | Python | Troca os arquivos de save do jogo conforme quem está jogando (usado pelo `hook.py`). |
| `common.py` | Python | Caminhos das pastas e arquivos de estado no Pi, leitura/escrita segura de JSON e a função `enqueue()` que põe mensagens na fila. |
| `jogadores.pygame` | Python (pygame) | A tela **"Login do Arcade"**: entrar, criar conta ou jogar como convidado, por teclado ou controle (com teclado virtual). A extensão `.pygame` é exigida pelo Batocera; o conteúdo é Python. |
| `memory_maps.json` | JSON | Para cada jogo (identificado pelo hash da ROM), os endereços de memória onde estão o score e a fase, e como decodificá-los. |
| `arcade_score` | Bash | Script de serviço do Batocera (`start`, `stop`, `status`) que mantém o `arcaded.py` rodando desde o boot. |
| `arcade-score.sh` | Shell (`sh`) | Gancho de início/fim de jogo: apenas repassa os argumentos para o `hook.py`. |
| `arcade-achievement.sh` | Shell (`sh`) | Gancho de conquista: apenas repassa os argumentos para o `achievement.py`. |

## O que não está no repositório

Estes itens existem só na máquina de cada um e são ignorados pelo git:

| Item | O que é |
|---|---|
| `.env` | Configuração real, com senhas do MQTT, do banco e a chave do RetroAchievements. **Nunca deve ser enviado ao GitHub.** |
| `.venv/` | Ambiente virtual Python com as bibliotecas instaladas. Recriado com `pip install -r requirements.txt`. |
| `__pycache__/` | Cache do Python, gerado automaticamente. |
