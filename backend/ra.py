"""Cliente mínimo da API web do RetroAchievements, com cache no Postgres (a API pede cache agressivo).

Tudo aqui é opcional: sem RA_API_KEY no .env, as conquistas continuam sendo gravadas pelo que o Pi manda,
só sem a lista completa de cada jogo e sem os ícones.
"""

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import timedelta

import db

API = "https://retroachievements.org/API/"
HASH_CACHE = timedelta(days=7)
# Pasta do sistema no Batocera -> ID do console no RetroAchievements.
CONSOLES = {
    "megadrive": 1, "n64": 2, "snes": 3, "gb": 4, "gba": 5, "gbc": 6, "nes": 7, "pcengine": 8,
    "mastersystem": 11, "psx": 12, "gamegear": 15, "atari2600": 25,
}

log = logging.getLogger("backend")


def enabled() -> bool:
    return bool(os.getenv("RA_API_KEY"))


def _get(endpoint: str, **params) -> dict | list:
    query = urllib.parse.urlencode({**params, "y": os.environ["RA_API_KEY"]})
    request = urllib.request.Request(
        f"{API}{endpoint}?{query}", headers={"User-Agent": "ArcadeCloudScore/1.0 (projeto academico FATEC)"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def game_id_for(system: str, md5: str) -> int | None:
    """Jogo do RA para a ROM, pela lista oficial de hashes do console (baixada no máximo 1x por semana)."""
    console = CONSOLES.get(system)
    if console is None:
        return None
    if db.ra_hashes_stale(console, HASH_CACHE):
        games = _get("API_GetGameList.php", i=console, f=1, h=1)
        db.replace_ra_hashes(console, [(h.lower(), g["ID"]) for g in games for h in g.get("Hashes") or []])
        log.info("RetroAchievements: lista de hashes do console %d atualizada (%d jogos)", console, len(games))
    return db.ra_game_for_hash(md5)


def sync_game(ra_game_id: int) -> dict:
    """Baixa a lista completa de conquistas do jogo (títulos, pontos, ícones) e grava no banco."""
    game = _get("API_GetGameExtended.php", i=ra_game_id)
    achievements = game.get("Achievements") or {}
    items = achievements.values() if isinstance(achievements, dict) else achievements
    db.upsert_achievements(ra_game_id, [
        (a["ID"], a["Title"], a.get("Description") or "", a.get("Points"), a.get("BadgeName"), a.get("DisplayOrder"))
        for a in items
    ])
    log.info("RetroAchievements: %s com %d conquistas", game.get("Title"), len(items))
    return game


def link_game(system: str, rom: str, md5: str) -> None:
    """Na primeira partida de uma ROM: descobre o jogo no RA e prepara a vitrine de conquistas."""
    if not enabled() or db.game_ra_id(system, rom) is not None:
        return
    ra_game_id = game_id_for(system, md5)
    if ra_game_id is None:
        log.info("ROM sem conquistas no RetroAchievements: %s/%s", system, rom)
        return
    game = sync_game(ra_game_id)
    db.set_game_ra(system, rom, ra_game_id, game.get("ImageIcon"))


def sync_achievement_game(achievement_id: int, play_uuid=None) -> None:
    """Depois de um desbloqueio: garante que a conquista e a partida estejam ligadas ao jogo do RA.

    Cobre o caso em que o RetroArch reconheceu a ROM mas o nosso hash não bateu com a lista do RA
    (outro formato de arquivo): sem isso, o jogo não apareceria na vitrine do jogador.
    """
    if not enabled():
        return
    ra_game_id, icon = db.achievement_ra_game(achievement_id), None
    if ra_game_id is None:
        data = _get("API_GetAchievementUnlocks.php", a=achievement_id, c=1)
        ra_game_id = data["Game"]["ID"]
        icon = sync_game(ra_game_id).get("ImageIcon")
        # Conquistas não oficiais não vêm na lista do jogo: liga esta ao jogo com o que a API informou.
        db.set_achievement_game(achievement_id, ra_game_id, data["Achievement"].get("Points"))
    if play_uuid:
        db.link_play_game(play_uuid, ra_game_id, icon)
