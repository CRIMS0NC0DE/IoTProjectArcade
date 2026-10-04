"""Daemon do arcade (serviço arcade_score): mantém a conexão MQTT, esvazia a fila local e aplica as respostas do servidor."""

import base64
import calendar
import hashlib
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))  # paho-mqtt copiado pelo install.sh
import paho.mqtt.client as mqtt  # noqa: E402

import esp32  # noqa: E402
from common import (  # noqa: E402
    AUTH_PENDING, AUTH_RESULT, BASE, CURRENT_PLAY, LIVE_SCORE, MEMORY_MAPS, SAVE_STORE, SAVES_SYNCED, SESSION, SPOOL,
    STATE,
    boot_id, load_config,
    read_json, write_json,
)

RETROARCH = ("127.0.0.1", 55355)  # comandos de rede do RetroArch (global.retroarch.network_cmd_enable)
MAX_SAVE_BYTES = 3 * 1024 * 1024  # o HiveMQ entrega mensagens de até ~4 MB, e o base64 aumenta 1/3
USERNAME = re.compile(r"[a-z0-9_]{3,20}")
SAFE_SYSTEM = re.compile(r"[a-z0-9_]{1,32}")
SAFE_FILENAME = re.compile(r"[^/\\.][^/\\]*")  # sem barras e sem começar com ponto: nada sai da pasta do jogador

handler = RotatingFileHandler(BASE / "arcaded.log", maxBytes=512 * 1024, backupCount=1)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[handler])
log = logging.getLogger("arcaded")

config = load_config()
DEVICE = config["DEVICE_ID"]
DEVICE_TOPIC = f"arcade/devices/{DEVICE}"
connected = threading.Event()
saves_lock = threading.Lock()  # protege saves_synced.json (thread principal x thread do MQTT)


def notify(text: str) -> None:
    """Balão na tela do EmulationStation (API local na porta 1234)."""
    try:
        urllib.request.urlopen(urllib.request.Request("http://localhost:1234/notify", data=text.encode()), timeout=2)
    except OSError:
        pass


def game_running() -> bool:
    """Há uma partida em andamento? O gancho grava current_play.json (a tela de login não conta) e a API do
    EmulationStation confirma, porque o arquivo pode ter sobrado de um desligamento no meio do jogo."""
    if not CURRENT_PLAY.exists():
        return False
    try:
        with urllib.request.urlopen("http://localhost:1234/runningGame", timeout=2) as reply:
            return "path" in json.load(reply)  # sem jogo: {"msg": "NO GAME RUNNING"}
    except (OSError, ValueError):
        return True  # API fora do ar (ex.: ES ainda subindo): vale o arquivo do gancho


def handle_command(client: mqtt.Client, cmd: dict) -> None:
    kind = cmd.get("type")
    if kind == "save_file":
        receive_save(cmd)
        return
    if kind in ("led", "led_mode"):
        esp32.command(cmd)
        return
    # Respostas de login/cadastro: a tela de login espera por elas em auth_result.json.
    write_json(AUTH_RESULT, cmd)
    pending = (read_json(AUTH_PENDING) or {}).get("request_id") == cmd.get("request_id")
    if kind in ("login_ok", "register_ok") and pending:  # resposta atrasada de uma tentativa antiga não loga ninguém
        write_json(SESSION, {"username": cmd["username"], "player_tag": cmd["player_tag"], "since": int(time.time()),
                             "confirmed": True})
        notify(f"Bem-vindo, {cmd['player_tag']}!")
        log.info("%s: %s", "Conta criada e logada" if kind == "register_ok" else "Login confirmado", cmd["username"])
        send_manifest(client, cmd["username"])  # traz os saves dele que estiverem mais novos na nuvem
    elif kind in ("login_error", "register_error"):
        log.warning("%s: %s", "Login recusado" if kind == "login_error" else "Cadastro recusado", cmd.get("message"))


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        log.error("MQTT recusou a conexão: %s", reason_code)
        return
    log.info("MQTT conectado")
    client.subscribe(f"{DEVICE_TOPIC}/commands", qos=1)
    connected.set()
    if username := (read_json(SESSION) or {}).get("username"):
        send_manifest(client, username)


def on_disconnect(client, userdata, flags, reason_code, properties):
    connected.clear()
    log.warning("MQTT desconectado: %s", reason_code)


def on_subscribe(client, userdata, mid, reason_code_list, properties):
    for rc in reason_code_list:
        if rc.is_failure:
            log.error("Inscrição recusada: %s (a credencial do Pi precisa de permissão de subscribe)", rc)


def on_message(client, userdata, msg):
    # Roda na thread do paho: uma exceção que escape daqui mata o loop MQTT.
    try:
        if msg.topic == f"{DEVICE_TOPIC}/commands":
            handle_command(client, json.loads(msg.payload))
    except Exception:
        log.exception("Erro ao processar %s", msg.topic)


# ---------- relógio ----------
# O Pi não tem relógio com bateria: cada boot começa na hora que o fake-hwclock salvou (o Batocera só salva ao
# desligar pelo menu, então ficava sempre em 09/09) e o ntpd só acerta quando a rede sobe, minutos depois.
# Tudo o que acontece antes disso sai com a data errada, e uma partida que atravessa o acerto durava semanas.

FAKE_HWCLOCK = Path("/userdata/system/fake-hwclock.data")
CLOCK_OK = STATE / "clock_ok"  # boot_id do boot em que o relógio já foi acertado (sobrevive a reiniciar o daemon)
TIMED_KINDS = ("events", "achievements")  # esperam o relógio acertar; login e saves não podem esperar
TIME_FIELDS = ("started_at", "ended_at", "unlocked_at", "ts")
BOOT = boot_id()
clock_ok = threading.Event()


def wall_offset() -> float:
    return time.time() - time.monotonic()


def fix_times(payload: dict) -> dict:
    """Tira o "_clock" da mensagem e, se o relógio foi acertado depois que ela entrou na fila (no mesmo boot),
    leva os horários junto com o acerto."""
    stamp = payload.pop("_clock", None)
    if stamp and stamp.get("boot") == BOOT:
        shift = round(wall_offset() - (stamp["wall"] - stamp["mono"]))
        if abs(shift) >= 2:
            for field in TIME_FIELDS:
                if isinstance(payload.get(field), int):
                    payload[field] += shift
    return payload


def wait_clock() -> str:
    """Espera a hora do Pi ficar confiável e diz por quê."""
    start = wall_offset()
    if CLOCK_OK.exists() and CLOCK_OK.read_text() == BOOT:
        return "já acertado neste boot"
    try:
        saved = calendar.timegm(time.strptime(FAKE_HWCLOCK.read_text().strip(), "%Y-%m-%d %H:%M:%S"))  # em UTC
        if time.time() - time.clock_gettime(time.CLOCK_BOOTTIME) - saved > 120:
            return "acertado antes de o daemon subir"  # a hora já não é a que o fake-hwclock carregou no boot
    except (OSError, ValueError):
        pass
    online_since = None
    while True:
        time.sleep(1)
        if abs(wall_offset() - start) >= 2:
            return "acertado pela internet"
        if not connected.is_set():
            online_since = None
        elif online_since is None:
            online_since = time.monotonic()
        elif time.monotonic() - online_since > 600:
            return "sem acerto depois de 10 min online; seguindo com a hora atual"


def watch_clock() -> None:
    """Libera o envio de partidas e conquistas quando a hora fica confiável e, daí em diante, salva a hora no
    fake-hwclock a cada 10 minutos, para o próximo boot começar perto da hora certa (os saves usam a data do
    arquivo para decidir qual versão é a mais nova)."""
    try:
        reason = wait_clock()
        CLOCK_OK.write_text(BOOT)
    except Exception:
        log.exception("Erro ao conferir o relógio")
        reason = "erro ao conferir; seguindo com a hora atual"
    clock_ok.set()  # nunca segurar a fila para sempre
    log.info("Relógio: %s (%s)", time.strftime("%d/%m/%Y %H:%M:%S"), reason)
    while True:
        try:
            subprocess.run(["fake-hwclock", "save"], capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("fake-hwclock save falhou: %s", exc)
        time.sleep(600)


def flush(client: mqtt.Client) -> None:
    """Envia a fila em ordem; um arquivo só sai da fila depois do PUBACK do broker."""
    for path in sorted(SPOOL.glob("*.json")):
        if not connected.is_set():
            return
        kind = path.stem.split("-", 1)[1]  # 1727612800123-events.json -> events
        if kind in TIMED_KINDS and not clock_ok.is_set():
            continue  # partidas e conquistas esperam a hora certa; entre elas a ordem se mantém
        payload = read_json(path)
        data = json.dumps(fix_times(payload)).encode() if isinstance(payload, dict) else path.read_bytes()
        info = client.publish(f"{DEVICE_TOPIC}/{kind}", data, qos=1)
        info.wait_for_publish(timeout=10)
        if not info.is_published():
            return  # tenta de novo no próximo ciclo
        path.unlink()
        log.info("Enviado: %s", path.name)


# ---------- saves na nuvem ----------


def player_save_files(username: str) -> list[Path]:
    return sorted(p for p in (SAVE_STORE / username).glob("*/*") if p.is_file() and not p.name.startswith("."))


def send_manifest(client: mqtt.Client, username: str) -> None:
    """Conta ao servidor quais saves do jogador este Pi tem; ele responde com os que estiverem mais novos na nuvem."""
    files = {
        f"{f.parent.name}/{f.name}": {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "modified_at": int(f.stat().st_mtime)}
        for f in player_save_files(username)
    }
    client.publish(f"{DEVICE_TOPIC}/saves", json.dumps({"type": "manifest", "username": username, "files": files}), qos=1)
    log.info("Lista de saves enviada: %s (%d arquivo(s))", username, len(files))


def receive_save(cmd: dict) -> None:
    """Save vindo da nuvem: entra na cópia do jogador, e o gancho o põe no jogo na próxima partida."""
    user, system, name = cmd.get("username", ""), cmd.get("system", ""), cmd.get("filename", "")
    if not (USERNAME.fullmatch(user) and SAFE_SYSTEM.fullmatch(system) and SAFE_FILENAME.fullmatch(name)):
        log.warning("Save recusado (nome inválido): %r/%r/%r", user, system, name)
        return
    data = base64.b64decode(cmd["data"])
    if hashlib.sha256(data).hexdigest() != cmd.get("sha256"):
        log.warning("Save recusado (hash não confere): %s/%s", system, name)
        return
    dst, modified = SAVE_STORE / user / system / name, cmd["modified_at"]
    if dst.exists() and dst.stat().st_mtime >= modified:
        return  # a cópia do Pi é mais nova: ela é que vai para a nuvem
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / f".{name}.tmp"  # começa com ponto: nunca é confundido com um save
    tmp.write_bytes(data)
    os.utime(tmp, (modified, modified))
    os.replace(tmp, dst)
    with saves_lock:
        synced = read_json(SAVES_SYNCED, {})
        synced[f"{user}/{system}/{name}"] = [cmd["sha256"], float(modified), len(data)]  # não reenviar o que veio de lá
        write_json(SAVES_SYNCED, synced)
    log.info("Save recebido da nuvem: %s/%s/%s", user, system, name)


def upload_saves(client: mqtt.Client) -> None:
    """Envia para a nuvem os saves de jogadores que mudaram (convidado e backup inicial ficam só no Pi)."""
    if not SAVE_STORE.is_dir():
        return
    with saves_lock:
        synced = read_json(SAVES_SYNCED, {})
    pending = []
    for user_dir in sorted(d for d in SAVE_STORE.iterdir() if d.is_dir() and not d.name.startswith("@")):
        for f in player_save_files(user_dir.name):
            key, st = f"{user_dir.name}/{f.parent.name}/{f.name}", f.stat()
            if synced.get(key, [None, None, None])[1:] != [st.st_mtime, st.st_size]:
                pending.append((key, user_dir.name, f, st))
    for key, user, f, st in pending:
        if st.st_size > MAX_SAVE_BYTES:
            log.warning("Save grande demais para a nuvem (%d bytes), fica só no Pi: %s", st.st_size, key)
            sha = None
        else:
            data = f.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if synced.get(key, [None])[0] != sha:  # data mudou mas o conteúdo não: não precisa reenviar
                if not connected.is_set():
                    return
                info = client.publish(f"{DEVICE_TOPIC}/saves", json.dumps({
                    "type": "upload", "username": user, "system": f.parent.name, "filename": f.name, "sha256": sha,
                    "modified_at": int(st.st_mtime), "data": base64.b64encode(data).decode(),
                }), qos=1)
                info.wait_for_publish(timeout=30)  # sem a trava: quem confirma o PUBACK é a thread do MQTT
                if not info.is_published():
                    return  # tenta de novo no próximo ciclo
                log.info("Save enviado para a nuvem: %s", key)
        with saves_lock:
            current = read_json(SAVES_SYNCED, {})
            current[key] = [sha, st.st_mtime, st.st_size]
            write_json(SAVES_SYNCED, current)


def decode(raw: bytes, fmt: str) -> int:
    if fmt == "be_swapped":
        # Mega Drive no RetroArch: a RAM do 68000 aparece com os 2 bytes de cada palavra de 16 bits trocados.
        raw = bytes(b for i in range(0, len(raw) - 1, 2) for b in (raw[i + 1], raw[i]))
        fmt = "be"
    if fmt in ("bcd", "bcd_le"):  # cada nibble é um dígito decimal; bytes fora disso geram ValueError
        return int("".join(f"{b:02x}" for b in (raw if fmt == "bcd" else raw[::-1])))
    return int.from_bytes(raw, "little" if fmt == "le" else "big")


def read_field(sock: socket.socket, spec: dict) -> int | None:
    """Lê um valor da memória do jogo. READ_CORE_RAM usa o mesmo mapa de endereços das conquistas do RA."""
    sock.sendto(f"READ_CORE_RAM {spec['address']} {spec['size']}".encode(), RETROARCH)
    try:
        reply = sock.recv(4096).decode().split()  # "READ_CORE_RAM fe26 00 01 86 a0" ou "... -1"
        raw = bytes(int(b, 16) for b in reply[2:])
        if len(raw) != spec["size"]:
            return None
        value = decode(raw, spec.get("format", "be")) * spec.get("multiplier", 1) + spec.get("add", 0)
        if value > spec.get("max", value):
            return None  # lixo de memória (ex.: RAM ainda não inicializada na tela de título)
        # "map" traduz códigos internos (ex.: ID da zona do Sonic) para a ordem em que aparecem no jogo.
        return spec["map"].get(str(value)) if "map" in spec else value
    except (OSError, ValueError):
        return None


def watch_scores() -> None:
    """Enquanto houver partida de um jogo mapeado, acompanha score e fase e guarda o melhor score da partida."""
    maps = read_json(MEMORY_MAPS, {})
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    while True:
        time.sleep(1)
        try:
            play = read_json(CURRENT_PLAY) or {}
            spec = maps.get(play.get("rom_md5") or "")
            # Só conta com o jogo de verdade rodando: os modos demonstração pontuam sozinhos.
            if spec and "active" in spec and read_field(sock, spec["active"]) not in spec["active"]["in"]:
                continue
            score = read_field(sock, spec["score"]) if spec else None
            if score is None:
                continue
            live = read_json(LIVE_SCORE) or {}
            same_play = live.get("play_uuid") == play["play_uuid"]
            current = {
                "play_uuid": play["play_uuid"],
                "score": score,
                "best_score": max(score, live.get("best_score", 0)) if same_play else score,  # vale o melhor "continue"
                "stage": max(read_field(sock, spec["stage"]) or 0, live.get("stage", 0) if same_play else 0)
                if "stage" in spec else 0,
            }
            if current != live:  # só grava quando muda: poupa o cartão SD
                write_json(LIVE_SCORE, current)
        except Exception:
            log.exception("Erro ao ler o score")


def main() -> None:
    SPOOL.mkdir(parents=True, exist_ok=True)
    # clean_session=False: comandos do servidor enviados com o Pi offline chegam quando ele reconectar.
    # O sufixo evita derrubar (mesmo client_id) quem testar o edge/publisher.py com o mesmo device_id.
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{DEVICE}-daemon", clean_session=False)
    client.username_pw_set(config["MQTT_USER"], config["MQTT_PASS"])
    client.tls_set()
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.on_connect, client.on_disconnect = on_connect, on_disconnect
    client.on_subscribe, client.on_message = on_subscribe, on_message
    client.connect_async(config["MQTT_HOST"], int(config.get("MQTT_PORT", "8883")))
    client.loop_start()  # conecta e reconecta sozinho, inclusive se a rede ainda não subiu no boot
    threading.Thread(target=watch_scores, daemon=True).start()
    threading.Thread(target=watch_clock, daemon=True).start()

    def publish_telemetry(data: dict) -> None:
        if connected.is_set():  # leitura ao vivo: sem conexão ela é descartada, não entra na fila
            client.publish(f"{DEVICE_TOPIC}/telemetry", json.dumps(data), qos=0)

    threading.Thread(target=esp32.run, args=(publish_telemetry,), daemon=True).start()
    log.info("Daemon iniciado como %s", DEVICE)
    tick = 0
    while True:
        try:
            esp32.set_playing(game_running())  # LEDs piscam enquanto houver jogo aberto
            flush(client)
            if tick % 10 == 0:  # saves: a cada 10 s basta
                upload_saves(client)
        except Exception:
            log.exception("Erro ao esvaziar a fila ou enviar saves")
        tick += 1
        time.sleep(1)


if __name__ == "__main__":
    main()
