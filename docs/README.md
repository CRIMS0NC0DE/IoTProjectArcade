# Documentação — Arcade Cloud Score

Índice da documentação técnica do projeto. O [README da raiz](../README.md) traz a visão geral, a instalação e
como rodar; os arquivos desta pasta detalham cada parte.

| Documento | O que responde |
|---|---|
| [estrutura-de-pastas.md](estrutura-de-pastas.md) | O que é cada pasta e cada arquivo do repositório, e em que linguagem está escrito |
| [tecnologias.md](tecnologias.md) | Linguagens, frameworks e protocolos de cada camada: front-end, back-end, banco de dados e Raspberry Pi |
| [funcionalidades.md](funcionalidades.md) | O que o sistema faz, por área, com as regras de negócio e onde cada coisa está implementada |
| [arquitetura.md](arquitetura.md) | Como as partes se conectam: componentes, fluxos passo a passo e modelo de concorrência |
| [contratos.md](contratos.md) | Referência das interfaces: API REST, WebSocket e tópicos/mensagens MQTT |
| [banco-de-dados.md](banco-de-dados.md) | Modelo de dados (diagrama ER), tabelas, restrições e migrações |
| [raspberry-pi.md](raspberry-pi.md) | O cliente que roda no Pi com Batocera: o que é instalado, onde fica e como diagnosticar |

## Por onde começar

- **Para escrever o relatório do projeto:** [tecnologias.md](tecnologias.md) (stack por camada),
  [estrutura-de-pastas.md](estrutura-de-pastas.md) (organização do código) e
  [funcionalidades.md](funcionalidades.md) (requisitos atendidos).
- **Para mexer no back-end ou no dashboard:** [arquitetura.md](arquitetura.md) e [contratos.md](contratos.md).
- **Para mexer no banco:** [banco-de-dados.md](banco-de-dados.md).
- **Para mexer no que roda no fliperama:** [raspberry-pi.md](raspberry-pi.md).

## Resumo em uma tabela

| Camada | Linguagem | Principais tecnologias | Pasta |
|---|---|---|---|
| Front-end (dashboard web) | HTML, JavaScript, CSS | Tailwind CSS (CDN), Fetch API, WebSocket | [`backend/static/`](../backend/static/) |
| Front-end (tela no fliperama) | Python | pygame | [`edge/batocera/jogadores.pygame`](../edge/batocera/jogadores.pygame) |
| Back-end | Python | FastAPI, Uvicorn, Pydantic, paho-mqtt | [`backend/`](../backend/) |
| Comunicação com o banco | Python + SQL | psycopg 3 (com pool), SQL puro, migrações `.sql` | [`backend/db.py`](../backend/db.py), [`backend/migrations/`](../backend/migrations/) |
| Banco de dados | SQL (PostgreSQL, PL/pgSQL) | PostgreSQL no Supabase | — (serviço em nuvem) |
| Raspberry Pi (borda/IoT) | Python, Shell, JSON | Batocera, RetroArch, paho-mqtt | [`edge/batocera/`](../edge/batocera/) |
| Mensageria | — | MQTT sobre TLS, QoS 1, broker HiveMQ Cloud | — (serviço em nuvem) |
