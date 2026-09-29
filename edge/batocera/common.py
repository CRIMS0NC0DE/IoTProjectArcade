"""Caminhos e utilidades compartilhados pelo daemon, pelo gancho de jogo e pela tela de jogadores."""

import json
import os
import time
from pathlib import Path

BASE = Path("/userdata/system/arcade")
CONFIG = BASE / "arcade.conf"
SPOOL = BASE / "spool"  # fila de mensagens esperando o daemon enviar
STATE = BASE / "state"
SESSION = STATE / "session.json"  # quem está logado neste aparelho (só depois de o servidor confirmar)
AUTH_PENDING = STATE / "auth_pending.json"  # pedido de login/cadastro que a tela está esperando
AUTH_RESULT = STATE / "auth_result.json"  # última resposta do servidor a um pedido de login/cadastro
CURRENT_PLAY = STATE / "current_play.json"  # partida em andamento, para o gameStop reusar o mesmo UUID
LIVE_SCORE = STATE / "live_score.json"  # última leitura de score da partida atual (escrita pelo daemon)
MEMORY_MAPS = BASE / "memory_maps.json"  # onde ler score e fase de cada jogo, por hash da ROM
LIVE_SAVES = Path("/userdata/saves")  # onde o RetroArch lê e grava os saves: <sistema>/<nome da ROM>.*
SAVE_STORE = BASE / "saves"  # cópia de cada jogador: saves/<usuário>/<sistema>/<arquivo>
SAVE_OWNERS = STATE / "save_owners.json"  # de quem são os saves que estão hoje em /userdata/saves
SAVES_SYNCED = STATE / "saves_synced.json"  # o que já foi enviado para a nuvem (hash, data, tamanho)
# "@" não é permitido em nome de usuário: estas pastas nunca colidem com um jogador e ficam só no Pi.
GUEST = "@convidado"
INITIAL_BACKUP = "@backup-inicial"


def load_config() -> dict:
    # arcade.conf é gerado pelo install.sh já com os valores limpos: KEY=VALUE, sem aspas nem comentários.
    config = {}
    for line in CONFIG.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep:
            config[key] = value
    return config


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    # Grava num temporário e renomeia: quem lê nunca pega o arquivo pela metade.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


def enqueue(kind: str, payload: dict) -> None:
    """Põe uma mensagem na fila para arcade/devices/<id>/<kind>; o daemon envia quando houver conexão."""
    write_json(SPOOL / f"{time.time_ns()}-{kind}.json", payload)  # o nome mantém a ordem cronológica
