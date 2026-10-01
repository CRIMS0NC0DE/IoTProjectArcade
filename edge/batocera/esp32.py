"""Ponte com o ESP32 ligado por USB (joystick, LEDs e DHT11): a telemetria vai para o MQTT, os comandos do site
vão para os LEDs e, enquanto houver jogo aberto no Batocera, os LEDs piscam.

O ESP32 manda uma linha JSON por leitura; linhas que não começam com "{" são texto para o Monitor Serial.
"""

import glob
import json
import logging
import os
import termios
import threading
import time

PORTS = ("/dev/ttyUSB*", "/dev/ttyACM*")  # conversores CP2102/CH340 viram ttyUSB; placas com USB nativo, ttyACM
SILENCE = 10  # s sem telemetria válida: porta errada ou ESP32 travado; fecha e procura de novo
COLORS = ("green", "yellow", "red")
MODES = ("joystick", "site")

log = logging.getLogger("arcaded")
_lock = threading.Lock()  # protege _fd: a thread do MQTT escreve comandos enquanto a da ponte lê
_fd: int | None = None
_playing = False  # há jogo aberto no Batocera: o ESP32 anima os LEDs enquanto for True


def _open(path: str) -> int:
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
    attrs = termios.tcgetattr(fd)
    attrs[0] = attrs[1] = attrs[3] = 0  # modo cru: sem eco e sem tradução de fim de linha
    # Sem HUPCL: fechar a porta não derruba o DTR, que nas placas de desenvolvimento reinicia o ESP32.
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[4] = attrs[5] = termios.B115200
    attrs[6][termios.VMIN], attrs[6][termios.VTIME] = 0, 10  # read() volta depois de 1 s sem dados
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIFLUSH)
    return fd


def _telemetry(line: bytes) -> dict | None:
    if not line.startswith(b"{"):
        return None
    try:
        data = json.loads(line)
    except ValueError:
        return None  # linha cortada (ex.: o ESP32 reiniciou no meio dela)
    return data if isinstance(data, dict) else None


def _serve(path: str, publish) -> None:
    global _fd
    fd = _open(path)
    with _lock:
        _fd = fd
    found, last, buf = False, time.monotonic(), b""
    try:
        while time.monotonic() - last < SILENCE:
            chunk = os.read(fd, 512)
            if not chunk:
                if not os.path.exists(path):
                    break  # cabo desconectado
                continue
            *lines, buf = (buf + chunk).split(b"\n")
            buf = buf[-1024:]  # lixo sem quebra de linha não cresce para sempre
            for line in lines:
                if (data := _telemetry(line.strip())) is None:
                    continue
                if not found:
                    log.info("ESP32 conectado em %s", path)
                    found = True
                last = time.monotonic()
                playing = data.get("playing")
                if isinstance(playing, bool) and playing != _playing:  # ex.: o ESP32 reiniciou no meio do jogo
                    _send(f"GAME {'on' if _playing else 'off'}")
                publish(data)
    finally:
        with _lock:
            _fd = None
        os.close(fd)
        if found:
            log.warning("ESP32 desconectado de %s", path)


def run(publish) -> None:
    """Procura o ESP32 nas portas USB-serial e entrega cada leitura a publish(dict). Roda numa thread própria."""
    while True:
        for path in sorted(p for pattern in PORTS for p in glob.glob(pattern)):
            try:
                _serve(path, publish)
            except OSError as exc:
                if os.path.exists(path):  # sumir no meio da leitura é só o cabo saindo
                    log.warning("Erro na porta %s: %s", path, exc)
            except Exception:
                log.exception("Erro na ponte com o ESP32")
        time.sleep(3)


def _send(line: str) -> bool:
    """Escreve um comando na serial. False se o ESP32 não estiver conectado."""
    with _lock:
        if _fd is None:
            return False
        try:
            os.write(_fd, f"{line}\n".encode())
        except OSError:
            return False  # cabo saiu agora: a thread da ponte percebe e volta a procurar
    return True


def set_playing(playing: bool) -> None:
    """Avisa o ESP32 quando um jogo abre ou fecha. Se ele estiver desconectado, o aviso vai quando ele voltar."""
    global _playing
    if playing != _playing:
        _playing = playing
        log.info("LEDs: %s", "jogo aberto, animação ligada" if playing else "jogo fechado, animação desligada")
        _send(f"GAME {'on' if playing else 'off'}")


def command(cmd: dict) -> None:
    """Comando do site para os LEDs. Sem o ESP32 conectado, é descartado: só vale ao vivo."""
    if cmd.get("type") == "led" and cmd.get("color") in COLORS and isinstance(cmd.get("on"), bool):
        line = f"LED {cmd['color']} {255 if cmd['on'] else 0}"
    elif cmd.get("type") == "led_mode" and cmd.get("mode") in MODES:
        line = f"MODE {cmd['mode']}"
    else:
        log.warning("Comando de LED inválido: %r", cmd)
        return
    if _send(line):
        log.info("Comando enviado ao ESP32: %s", line)
    else:
        log.warning("Comando descartado, o ESP32 não está conectado: %s", line)
