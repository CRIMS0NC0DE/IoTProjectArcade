"""Gancho de conquistas do Batocera: o RetroArch chama a cada desbloqueio do RetroAchievements.

Chega via /userdata/system/configs/emulationstation/scripts/achievements/arcade-score.sh com 3 argumentos:
ID da conquista no RetroAchievements, título e descrição (conferido no Batocera 43). Os argumentos crus
também vão para achievements.log, para conferência.
"""

import sys
import time
import traceback

from common import BASE, CURRENT_PLAY, SESSION, enqueue, read_json


def main(args: list[str]) -> None:
    with open(BASE / "achievements.log", "a") as log:
        log.write(f"{time.ctime()} {args!r}\n")
    play = read_json(CURRENT_PLAY) or {}
    enqueue("achievements", {
        "achievement_id": int(args[0]) if args and args[0].isdigit() else None,
        "title": args[1] if len(args) > 1 else "",
        "description": args[2] if len(args) > 2 else "",
        "play_uuid": play.get("play_uuid"),
        "rom_md5": play.get("rom_md5"),
        "username": (read_json(SESSION) or {}).get("username"),  # None = convidado: não vai para perfil nenhum
        "unlocked_at": int(time.time()),
    })


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception:
        with open(BASE / "achievements.log", "a") as log:
            log.write(traceback.format_exc() + "\n")
