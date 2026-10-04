# Raspberry Pi (cliente do fliperama)

O que roda no Raspberry Pi 4 com Batocera, onde cada arquivo fica e como diagnosticar problemas. O código-fonte
está em [`edge/batocera/`](../edge/batocera/).

## O que o Batocera já fornece

O cliente foi escrito para não instalar nada além do próprio código:

| Recurso | Uso |
|---|---|
| Python 3 | Executa todos os scripts |
| pygame | Tela de login |
| RetroArch | Emulação, conquistas e leitura de memória |
| EmulationStation | Menu; API local na porta 1234 |
| `batocera-settings-set`, `batocera-services` | Configuração e serviço de boot |
| Servidor SSH | Instalação remota |

A única biblioteca externa é a **paho-mqtt**. Como o Batocera não tem `pip`, o instalador copia a biblioteca (que
é Python puro) do ambiente virtual do computador para o Pi.

## Instalação

A partir do computador de desenvolvimento, na raiz do projeto:

```bash
edge/batocera/install.sh <ip-do-pi> <device_id>
```

Por exemplo: `edge/batocera/install.sh 192.168.1.94 arcade-pi-01`. O mesmo comando serve para atualizar depois
de qualquer mudança em `edge/batocera/`.

**O que precisa existir antes:**

| Requisito | Por quê |
|---|---|
| `.env` na raiz do projeto | O instalador tira dele o endereço e a credencial MQTT do Pi |
| `.venv/` na raiz, com `pip install -r requirements.txt` | O instalador copia a paho-mqtt de lá e usa o python-dotenv |
| Chave SSH em `~/.ssh/arcade_pi`, autorizada no Pi | Acesso sem senha. Outro caminho pode ser passado na variável `PI_KEY` |

O passo a passo para criar a chave está no [README](../README.md#como-rodar).

**O que o instalador faz, em ordem:**

1. Cria as pastas no Pi.
2. Copia o código Python, o `memory_maps.json` e a biblioteca paho-mqtt.
3. Instala os ganchos, o script de serviço e a tela de login, e os marca como executáveis.
4. Gera o `arcade.conf` no Pi só com `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASS` e `DEVICE_ID`. As demais
   variáveis do `.env` (banco, credencial do back-end, chave do RetroAchievements) **não vão para o Pi**.
5. Cria a entrada **"Login do Arcade"** no sistema PYGAME do menu.
6. Liga os comandos de rede do RetroArch (porta 55355) e o modo *encore* do RetroAchievements.
7. Ativa e reinicia o serviço `arcade_score`, e mostra o estado e as últimas linhas do log.

## Onde cada arquivo fica no Pi

| Arquivo no repositório | Destino no Pi |
|---|---|
| `common.py`, `hook.py`, `saves.py`, `achievement.py`, `arcaded.py`, `memory_maps.json` | `/userdata/system/arcade/` |
| paho-mqtt (do `.venv`) | `/userdata/system/arcade/lib/paho/` |
| `arcade-score.sh` | `/userdata/system/scripts/arcade-score.sh` |
| `arcade-achievement.sh` | `/userdata/system/configs/emulationstation/scripts/achievements/arcade-score.sh` |
| `arcade_score` | `/userdata/system/services/arcade_score` |
| `jogadores.pygame` | `/userdata/roms/pygame/jogadores/jogadores.pygame` |

Os destinos dos ganchos e do serviço são pastas que o Batocera procura por convenção: ele executa os scripts de
`/userdata/system/scripts/` ao abrir e fechar jogos, os de `.../scripts/achievements/` a cada conquista, e
gerencia como serviço o que está em `/userdata/system/services/`.

## Pastas e arquivos criados em uso

Tudo dentro de `/userdata/system/arcade/`:

| Caminho | Conteúdo | Quem escreve |
|---|---|---|
| `arcade.conf` | Configuração MQTT e `DEVICE_ID`, em `CHAVE=valor`. Legível só pelo root | `install.sh` |
| `spool/` | **Fila de saída.** Um arquivo JSON por mensagem a enviar | ganchos e tela de login |
| `state/session.json` | Quem está logado neste aparelho | `arcaded.py` (após confirmação do servidor) |
| `state/auth_pending.json` | Pedido de login/cadastro que a tela está esperando | tela de login |
| `state/auth_result.json` | Última resposta do servidor a um pedido de login/cadastro | `arcaded.py` |
| `state/current_play.json` | Partida em andamento | `hook.py` |
| `state/live_score.json` | Melhor score e fase da partida atual | `arcaded.py` |
| `state/save_owners.json` | De quem são os saves que estão no emulador agora | `saves.py` |
| `state/saves_synced.json` | O que já foi enviado para a nuvem | `arcaded.py` |
| `state/clock_ok` | `boot_id` do boot em que a hora já foi acertada (vale se o serviço reiniciar) | `arcaded.py` |
| `saves/<usuário>/<sistema>/` | Cópia dos saves de cada jogador | `saves.py`, `arcaded.py` |
| `saves/@convidado/` | Saves de quem joga sem login. Não vão para a nuvem | `saves.py` |
| `saves/@backup-inicial/` | Saves que existiam antes de o recurso ser instalado | `saves.py` |
| `arcaded.log` | Log do serviço (rotativo, 512 KB) | `arcaded.py` |
| `hook.log` | Erros dos ganchos de jogo | `hook.py` |
| `achievements.log` | Todas as conquistas recebidas, com os argumentos crus | `achievement.py` |

As pastas que começam com `@` nunca colidem com um jogador, porque `@` não é permitido em nome de usuário.

### A fila `spool/`

Cada arquivo se chama `<horário em nanossegundos>-<tipo>.json`, por exemplo `1790697726123456789-events.json`. O
tipo (`login`, `events`, `achievements`) define o tópico MQTT de destino, e o horário no nome mantém a ordem de
envio. O serviço publica um arquivo por vez e só o apaga depois da confirmação do broker.

Cada arquivo também guarda um campo `_clock` (identificador do boot, relógio comum e relógio monotônico do momento
em que entrou na fila). Ele não sai do Pi: o serviço o tira antes de publicar e, se a hora foi acertada depois que a
mensagem entrou na fila, desloca `started_at`, `ended_at`, `unlocked_at` e `ts` junto com o acerto. Partidas e
conquistas esperam a hora ficar confiável; login e logout saem na hora.

## Os quatro programas

### `arcaded.py` — o serviço

Inicia no boot pelo script `arcade_score` e nunca termina. Tarefas:

| Tarefa | Frequência |
|---|---|
| Enviar a fila `spool/` | A cada 1 segundo |
| Enviar saves de jogadores que mudaram | A cada 10 segundos |
| Ler score e fase do jogo em execução | A cada 1 segundo |
| Aplicar respostas do servidor (login, cadastro, saves recebidos) | Quando chegam |
| Ponte com o ESP32: telemetria, LEDs e setas do teclado virtual | A cada leitura do ESP32 |
| Salvar a hora no `fake-hwclock` (depois que ela é confiável) | A cada 10 minutos |

**Relógio:** o Pi não tem relógio com bateria. Cada boot começa na hora salva pelo `fake-hwclock`, e o `ntpd` só
acerta quando a rede sobe. O serviço considera a hora confiável quando vê o `ntpd` acertá-la, quando ela já não é a
carregada no boot ou, sem acerto, depois de 10 minutos conectado. O log mostra `Relógio: ... (motivo)`.

Conecta e reconecta sozinho, com espera crescente de 1 a 60 segundos, inclusive se a rede ainda não tiver subido
no boot.

### `hook.py` — início e fim de jogo

O Batocera chama `arcade-score.sh gameStart|gameStop <sistema> <emulador> <core> <caminho da ROM>`, que repassa
para o `hook.py`.

- **`gameStart`:** cria o `play_uuid`, descobre o título, calcula o hash da ROM, lê quem está logado, grava
  `current_play.json`, põe o evento na fila e troca os saves para os do jogador.
- **`gameStop`:** recupera a partida de `current_play.json`, acrescenta o melhor score de `live_score.json`, põe
  o evento na fila e guarda os saves na cópia do jogador. Se a hora foi acertada no meio da partida, refaz o início
  pelo relógio monotônico guardado no `gameStart`.

O Batocera espera o gancho terminar antes de abrir o jogo. Por isso ele só lê e grava arquivos locais, e qualquer
erro é anotado em `hook.log` sem impedir o jogo de abrir.

**Hash da ROM:** MD5 no formato do RetroAchievements. Em arquivos `.zip`, usa o maior arquivo de dentro; remove o
cabeçalho de copiadora no SNES e o cabeçalho iNES no NES. Arquivos acima de 64 MB (imagens de CD) ficam sem hash.

### `achievement.py` — conquista

O Batocera chama o gancho a cada conquista desbloqueada, com três argumentos: ID da conquista, título e descrição. O script junta a partida
atual e o jogador logado e põe o desbloqueio na fila.

### `jogadores.pygame` — tela de login

Aparece no menu como "Login do Arcade". Não usa a rede: põe o pedido na fila e espera a resposta aparecer em
`state/auth_result.json`, por até 10 segundos em cada etapa. O funcionamento do login está em
[arquitetura.md](arquitetura.md#login-no-fliperama).

## Leitura de score

O serviço pergunta ao RetroArch, por UDP na porta 55355 do próprio Pi:

```
READ_CORE_RAM 0xfe26 4
```

e recebe os bytes daquele endereço:

```
READ_CORE_RAM fe26 00 01 86 a0
```

O arquivo `memory_maps.json` diz, para cada jogo, quais endereços ler e como interpretar. A chave é o hash da ROM:

```json
"1bc674be034e43c96b86487ac69d9293": {
  "name": "Sonic The Hedgehog (Mega Drive, USA/Europa)",
  "verified": true,
  "score":  { "address": "0xfe26", "size": 4, "format": "be_swapped", "multiplier": 10, "max": 9999990 },
  "stage":  { "address": "0xfe11", "size": 1, "map": { "0": 1, "2": 2, "4": 3, "1": 4, "3": 5, "5": 6 } },
  "active": { "address": "0xf601", "size": 1, "in": [12, 16] }
}
```

| Campo | Significado |
|---|---|
| `address`, `size` | Endereço na memória e quantos bytes ler |
| `format` | `be` (padrão), `le`, `bcd`, `bcd_le` ou `be_swapped` (Mega Drive) |
| `multiplier`, `add` | Ajuste do valor lido: `valor × multiplier + add` |
| `max` | Leituras acima disso são descartadas como lixo de memória |
| `map` | Traduz códigos internos para a ordem do jogo (ex.: ID da zona → número da fase) |
| `active` | O score só conta quando o endereço indicado tem um dos valores de `in` (exclui o modo demonstração) |

Para incluir um jogo novo, descubra os endereços com o jogo rodando, acrescente a entrada ao `memory_maps.json`
e rode o `install.sh` de novo.

A leitura de memória só funciona com uma conta do RetroAchievements ativa no Batocera e em jogos compatíveis com
ele.

## Configurações alteradas no Batocera

| Chave | Valor | Para quê |
|---|---|---|
| `global.retroarch.network_cmd_enable` | `true` | Permite a leitura de memória |
| `global.retroarch.network_cmd_port` | `55355` | Porta dos comandos |
| `global.retroachievements.encore` | `1` | Conquistas voltam a disparar a cada partida, para cada jogador poder recebê-las |

## Diagnóstico

Comandos para rodar no Pi, por SSH (`ssh -i ~/.ssh/arcade_pi root@<ip-do-pi>`):

| Para ver | Comando |
|---|---|
| Se o serviço está rodando | `bash /userdata/system/services/arcade_score status` |
| O que o serviço está fazendo | `tail -f /userdata/system/arcade/arcaded.log` |
| Erros ao abrir ou fechar jogos | `cat /userdata/system/arcade/hook.log` |
| Conquistas recebidas | `cat /userdata/system/arcade/achievements.log` |
| Mensagens paradas na fila | `ls /userdata/system/arcade/spool/` |
| Quem está logado | `cat /userdata/system/arcade/state/session.json` |
| Reiniciar o serviço | `batocera-services stop arcade_score; batocera-services start arcade_score` |

| Sintoma | Onde olhar |
|---|---|
| Partidas não aparecem no site | Arquivos acumulando em `spool/` indicam que o Pi não está conectado ao broker: veja `arcaded.log`. Fila vazia indica que o problema está no back-end. |
| `MQTT recusou a conexão` no log | Credencial errada no `arcade.conf`. Corrija o `.env` e rode o `install.sh` de novo. |
| `Inscrição recusada` no log | A credencial do Pi no HiveMQ precisa de permissão de *Publish and Subscribe*. |
| Login fica em "Sem resposta do servidor" | O Pi está sem internet ou o back-end está desligado. |
| Jogo mapeado sem score | Confira se o RetroAchievements está ativo no Batocera e se o hash da ROM é o mesmo do `memory_maps.json` (com o jogo aberto, veja `rom_md5` em `state/current_play.json`). |

## Segurança

- Troque a senha padrão do root do Batocera (`linux`).
- O `arcade.conf` contém a credencial MQTT do Pi em texto; ele é criado com permissão de leitura só para o root.
- A senha dos jogadores nunca é gravada no Pi nem enviada pela rede.
- A porta UDP 55355 do RetroArch fica acessível na rede local.
