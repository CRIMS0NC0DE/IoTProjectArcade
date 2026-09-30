# Contratos: REST, WebSocket e MQTT

Referência das interfaces do sistema. Tudo trafega em JSON.

- [API REST](#api-rest) — navegador ↔ back-end
- [WebSocket](#websocket) — back-end → navegador
- [MQTT](#mqtt) — Raspberry Pi ↔ back-end

## API REST

Base: `http://localhost:8000` em desenvolvimento. O FastAPI publica uma versão interativa desta referência em
`/docs`.

| Método | Rota | Login | O que faz |
|---|---|---|---|
| `GET` | `/api/leaderboard` | Não | Ranking |
| `POST` | `/api/auth/register` | Não | Cria a conta e já entra |
| `POST` | `/api/auth/login` | Não | Entra |
| `POST` | `/api/auth/logout` | — | Sai |
| `GET` | `/api/me` | Sim | Usuário e tag de quem está logado |
| `GET` | `/api/me/profile` | Sim | Estatísticas, recordes e últimas partidas |
| `GET` | `/api/me/achievements` | Sim | Conquistas por jogo |
| `GET` | `/api/me/saves` | Sim | Lista dos saves na nuvem |
| `GET` | `/api/me/saves/{save_id}` | Sim | Baixa um save |

As páginas do dashboard são servidas na raiz: `/`, `/login.html` e `/perfil.html`.

### Autenticação

O login grava o cookie `arcade_session`, válido por 30 dias, com `HttpOnly` e `SameSite=Lax` (e `Secure` quando
o site está em HTTPS). As rotas marcadas com "Sim" respondem `401` sem um cookie válido.

### Erros

Os erros seguem o padrão do FastAPI, com a mensagem no campo `detail`:

| Código | Quando | Exemplo de `detail` |
|---|---|---|
| `401` | Sem login, ou usuário/senha errados | `"Usuário ou senha incorretos"` |
| `404` | Save inexistente ou de outro jogador | `"Save não encontrado"` |
| `409` | Usuário ou tag já em uso | `"Essa tag já pertence a outro jogador"` |
| `422` | Campo fora das regras | lista de erros de validação, um por campo |

### `GET /api/leaderboard`

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `game` | texto | — | Título exato do jogo. Sem ele, o ranking é de todos os jogos. |
| `limit` | inteiro, 1 a 100 | `10` | Quantidade de posições |

```json
[
  {
    "player_tag": "THI",
    "game": "Sonic The Hedgehog",
    "score": 142500,
    "stage_reached": 4,
    "timestamp": 1790697810
  }
]
```

Ordenado por score decrescente; em empate, o mais antigo primeiro. `timestamp` é o fim da partida, em segundos
(epoch). Scores de convidados têm `player_tag` igual a `"???"`.

### `POST /api/auth/register`

```json
{ "username": "thiago", "player_tag": "THI", "password": "minha-senha" }
```

| Campo | Regra |
|---|---|
| `username` | 3 a 20 caracteres: `a-z`, `0-9` ou `_` |
| `player_tag` | 1 a 3 caracteres: `A-Z` ou `0-9` |
| `password` | 8 a 128 caracteres |

Resposta `201`, com o cookie de sessão:

```json
{ "username": "thiago", "player_tag": "THI" }
```

### `POST /api/auth/login`

```json
{ "username": "thiago", "password": "minha-senha" }
```

Resposta `200`, igual à do cadastro. `401` se o usuário não existir ou a senha estiver errada (a mensagem é a
mesma nos dois casos).

### `POST /api/auth/logout`

Sem corpo. Resposta `204`. Apaga a sessão no banco e o cookie.

### `GET /api/me`

```json
{ "username": "thiago", "player_tag": "THI" }
```

### `GET /api/me/profile`

```json
{
  "username": "thiago",
  "player_tag": "THI",
  "created_at": 1790600000,
  "plays": 42,
  "games": 7,
  "seconds_played": 18340,
  "records": [
    { "game": "Sonic The Hedgehog", "plays": 12, "best": 142500, "position": 1 }
  ],
  "recent": [
    { "game": "Sonic The Hedgehog", "started_at": 1790697726, "seconds": 84, "score": 700 }
  ]
}
```

- `records`: um item por jogo já jogado, do mais recente para o mais antigo. `best` e `position` são `null` em
  jogos sem score. `position` é o lugar do melhor score do jogador no ranking daquele jogo.
- `recent`: as 20 últimas partidas. `score` é `null` quando o jogo não tem score mapeado.

### `GET /api/me/achievements`

Um item por jogo já jogado que tenha conquistas no RetroAchievements.

```json
[
  {
    "ra_game_id": 1,
    "game": "Sonic The Hedgehog",
    "icon": "/Images/085573.png",
    "total": 23,
    "unlocked": 5,
    "points_total": 235,
    "points_unlocked": 40,
    "achievements": [
      {
        "id": 1234,
        "title": "Título da conquista",
        "description": "Como desbloquear",
        "points": 5,
        "badge": "250336",
        "unlocked_at": 1790697800,
        "rarity": 50
      }
    ]
  }
]
```

- `unlocked_at` é `null` nas conquistas ainda bloqueadas.
- `rarity` é a porcentagem dos jogadores do fliperama que jogaram aquele jogo e têm a conquista.
- `icon` e `badge` são caminhos em `https://media.retroachievements.org`
  (o ícone da conquista fica em `/Badge/<badge>.png`).
- Conquistas que não puderam ser ligadas a um jogo vêm em um grupo chamado `"Outras conquistas"`, com
  `ra_game_id` igual a `null`.

### `GET /api/me/saves`

```json
[
  {
    "id": 17,
    "system": "megadrive",
    "filename": "Sonic The Hedgehog (USA, Europe).state1",
    "size": 48211,
    "modified_at": 1790697810
  }
]
```

### `GET /api/me/saves/{save_id}`

Devolve o arquivo (`application/octet-stream`) com o nome original. `404` se o save não existir ou for de outro
jogador.

## WebSocket

Endereço: `ws://<servidor>/ws` (ou `wss://` em HTTPS). Não exige login.

- Ao conectar, o servidor envia o ranking atual.
- Depois, envia o ranking inteiro de novo sempre que um score novo é gravado.
- O formato é o mesmo de `GET /api/leaderboard` sem parâmetros: os 10 melhores entre todos os jogos.
- O servidor não espera mensagens do navegador.

## MQTT

| Item | Valor |
|---|---|
| Broker | HiveMQ Cloud, porta 8883 (TLS) |
| Qualidade de serviço | QoS 1 em todos os tópicos (entrega "pelo menos uma vez") |
| Sessão | Persistente (`clean_session=False`) nos dois lados |
| `client_id` do back-end | `arcade-backend` |
| `client_id` do Pi | `<device_id>-daemon` |

### Tópicos

`<id>` é o identificador do aparelho, por exemplo `arcade-pi-01`.

| Tópico | Sentido | Conteúdo |
|---|---|---|
| `arcade/devices/<id>/login` | Pi → servidor | Pedidos de desafio, login, cadastro e logout |
| `arcade/devices/<id>/commands` | servidor → Pi | Respostas de login/cadastro e envio de saves |
| `arcade/devices/<id>/events` | Pi → servidor | Início e fim de partida |
| `arcade/devices/<id>/achievements` | Pi → servidor | Conquista desbloqueada |
| `arcade/devices/<id>/saves` | Pi → servidor | Envio de save ou lista de saves |
| `arcade/scores/submit` | qualquer → servidor | Score avulso (contrato original, usado pelo `publisher.py`) |

O back-end assina os quatro tópicos `arcade/devices/+/...` que o Pi publica, mais `arcade/scores/submit`. O Pi
assina só o seu `commands`.

### Validação

Cada mensagem é validada pelo back-end em **modo estrito**: um número enviado como texto (`"700"`) ou com casas
decimais (`700.0`) é recusado. Mensagens inválidas são registradas no log como `Payload rejeitado` e descartadas.

Tipos recorrentes:

| Nome | Regra |
|---|---|
| Epoch | Inteiro, em **segundos**, de 0 a 9 999 999 999 (um valor em milissegundos é recusado) |
| MD5 | 32 caracteres hexadecimais minúsculos |
| SHA-256 | 64 caracteres hexadecimais minúsculos |
| Usuário | `^[a-z0-9_]{3,20}$` |
| Tag | `^[A-Z0-9]{1,3}$` |

### `.../login` e as respostas em `.../commands`

Todo pedido tem `request_id` (texto de até 64 caracteres, criado no Pi) e `ts` (epoch). A resposta repete o
`request_id`.

**Pedir um desafio**

```json
{ "request_id": "9f2c...", "ts": 1790697700, "action": "challenge", "username": "thiago" }
```

```json
{ "request_id": "9f2c...", "type": "challenge", "salt": "a1b2...", "n": 32768, "r": 8, "p": 3, "nonce": "c3d4..." }
```

**Entrar** — `proof` é `HMAC-SHA256(scrypt(senha, salt), "<nonce>:<device_id>:<request_id>")`, em hexadecimal.

```json
{ "request_id": "7e1a...", "ts": 1790697702, "action": "login", "username": "thiago", "nonce": "c3d4...", "proof": "5f6e..." }
```

```json
{ "request_id": "7e1a...", "type": "login_ok", "username": "thiago", "player_tag": "THI" }
```

```json
{ "request_id": "7e1a...", "type": "login_error", "message": "Usuário ou senha incorretos" }
```

**Criar conta** — `password_hash` no formato `scrypt$32768$8$3$<salt em hex>$<hash em hex>`.

```json
{ "request_id": "4b8d...", "ts": 1790697700, "action": "register", "username": "thiago", "player_tag": "THI", "password_hash": "scrypt$32768$8$3$..." }
```

Resposta: `register_ok` (com `username` e `player_tag`) ou `register_error` (com `message`).

**Sair / modo convidado**

```json
{ "request_id": "2a6f...", "ts": 1790697900, "action": "logout" }
```

Resposta: `{ "request_id": "2a6f...", "type": "logout_ok" }`.

### `.../events` — partida

```json
{
  "play_uuid": "3f1c9a2e-6d4b-4c8a-9e21-7b5d0f8a1c33",
  "event": "stop",
  "system": "megadrive",
  "rom": "Sonic The Hedgehog (USA, Europe).zip",
  "title": "Sonic The Hedgehog",
  "rom_md5": "1bc674be034e43c96b86487ac69d9293",
  "username": "thiago",
  "started_at": 1790697726,
  "ended_at": 1790697810,
  "score": 700,
  "stage_reached": 1
}
```

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `play_uuid` | UUID | Sim | Identificador da partida, criado no Pi. Chave de idempotência. |
| `event` | `"start"` ou `"stop"` | Sim | — |
| `system` | texto, até 64 | Sim | Pasta do sistema no Batocera (`megadrive`, `snes`...) |
| `rom` | texto, até 255 | Sim | Nome do arquivo da ROM |
| `title` | texto, até 255 | Sim | Nome do jogo para exibição |
| `rom_md5` | MD5 | Não | Hash da ROM no formato do RetroAchievements |
| `username` | texto | Não | Quem estava logado. `null` = convidado |
| `started_at` | Epoch | Sim | — |
| `ended_at` | Epoch | No `stop` | Deve ser maior ou igual a `started_at` |
| `score` | inteiro ≥ 0 | Não | Só no `stop` de jogos com mapa de memória |
| `stage_reached` | inteiro ≥ 0 | Não | Fase mais avançada |

### `.../achievements` — conquista

```json
{
  "achievement_id": 1234,
  "title": "Título da conquista",
  "description": "Como desbloquear",
  "play_uuid": "3f1c9a2e-6d4b-4c8a-9e21-7b5d0f8a1c33",
  "rom_md5": "1bc674be034e43c96b86487ac69d9293",
  "username": "thiago",
  "unlocked_at": 1790697800
}
```

`achievement_id` é o ID da conquista no RetroAchievements. Com `username` igual a `null` (convidado), a
conquista não é gravada em perfil nenhum.

### `.../saves` — saves

**Enviar um arquivo** (Pi → servidor):

```json
{
  "type": "upload",
  "username": "thiago",
  "system": "megadrive",
  "filename": "Sonic The Hedgehog (USA, Europe).srm",
  "sha256": "e3b0...",
  "modified_at": 1790697810,
  "data": "<conteúdo em base64>"
}
```

- `system`: `^[a-z0-9_]{1,32}$`.
- `filename`: só o nome, sem barras e sem começar com ponto.
- Tamanho máximo do arquivo: 3 MB.
- O servidor recalcula o SHA-256 e recusa o arquivo se não conferir.

**Enviar a lista do que o Pi tem** (Pi → servidor, no login):

```json
{
  "type": "manifest",
  "username": "thiago",
  "files": {
    "megadrive/Sonic The Hedgehog (USA, Europe).srm": { "sha256": "e3b0...", "modified_at": 1790697810 }
  }
}
```

Para cada save da nuvem que o Pi não tem, ou que está mais novo na nuvem, o servidor publica em `.../commands`:

```json
{
  "type": "save_file",
  "username": "thiago",
  "system": "megadrive",
  "filename": "Sonic The Hedgehog (USA, Europe).srm",
  "sha256": "e3b0...",
  "modified_at": 1790697810,
  "data": "<conteúdo em base64>"
}
```

### `arcade/scores/submit` — score avulso

Contrato original do projeto, mantido para testes com o `edge/publisher.py`.

```json
{
  "device_id": "arcade-pi-01",
  "player_tag": "THI",
  "game": "Street Fighter II",
  "score": 142500,
  "stage_reached": 8,
  "timestamp": 1790697810
}
```

| Campo | Regra |
|---|---|
| `device_id` | Texto não vazio |
| `player_tag` | Tag (1 a 3 letras maiúsculas ou números) |
| `game` | Texto não vazio |
| `score` | Inteiro ≥ 0 |
| `stage_reached` | Inteiro ≥ 0 |
| `timestamp` | Epoch |

A combinação `device_id` + `player_tag` + `timestamp` identifica a partida: uma mensagem repetida é ignorada. Se
a tag pertencer a uma conta, a partida é associada a ela.
