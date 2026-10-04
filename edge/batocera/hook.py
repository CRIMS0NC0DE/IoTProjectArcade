"""Gancho do Batocera: chamado no início e no fim de cada jogo por /userdata/system/scripts/arcade-score.sh.

Argumentos: gameStart|gameStop <sistema> <emulador> <core> <caminho da ROM>.
O Batocera espera este script terminar antes de abrir o jogo, por isso ele só grava na fila local;
quem fala com a rede é o daemon.
"""

import hashlib
import re
import sys
import time
import traceback
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import saves
from common import BASE, CURRENT_PLAY, LIVE_SCORE, SESSION, boot_id, clock_stamp, enqueue, read_json, write_json

ROMS = Path("/userdata/roms")
IGNORED = {ROMS / "pygame/jogadores/jogadores.pygame"}  # a própria tela de login não conta como partida


def title_for(rom: Path) -> str:
    """Nome do jogo no gamelist.xml do sistema; sem ele, o nome do arquivo sem tags como (USA) e [!]."""
    try:
        system_dir = ROMS / rom.relative_to(ROMS).parts[0]
        wanted = "./" + rom.relative_to(system_dir).as_posix()
        for game in ET.parse(system_dir / "gamelist.xml").getroot().iter("game"):
            if game.findtext("path") == wanted and game.findtext("name"):
                return game.findtext("name").strip()
    except (ValueError, OSError, ET.ParseError):
        pass
    return re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", rom.stem).strip() or rom.stem


def ra_hash(rom: Path, system: str) -> str | None:
    """MD5 da ROM no formato do RetroAchievements: identifica a versão exata (conquistas e endereços de memória)."""
    try:
        if rom.suffix.lower() == ".zip":
            with zipfile.ZipFile(rom) as z:
                info = max(z.infolist(), key=lambda i: i.file_size)
                data = z.read(info) if info.file_size <= 64 * 1024 * 1024 else None
        else:
            data = rom.read_bytes() if rom.stat().st_size <= 64 * 1024 * 1024 else None
    except (OSError, zipfile.BadZipFile):
        return None
    if data is None:
        return None  # imagens de CD e afins ficam de fora
    if system == "snes" and len(data) % 8192 == 512:
        data = data[512:]  # cabeçalho de copiadora
    elif system == "nes" and data[:4] == b"NES\x1a":
        data = data[16:]  # cabeçalho iNES
    return hashlib.md5(data).hexdigest()


def guarded(fn, *args) -> None:
    """Um erro com os saves nunca pode impedir o jogo de abrir nem a partida de ser registrada."""
    try:
        fn(*args)
    except Exception:
        with open(BASE / "hook.log", "a") as log:
            log.write(f"{time.ctime()} {fn.__name__}{args}\n{traceback.format_exc()}\n")


def main(event: str, system: str, emulator: str, core: str, rom_path: str, *_) -> None:
    rom = Path(rom_path)
    if rom in IGNORED:
        return
    now = int(time.time())
    if event == "gameStart":
        play = {
            "play_uuid": str(uuid.uuid4()),
            "system": system,
            "rom": rom.name,
            "title": title_for(rom),
            "rom_md5": ra_hash(rom, system),
            "username": (read_json(SESSION) or {}).get("username"),  # None = convidado
            "started_at": now,
        }
        write_json(CURRENT_PLAY, {**play, "clock": clock_stamp()})
        enqueue("events", {**play, "event": "start"})
        guarded(saves.restore, play["username"], system, rom)  # põe no lugar os saves de quem vai jogar
    elif event == "gameStop":
        play = read_json(CURRENT_PLAY)
        if not play or play["rom"] != rom.name:
            return  # sem início registrado para esta ROM
        CURRENT_PLAY.unlink(missing_ok=True)
        clock = play.pop("clock", None)
        if clock and clock["boot"] == boot_id():
            # Se o ntpd acertou o relógio no meio da partida, o started_at gravado está na hora antiga:
            # refaz pelo monotônico, que não pula. Sem isso, um jogo aberto logo depois do boot durava semanas.
            play["started_at"] = now - round(time.monotonic() - clock["mono"])
        stop = {**play, "event": "stop", "ended_at": max(now, play["started_at"])}
        live = read_json(LIVE_SCORE) or {}
        if live.get("play_uuid") == play["play_uuid"]:  # última leitura de memória feita pelo daemon
            stop |= {"score": live["best_score"], "stage_reached": live.get("stage", 0)}
        enqueue("events", stop)
        guarded(saves.store, play["username"], system, rom)  # guarda o que o emulador gravou na cópia do jogador


if __name__ == "__main__":
    try:
        main(*sys.argv[1:])
    except Exception:
        # Nunca atrapalhar a abertura do jogo: registra o erro e sai normalmente.
        with open(BASE / "hook.log", "a") as log:
            log.write(f"{time.ctime()} {sys.argv[1:]}\n{traceback.format_exc()}\n")
