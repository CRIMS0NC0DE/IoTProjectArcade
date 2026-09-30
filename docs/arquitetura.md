# Arquitetura

Como as partes do sistema se conectam e o que acontece, passo a passo, em cada fluxo principal.

## Componentes

```mermaid
flowchart TB
  subgraph Pi["Raspberry Pi 4 · Batocera"]
    direction TB
    Tela["Tela 'Login do Arcade'<br/>jogadores.pygame"]
    Hook["Ganchos de jogo e conquista<br/>hook.py · achievement.py"]
    Fila[("Fila em disco<br/>spool/")]
    Estado[("Estado<br/>state/")]
    Daemon["Serviço arcaded.py"]
    RA["Batocera · RetroArch"]
    Tela --> Fila
    Hook --> Fila
    Hook <--> Estado
    Tela <--> Estado
    Fila --> Daemon
    Daemon <--> Estado
    Daemon -- "UDP :55355<br/>READ_CORE_RAM" --> RA
    RA -- "chama ao abrir, fechar<br/>e desbloquear" --> Hook
  end

  Broker[("HiveMQ Cloud<br/>broker MQTT")]

  subgraph Servidor["Back-end · um processo Python"]
    direction TB
    MQTT["Assinante MQTT<br/>main.py"]
    API["API REST + WebSocket<br/>main.py · auth.py"]
    DBL["Camada de dados<br/>db.py"]
    RAC["Cliente RetroAchievements<br/>ra.py"]
    MQTT --> DBL
    API --> DBL
    MQTT --> RAC
    RAC --> DBL
  end

  PG[("PostgreSQL<br/>Supabase")]
  RAAPI["retroachievements.org"]
  Web["Navegador<br/>index · login · perfil"]

  Daemon <-- "MQTT/TLS :8883" --> Broker
  Broker <-- "MQTT/TLS :8883" --> MQTT
  DBL -- "SQL/TLS" --> PG
  RAC -- "HTTPS" --> RAAPI
  Web -- "HTTP + WebSocket" --> API
```

Três ideias sustentam o desenho:

1. **O Pi e o servidor nunca se conectam um ao outro.** Os dois fazem conexões de saída para o broker MQTT. O
   fliperama pode ficar atrás de qualquer roteador doméstico, sem IP público e sem abrir portas.
2. **No Pi, só o serviço `arcaded.py` usa a rede.** Os ganchos e a tela de login apenas gravam arquivos numa fila
   em disco. O Batocera espera o gancho terminar antes de abrir o jogo; se o gancho dependesse da rede, um Wi-Fi
   ruim atrasaria o jogo.
3. **No servidor, só o `db.py` fala com o banco.** Os demais módulos chamam funções dele.

## Fluxo de uma partida com score

```mermaid
sequenceDiagram
  autonumber
  participant B as Batocera / RetroArch
  participant H as hook.py
  participant D as arcaded.py
  participant M as Broker MQTT
  participant S as Back-end
  participant P as PostgreSQL
  participant N as Navegador

  B->>H: gameStart (sistema, ROM)
  H->>H: gera play_uuid, calcula hash da ROM,<br/>lê quem está logado
  H-->>D: grava evento "start" na fila
  H->>H: troca os saves para os do jogador
  D->>M: publica em .../events (QoS 1)
  M->>S: entrega
  S->>P: grava a partida
  loop a cada 1 s, durante o jogo
    D->>B: READ_CORE_RAM (score, fase)
    B-->>D: bytes da memória
    D->>D: guarda o melhor score em live_score.json
  end
  B->>H: gameStop
  H->>H: junta o melhor score lido pelo daemon
  H-->>D: grava evento "stop" na fila
  D->>M: publica em .../events (QoS 1)
  M->>S: entrega
  S->>P: fecha a partida e grava o score
  S->>N: envia o ranking novo por WebSocket
```

Pontos importantes:

- O `play_uuid` criado no passo 2 acompanha a partida até o banco. Como o QoS 1 pode entregar a mesma mensagem
  mais de uma vez, é ele (com uma restrição `UNIQUE`) que impede duplicatas.
- O evento `stop` repete todos os dados do `start`. Se o `start` se perder, o `stop` sozinho cria a partida.
- O daemon só remove um arquivo da fila depois de o broker confirmar o recebimento (PUBACK).
- O navegador só é avisado quando um **score novo** é gravado, não a cada partida.

## Login no fliperama

A senha nunca é enviada pela rede. O broker MQTT usado não restringe tópicos por credencial, então qualquer
cliente autenticado poderia ler as mensagens; por isso só trafegam dados que não servem para entrar de novo.

```mermaid
sequenceDiagram
  autonumber
  participant T as Tela de login (Pi)
  participant D as arcaded.py
  participant S as Back-end
  participant P as PostgreSQL

  T-->>D: pedido "challenge" (usuário) na fila
  D->>S: .../login
  S->>P: busca o hash da conta
  S->>D: .../commands — salt, N, r, p e nonce de uso único
  D-->>T: grava a resposta em auth_result.json
  T->>T: chave = scrypt(senha, salt)<br/>prova = HMAC(chave, nonce:aparelho:pedido)
  T-->>D: pedido "login" (nonce, prova) na fila
  D->>S: .../login
  S->>S: refaz o HMAC com o hash guardado<br/>e compara em tempo constante
  S->>P: abre a sessão do jogador no aparelho
  S->>D: .../commands — login_ok (usuário, tag)
  D-->>T: grava a resposta em auth_result.json
  D->>D: grava session.json
  D->>S: .../saves — lista de saves do jogador
```

- O desafio vale 60 segundos e uma única tentativa, certa ou errada.
- A prova é amarrada ao aparelho e ao pedido: capturá-la não permite entrar de outro lugar nem repetir o login.
- Para um usuário que não existe, o servidor devolve um salt falso, mas sempre o mesmo para aquele nome. A
  resposta fica igual à de uma conta real.
- A tela recusa parâmetros de scrypt exagerados, para que um servidor falso não consiga travar o Pi.
- Só depois do `login_ok` o Pi considera alguém logado (arquivo `session.json`). Uma resposta atrasada de uma
  tentativa antiga é ignorada.

No **cadastro** pelo fliperama, o Pi gera o salt e o hash scrypt e envia o hash. O servidor só aceita hashes com
os mesmos parâmetros que ele próprio usa.

No **site**, o login é o tradicional: a senha vai por HTTP(S) para o back-end, que confere com o hash.

## Sincronização de saves

```mermaid
sequenceDiagram
  autonumber
  participant D as arcaded.py
  participant S as Back-end
  participant P as PostgreSQL

  Note over D,S: No login (e a cada reconexão com alguém logado)
  D->>S: manifest — lista de arquivos com hash e data
  S->>P: consulta os saves do jogador
  loop para cada save mais novo na nuvem, ou que falta no Pi
    S->>D: save_file (arquivo em base64)
    D->>D: confere o hash e grava na cópia do jogador
  end

  Note over D,S: A cada 10 s
  D->>D: procura cópias de jogadores que mudaram
  D->>S: upload (arquivo em base64, hash, data)
  S->>S: confere o hash
  S->>P: grava se for mais novo que o da nuvem
```

O arquivo recebido da nuvem entra na cópia do jogador no Pi; é o gancho de início de jogo que o põe no lugar
onde o emulador lê, na partida seguinte.

## Modelo de concorrência

### No back-end

O processo tem três frentes de execução, e o código é escrito para que uma não trave a outra:

| Frente | O que roda nela |
|---|---|
| Laço de eventos `asyncio` | WebSocket e o envio do ranking para os navegadores |
| Thread do paho-mqtt | Os tratadores de mensagens MQTT, incluindo as gravações no banco |
| Fila do RetroAchievements (1 thread) | Chamadas à API do RA, que podem levar segundos |

As rotas REST são funções comuns (`def`), que o FastAPI executa em um pool de threads à parte.

- Quando a thread do MQTT grava um score novo, ela **agenda** o envio do ranking no laço de eventos com
  `asyncio.run_coroutine_threadsafe`.
- O envio do ranking consulta o banco fora do laço de eventos, com `asyncio.to_thread`.
- Cada tratador MQTT é protegido por `try/except`: uma exceção que escapasse derrubaria o laço do paho.
- As conexões com o banco vêm de um pool (1 a 5), compartilhado com segurança entre as threads.

Os navegadores conectados ao WebSocket ficam numa lista **em memória**. Por isso o back-end deve rodar com um
único processo do Uvicorn.

### No Raspberry Pi

| Processo | Duração | O que faz |
|---|---|---|
| `arcaded.py` | Permanente, desde o boot | Três frentes: o laço principal (fila a cada 1 s, saves a cada 10 s), a thread do paho-mqtt (recebe comandos) e uma thread que lê o score a cada 1 s |
| `hook.py` | Frações de segundo | Executa uma vez por abertura e por fechamento de jogo |
| `achievement.py` | Frações de segundo | Executa uma vez por conquista |
| `jogadores.pygame` | Enquanto a tela está aberta | Interface de login |

Esses processos trocam informação por **arquivos JSON**. Toda escrita é feita em um arquivo temporário e depois
renomeada, de modo que quem lê nunca encontra um arquivo pela metade.

## Decisões e limitações

As justificativas de cada escolha técnica e as limitações conhecidas estão no README:
[Decisões técnicas](../README.md#decisões-técnicas) e [Limitações conhecidas](../README.md#limitações-conhecidas).
