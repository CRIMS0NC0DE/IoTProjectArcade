"""Saves por jogador: troca os arquivos de save do jogo conforme quem está jogando.

O RetroArch lê e grava em /userdata/saves/<sistema>/<nome da ROM>.* (.srm, .state, .state1, .png...), um
arquivo por ROM para todo mundo. Cada jogador tem a própria cópia em saves/<usuário>/<sistema>/: no início
da partida o gancho põe no lugar os arquivos de quem vai jogar e, no fim, guarda de volta o que o emulador
gravou. A sincronização dessas cópias com a nuvem fica com o daemon.

Regra de ouro: nenhum arquivo é apagado sem antes existir uma cópia igual ou mais nova em outro lugar.
"""

import shutil
from pathlib import Path

from common import GUEST, INITIAL_BACKUP, LIVE_SAVES, SAVE_OWNERS, SAVE_STORE, read_json, write_json


def save_files(directory: Path, stem: str) -> list[Path]:
    """Arquivos de save de uma ROM: "<nome da ROM>.srm", ".state", ".state1", ".png"..."""
    if not directory.is_dir():
        return []
    return sorted(f for f in directory.iterdir() if f.is_file() and f.name.startswith(stem + "."))


def copy_if_newer(src: Path, dst_dir: Path) -> None:
    """Copia preservando a data de modificação; nunca troca um arquivo por uma versão mais antiga."""
    dst = dst_dir / src.name
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def restore(user: str | None, system: str, rom: Path) -> None:
    """Antes do jogo abrir: guarda os saves de quem jogou por último e põe no lugar os de quem vai jogar."""
    user = user or GUEST
    stem, live = rom.stem, LIVE_SAVES / system
    key = f"{system}/{stem}"
    owners = read_json(SAVE_OWNERS, {})
    current = save_files(live, stem)
    owner = owners.get(key)
    if owner is None and current:
        # Saves de antes deste recurso: vão para um backup e ficam com quem jogar primeiro.
        for f in current:
            copy_if_newer(f, SAVE_STORE / INITIAL_BACKUP / system)
        owner = user
    for f in current:
        copy_if_newer(f, SAVE_STORE / owner / system)  # a cópia do dono fica em dia (ex.: faltou um gameStop)
    mine = save_files(SAVE_STORE / user / system, stem)
    if owner != user:  # outro jogador: os arquivos do dono já estão guardados, então dá para trocar
        for f in current:
            f.unlink()
        live.mkdir(parents=True, exist_ok=True)
        for f in mine:
            shutil.copy2(f, live / f.name)
    else:  # mesmo jogador: só traz o que chegou da nuvem mais novo que o arquivo local
        for f in mine:
            copy_if_newer(f, live)
    owners[key] = user
    write_json(SAVE_OWNERS, owners)


def store(user: str | None, system: str, rom: Path) -> None:
    """Depois do jogo: guarda na cópia do jogador o que o emulador gravou.

    Nada é apagado da cópia: um save state removido dentro do jogo pode reaparecer, mas progresso nunca se perde.
    """
    dst = SAVE_STORE / (user or GUEST) / system
    for f in save_files(LIVE_SAVES / system, rom.stem):
        copy_if_newer(f, dst)
