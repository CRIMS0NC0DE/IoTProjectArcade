"""Backend: recebe partidas e logins dos arcades via MQTT, grava no PostgreSQL e empurra o ranking via WebSocket."""

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

import auth
import db
import ra

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
load_dotenv(BASE_DIR.parent / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]
MQTT_HOST = os.environ["MQTT_HOST"]
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
# Credencial própria do backend; sem ela, usa a mesma do publisher.
MQTT_USER = os.getenv("MQTT_BACKEND_USER") or os.environ["MQTT_USER"]
MQTT_PASS = os.getenv("MQTT_BACKEND_PASS") or os.environ["MQTT_PASS"]
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "arcade/scores/submit")
# Por aparelho: arcade/devices/<device_id>/{login,events} (Pi → backend) e .../commands (backend → Pi).
SUBSCRIPTIONS = [
    MQTT_TOPIC, "arcade/devices/+/login", "arcade/devices/+/events", "arcade/devices/+/achievements",
    "arcade/devices/+/saves",
]
MAX_SAVE_BYTES = 3 * 1024 * 1024  # o HiveMQ entrega mensagens de até ~4 MB, e o base64 aumenta 1/3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backend")

Epoch = Annotated[int, Field(ge=0, lt=10**10)]  # epoch em segundos; barra timestamp em ms por engano
Md5 = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Username = Annotated[str, Field(pattern=r"^[a-z0-9_]{3,20}$")]

# Chamadas à API do RetroAchievements podem levar segundos: rodam numa fila própria, fora da thread do MQTT.
ra_tasks = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ra")


class Message(BaseModel):
    # strict: "142500" (string) ou 142500.0 não passam como int
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)


class Score(Message):
    device_id: str = Field(min_length=1)
    player_tag: str = Field(pattern=r"^[A-Z0-9]{1,3}$")
    game: str = Field(min_length=1)
    score: int = Field(ge=0)
    stage_reached: int = Field(ge=0)
    timestamp: Epoch


class AuthMessage(Message):
    request_id: str = Field(min_length=1, max_length=64)
    ts: Epoch


class ChallengeRequest(AuthMessage):
    action: Literal["challenge"]
    username: str = Field(max_length=64)  # formato livre: usuário inexistente também recebe um desafio


class LoginProof(AuthMessage):
    action: Literal["login"]
    username: str = Field(max_length=64)
    nonce: str = Field(pattern=r"^[0-9a-f]{32}$")
    proof: Sha256  # HMAC(scrypt(senha), nonce:aparelho:pedido): a senha não sai do Pi


class RegisterRequest(AuthMessage):
    action: Literal["register"]
    username: Username
    player_tag: str = Field(pattern=r"^[A-Z0-9]{1,3}$")
    password_hash: str = Field(pattern=auth.DEVICE_HASH_PATTERN)  # o Pi gera o scrypt com os parâmetros do servidor


class LogoutRequest(AuthMessage):
    action: Literal["logout"]


LoginMessage = TypeAdapter(
    Annotated[ChallengeRequest | LoginProof | RegisterRequest | LogoutRequest, Field(discriminator="action")]
)


class PlayEvent(Message):
    play_uuid: uuid.UUID  # gerado no Pi: chave de idempotência da partida
    event: Literal["start", "stop"]
    system: str = Field(min_length=1, max_length=64)
    rom: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    username: str | None = None  # quem estava logado no Pi naquele momento; None = convidado
    rom_md5: Md5 | None = None  # hash da ROM no formato do RetroAchievements
    started_at: Epoch
    ended_at: Epoch | None = None
    score: int | None = Field(default=None, ge=0)  # só no "stop" de jogos com mapa de memória
    stage_reached: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def stop_has_end(self):
        if self.event == "stop" and (self.ended_at is None or self.ended_at < self.started_at):
            raise ValueError("stop precisa de ended_at >= started_at")
        return self


class AchievementUnlock(Message):
    achievement_id: int | None = Field(default=None, ge=1)
    title: str = Field(max_length=255)
    description: str = Field(default="", max_length=1000)
    play_uuid: uuid.UUID | None = None
    rom_md5: Md5 | None = None
    username: str | None = None  # None = convidado
    unlocked_at: Epoch


class SaveInfo(Message):
    sha256: Sha256
    modified_at: Epoch


class SaveUpload(Message):
    type: Literal["upload"]
    username: Username
    system: str = Field(pattern=r"^[a-z0-9_]{1,32}$")
    filename: str = Field(max_length=255, pattern=r"^[^/\\.][^/\\]*$")  # nada de caminho: só o nome do arquivo
    sha256: Sha256
    modified_at: Epoch
    data: str = Field(max_length=MAX_SAVE_BYTES * 4 // 3 + 4)  # base64


class SaveManifest(Message):
    type: Literal["manifest"]
    username: Username
    files: dict[str, SaveInfo]  # "<sistema>/<arquivo>" -> versão que o Pi tem


SaveMessage = TypeAdapter(Annotated[SaveUpload | SaveManifest, Field(discriminator="type")])

clients: set[WebSocket] = set()


async def broadcast() -> None:
    board = await asyncio.to_thread(db.leaderboard)  # consulta remota: fora do event loop
    for ws in list(clients):
        try:
            await ws.send_json(board)
        except Exception:
            clients.discard(ws)  # cliente caiu sem fechar o socket


def describe(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'payload'}: {e['msg']}" for e in exc.errors())


def handle_score(payload: bytes, loop: asyncio.AbstractEventLoop) -> None:
    score = Score.model_validate_json(payload)
    if db.insert_manual_score(score.model_dump()):
        log.info("Score gravado: %s | %s | %d", score.player_tag, score.game, score.score)
        asyncio.run_coroutine_threadsafe(broadcast(), loop)
    else:
        log.info("Duplicado ignorado: %s | %s | ts=%d", score.device_id, score.player_tag, score.timestamp)


def handle_login(client: mqtt.Client, device_id: str, payload: bytes) -> None:
    req = LoginMessage.validate_json(payload)
    reply = {"request_id": req.request_id}
    if isinstance(req, ChallengeRequest):
        reply |= {"type": "challenge", **auth.new_challenge(req.username, device_id)}
    elif isinstance(req, LoginProof):
        if user := auth.verify_device_login(req.username, req.nonce, req.proof, device_id, req.request_id):
            db.device_login(device_id, user["username"], req.ts)
            log.info("Login em %s: %s (%s)", device_id, user["username"], user["player_tag"])
            reply |= {"type": "login_ok", "username": user["username"], "player_tag": user["player_tag"]}
        else:
            log.info("Login recusado em %s: %r (senha errada ou desafio inválido)", device_id, req.username)
            reply |= {"type": "login_error", "message": "Usuário ou senha incorretos"}
    elif isinstance(req, RegisterRequest):
        try:
            user = auth.create_account(req.username, req.player_tag, req.password_hash)
        except ValueError as exc:
            reply |= {"type": "register_error", "message": str(exc)}
        else:
            db.device_login(device_id, user["username"], req.ts)  # quem cria a conta já entra jogando
            log.info("Conta criada e logada em %s: %s (%s)", device_id, user["username"], user["player_tag"])
            reply |= {"type": "register_ok", "username": user["username"], "player_tag": user["player_tag"]}
    else:
        db.device_logout(device_id, req.ts)
        log.info("Logout em %s", device_id)
        reply["type"] = "logout_ok"
    client.publish(f"arcade/devices/{device_id}/commands", json.dumps(reply), qos=1)


def run_ra(fn, *args) -> None:
    def task():
        try:
            fn(*args)
        except Exception:
            log.exception("Erro ao consultar o RetroAchievements")

    ra_tasks.submit(task)


def handle_event(device_id: str, payload: bytes, loop: asyncio.AbstractEventLoop) -> None:
    e = PlayEvent.model_validate_json(payload)
    new_score = db.record_play(device_id, e.model_dump())
    who = e.username or "convidado"
    if e.event == "start":
        log.info("Partida iniciada em %s: %s | %s | %s", device_id, who, e.system, e.title)
        if e.rom_md5:
            run_ra(ra.link_game, e.system, e.rom, e.rom_md5)
    else:
        score = f" | score {e.score}" if e.score is not None else ""
        log.info("Partida encerrada em %s: %s | %s | %ds%s", device_id, who, e.title, e.ended_at - e.started_at, score)
    if new_score:
        asyncio.run_coroutine_threadsafe(broadcast(), loop)


def handle_achievement(device_id: str, payload: bytes) -> None:
    e = AchievementUnlock.model_validate_json(payload)
    if e.achievement_id is None:
        log.warning("Conquista sem ID em %s (formato inesperado do Batocera?): %r", device_id, e.title)
    elif e.username is None:
        log.info("Conquista de convidado em %s (não vai para perfil): %s", device_id, e.title)
    elif db.record_unlock(e.model_dump()):
        log.info("Conquista desbloqueada em %s: %s | %s", device_id, e.username, e.title)
        run_ra(ra.sync_achievement_game, e.achievement_id, e.play_uuid)
    else:
        log.info("Conquista repetida (encore) em %s: %s | %s", device_id, e.username, e.title)


def handle_save(client: mqtt.Client, device_id: str, payload: bytes) -> None:
    msg = SaveMessage.validate_json(payload)
    where = f"{msg.username} em {device_id}"
    if isinstance(msg, SaveUpload):
        try:
            data = base64.b64decode(msg.data, validate=True)
        except binascii.Error:
            log.warning("Save recusado (base64 inválido): %s | %s", where, msg.filename)
            return
        if hashlib.sha256(data).hexdigest() != msg.sha256:
            log.warning("Save recusado (hash não confere): %s | %s", where, msg.filename)
        elif db.upsert_save(device_id, msg.model_dump(exclude={"type", "data"}), data):
            log.info("Save guardado na nuvem: %s | %s/%s (%d bytes)", where, msg.system, msg.filename, len(data))
        return
    # Lista do Pi (no login): manda de volta o que a nuvem tem de mais novo ou que falta no aparelho.
    sent = 0
    for save in db.save_index(msg.username):
        local = msg.files.get(f"{save['system']}/{save['filename']}")
        if local and (local.sha256 == save["sha256"] or local.modified_at >= save["modified_at"]):
            continue
        client.publish(f"arcade/devices/{device_id}/commands", json.dumps({
            "type": "save_file", "username": msg.username, "system": save["system"], "filename": save["filename"],
            "sha256": save["sha256"], "modified_at": save["modified_at"],
            "data": base64.b64encode(db.save_data(save["id"])).decode(),
        }), qos=1)
        sent += 1
    log.info("Saves sincronizados: %s | %d arquivo(s) enviados ao aparelho", where, sent)


def build_mqtt_client(loop: asyncio.AbstractEventLoop) -> mqtt.Client:
    # client_id fixo + clean_session=False: o broker guarda as mensagens QoS 1 enquanto o backend estiver fora.
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="arcade-backend", clean_session=False)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set()

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT recusou a conexão: %s", reason_code)
            return
        log.info("MQTT conectado: %s", reason_code)
        client.subscribe([(topic, 1) for topic in SUBSCRIPTIONS])  # aqui dentro para reinscrever a cada reconexão

    def on_subscribe(client, userdata, mid, reason_code_list, properties):
        # Credencial sem permissão de subscribe conecta normalmente; a recusa só aparece no SUBACK.
        for topic, rc in zip(SUBSCRIPTIONS, reason_code_list):
            if rc.is_failure:
                log.error("Inscrição em %s recusada: %s (a credencial precisa de permissão de subscribe)", topic, rc)
            else:
                log.info("Inscrito em %s (QoS %d)", topic, rc.value)

    def on_message(client, userdata, msg):
        # Roda na thread do paho: uma exceção que escape daqui mata o loop MQTT.
        try:
            match msg.topic.split("/"):
                case _ if msg.topic == MQTT_TOPIC:
                    handle_score(msg.payload, loop)
                case ["arcade", "devices", device_id, "login"]:
                    handle_login(client, device_id, msg.payload)
                case ["arcade", "devices", device_id, "events"]:
                    handle_event(device_id, msg.payload, loop)
                case ["arcade", "devices", device_id, "achievements"]:
                    handle_achievement(device_id, msg.payload)
                case ["arcade", "devices", device_id, "saves"]:
                    handle_save(client, device_id, msg.payload)
        except ValidationError as exc:
            log.warning("Payload rejeitado em %s: %s", msg.topic, describe(exc))
        except Exception:
            log.exception("Erro ao processar mensagem MQTT em %s", msg.topic)

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    return client


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect(DATABASE_URL)  # falha já na subida se o banco estiver inacessível
    db.migrate()
    client = build_mqtt_client(asyncio.get_running_loop())
    client.connect_async(MQTT_HOST, MQTT_PORT)  # não bloqueia: o loop do paho conecta e reconecta sozinho
    client.loop_start()
    try:
        yield
    finally:
        client.disconnect()
        client.loop_stop()
        ra_tasks.shutdown(wait=False, cancel_futures=True)
        db.close()


app = FastAPI(title="Arcade Cloud Score", lifespan=lifespan)
app.include_router(auth.router)


@app.get("/api/leaderboard")
def get_leaderboard(game: str | None = None, limit: int = Query(10, ge=1, le=100)):
    return db.leaderboard(game, limit)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        await ws.send_json(await asyncio.to_thread(db.leaderboard))
        while True:
            await ws.receive_text()  # só para perceber a desconexão
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


# Montado por último: em "/" ele engoliria qualquer rota registrada depois dele.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
