# Funcionalidades

O que o Arcade Cloud Score faz, organizado por área. Cada item indica onde está implementado.

## Quem usa o sistema

| Ator | Onde interage | O que pode fazer |
|---|---|---|
| **Jogador** | No fliperama e no site | Entrar, jogar com as partidas indo para o seu perfil, ver ranking, conquistas e saves |
| **Convidado** | No fliperama | Jogar sem conta. As partidas são registradas sem dono e o score aparece no ranking como `???` |
| **Visitante** | No site | Ver o ranking ao vivo, sem login |
| **Mantenedor** | No computador de desenvolvimento | Instalar o cliente no Pi, subir o back-end, mapear jogos novos |

## 1. Contas de jogador

| Funcionalidade | Como funciona | Implementação |
|---|---|---|
| Criar conta pelo site | Usuário, tag do placar e senha (com confirmação). Entra logado em seguida. | `static/login.html`, `auth.py` → `register` |
| Criar conta pelo fliperama | Mesmos campos, na tela "Login do Arcade". A conta vale também no site. | `jogadores.pygame` → `register`, `main.py` → `handle_login` |
| Entrar e sair pelo site | Sessão de 30 dias guardada em cookie. | `auth.py` → `login`, `logout` |
| Entrar pelo fliperama | Usuário e senha, por teclado ou controle. | `jogadores.pygame` → `login` |
| Jogar como convidado | Encerra a sessão de quem estava logado no aparelho. | `jogadores.pygame` → `guest` |

**Regras:**

- **Usuário:** 3 a 20 caracteres, só letras minúsculas sem acento, números ou `_`. Único.
- **Tag do placar:** 1 a 3 letras maiúsculas ou números, como nos fliperamas clássicos. Única.
- **Senha:** 8 a 128 caracteres.
- As mesmas regras são conferidas em três lugares: na tela (site ou fliperama), no back-end e no banco.
- Não há recuperação de senha: o cadastro não pede e-mail.

**Segurança das contas:**

- A senha é guardada como hash scrypt (N=2¹⁵, r=8, p=3); o texto da senha nunca é armazenado.
- O login responde no mesmo tempo para usuário inexistente e senha errada, e com a mesma mensagem, para não
  revelar quem tem conta.
- O cookie de sessão é `HttpOnly` e `SameSite=Lax`. No banco fica só o SHA-256 do token.
- No fliperama, a senha **nunca sai do Pi**: o login é por desafio-resposta (ver
  [arquitetura.md](arquitetura.md#login-no-fliperama)).

## 2. Tela "Login do Arcade"

Aparece no menu do Batocera, no sistema PYGAME. Implementada em `edge/batocera/jogadores.pygame`.

- Três caminhos: **ENTRAR**, **CRIAR CONTA** e **JOGAR COMO CONVIDADO**.
- **Teclado:** digitação direta; setas ou Tab trocam de campo; Enter confirma; Esc volta.
- **Controle:** direcional escolhe; A abre um teclado virtual na tela; B volta ou apaga; START envia.
- Aceita controles reconhecidos pelo SDL2 e joysticks genéricos.
- Mostra quem está jogando agora ("Jogando agora: THI" ou "convidado").
- Se o servidor não responder em 10 segundos, avisa e sugere jogar como convidado.
- Ao entrar, o menu do Batocera mostra um balão "Bem-vindo, &lt;TAG&gt;!".

## 3. Registro automático de partidas

Toda vez que um jogo abre e fecha no fliperama, a partida é registrada sem o jogador precisar fazer nada.
Implementado em `edge/batocera/hook.py` e `backend/main.py` → `handle_event`.

Para cada partida são guardados: jogo, sistema (console), quem jogou (ou convidado), aparelho, início, fim e,
quando disponível, score e fase.

- O nome do jogo vem do `gamelist.xml` do Batocera; sem ele, do nome do arquivo sem marcações como `(USA)`.
- A ROM é identificada por um hash MD5 no formato do RetroAchievements, que distingue versões do mesmo jogo.
- A própria tela de login não conta como partida.
- Cada partida tem um identificador único (`play_uuid`) gerado no Pi, então uma mensagem entregue duas vezes não
  vira duas partidas.
- O evento de fim repete todos os dados do início: se o de início se perder, a partida é registrada do mesmo jeito.

## 4. Score lido da memória do jogo

Os jogos antigos não informam o score para fora. O sistema lê os pontos direto da memória RAM do jogo em execução.
Implementado em `edge/batocera/arcaded.py` → `watch_scores` e configurado em `edge/batocera/memory_maps.json`.

- A cada segundo, durante a partida, o serviço pergunta ao RetroArch o conteúdo dos endereços de score e fase.
- Guarda o **melhor score da partida** (se o jogador usar "continue", vale o melhor) e a **fase mais avançada**.
- Só conta quando o jogo está de fato sendo jogado: os modos de demonstração pontuam sozinhos e são ignorados.
- Leituras absurdas (memória ainda não inicializada) são descartadas.
- Formatos suportados: inteiro big-endian, little-endian, BCD e o formato com bytes trocados do Mega Drive.

**Jogos com score verificado:** Sonic The Hedgehog, Sonic The Hedgehog 2 (Mega Drive) e Super Mario World (SNES).
Os demais jogos ficam com o histórico de partidas, sem score.

## 5. Ranking ao vivo

Página inicial do site (`backend/static/index.html`), aberta a qualquer visitante.

- Tabela **HIGH SCORES** com posição, tag, jogo, fase e score: os 10 melhores entre todos os jogos.
- Atualiza sozinha no instante em que um score novo é gravado, por WebSocket, sem recarregar a página.
- Indicador de estado: **AO VIVO**, **CONECTANDO** ou **RECONECTANDO** (tenta de novo a cada 2 segundos).
- Os três primeiros lugares têm cores de pódio.
- Em caso de empate, fica na frente quem fez o score primeiro.
- O jogador logado aparece com a tag atual do seu perfil.
- A API permite filtrar por jogo e pedir até 100 posições (`GET /api/leaderboard?game=...&limit=...`).

## 6. Perfil do jogador

Página `backend/static/perfil.html`, só para quem está logado. Dados de `backend/db.py` → `profile`.

| Seção | Conteúdo |
|---|---|
| Cabeçalho | Tag, nome de usuário e data de criação da conta |
| Estatísticas | Total de partidas, quantidade de jogos diferentes e tempo total jogado |
| Conquistas | Ver item 7 |
| Recordes | Por jogo: número de partidas, melhor score e posição no ranking daquele jogo |
| Últimas partidas | As 20 mais recentes, com data, duração e score |
| Saves na nuvem | Ver item 8 |

## 7. Conquistas (RetroAchievements)

Vitrine de conquistas no estilo Steam. Implementado em `edge/batocera/achievement.py`, `backend/ra.py` e
`backend/db.py` → `user_achievements`.

- Cada conquista desbloqueada durante o jogo vai para o perfil de quem está logado no fliperama.
- O perfil mostra, para cada jogo já jogado, **todas** as conquistas — desbloqueadas e bloqueadas —, com ícone,
  descrição e pontos.
- Por jogo: barra de progresso, contagem (ex.: 5/32) e pontos obtidos.
- Por conquista: data do desbloqueio e **raridade** (porcentagem dos jogadores do fliperama que a têm).
- Cada jogador recebe cada conquista uma vez só, mesmo que o jogo a dispare de novo em outra partida.
- Conquistas de convidados aparecem na tela do jogo, mas não vão para perfil nenhum.
- Na primeira partida de uma ROM, o back-end identifica o jogo no RetroAchievements e baixa a lista completa de
  conquistas.

O fliperama usa uma única conta do RetroAchievements em modo *encore*; quem separa as conquistas por pessoa é
este sistema. A chave da API web (`RA_API_KEY`) é opcional: sem ela, os desbloqueios ainda são gravados, mas sem
ícones e sem a lista completa de cada jogo.

## 8. Saves por jogador, com cópia na nuvem

O emulador guarda um único save por jogo, o que faria todos dividirem o mesmo progresso. O sistema dá a cada
jogador o seu. Implementado em `edge/batocera/saves.py`, `arcaded.py` e `backend/main.py` → `handle_save`.

- **Ao abrir o jogo:** os saves de quem jogou por último são guardados e os de quem vai jogar entram no lugar.
- **Ao fechar o jogo:** o que o emulador gravou vai para a cópia do jogador.
- **Na nuvem:** a cada 10 segundos o Pi envia as cópias que mudaram. No login, o servidor devolve o que estiver
  mais novo lá — é assim que o progresso aparece em outro fliperama.
- **No site:** o perfil lista os saves e permite baixá-los.
- Convidados têm saves próprios, separados, que ficam só no Pi.
- Nenhum arquivo é apagado sem existir uma cópia igual ou mais nova em outro lugar.
- Em caso de conflito, vale a versão gravada por último.
- A integridade de cada arquivo é conferida por hash SHA-256 nos dois sentidos.
- Arquivos acima de 3 MB ficam só no Pi.

## 9. Funcionamento sem internet e tolerância a falhas

| Situação | O que acontece |
|---|---|
| Wi-Fi do fliperama cai | As partidas e conquistas ficam numa fila em disco e são enviadas quando a rede voltar. Um item só sai da fila depois que o broker confirma o recebimento. |
| Back-end desligado | O broker guarda as mensagens e entrega quando ele voltar (sessão persistente, QoS 1). |
| Mensagem entregue duas vezes | O banco descarta a repetição pelas chaves únicas. |
| Mensagem inválida | É registrada no log como "Payload rejeitado" e descartada; o back-end continua funcionando. |
| Erro num gancho do Pi | É anotado em um arquivo de log e o jogo abre normalmente. |
| Queda de energia no meio da partida | O progresso salvo é guardado na próxima troca de saves. |
| Sem servidor na hora do login | Não dá para entrar (a senha é conferida no servidor), mas dá para jogar como convidado. |

## 10. Ferramentas de apoio

| Ferramenta | Para que serve | Implementação |
|---|---|---|
| Instalador do Pi | Um comando instala ou atualiza todo o cliente no fliperama, por SSH. | `edge/batocera/install.sh` |
| Publicador manual | Envia scores de teste pelo terminal, sem precisar do Pi. Tem modo interativo e por argumentos. | `edge/publisher.py` |
| Migrações automáticas | O esquema do banco é criado e atualizado sozinho quando o back-end sobe. | `backend/db.py` → `migrate` |
| Documentação da API | O FastAPI gera uma página interativa da API REST em `/docs`. | automático |

## Limitações

As limitações conhecidas (um processo por vez para o WebSocket, conta única do RetroAchievements, jogos sem
score, entre outras) estão listadas no [README](../README.md#limitações-conhecidas).
