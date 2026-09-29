"""Contas de jogador: cadastro, login e sessão por cookie."""

import hashlib
import hmac
import logging
import secrets
import threading
import time
from datetime import timedelta
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from psycopg import errors
from pydantic import BaseModel, Field, field_validator

import db

COOKIE = "arcade_session"
SESSION_TTL = timedelta(days=30)
# Parâmetros do scrypt recomendados pela OWASP (~32 MiB e ~200 ms por hash: caro para força bruta).
SCRYPT = {"n": 2**15, "r": 8, "p": 3}
SCRYPT_MAXMEM = 64 * 1024 * 1024

router = APIRouter(prefix="/api")
log = logging.getLogger("backend")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, maxmem=SCRYPT_MAXMEM, **SCRYPT)
    # Os parâmetros vão junto no hash: dá para endurecê-los depois sem invalidar as senhas antigas.
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    _, n, r, p, salt, digest = stored.split("$")
    candidate = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), maxmem=SCRYPT_MAXMEM
    )
    return hmac.compare_digest(candidate, bytes.fromhex(digest))


# Login de usuário inexistente também paga um scrypt: pelo tempo de resposta não dá para descobrir quem tem conta.
DUMMY_HASH = hash_password(secrets.token_hex())


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def start_session(request: Request, response: Response, user_id: int) -> None:
    token = secrets.token_urlsafe(32)
    db.create_token(user_id, token_hash(token), SESSION_TTL)
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,  # o JavaScript da página não lê o token: um XSS não rouba a sessão
        samesite="lax",  # outro site não consegue disparar POST autenticado (CSRF)
        secure=request.url.scheme == "https",
    )


def current_user(arcade_session: Annotated[str | None, Cookie()] = None) -> dict:
    user = db.user_by_token(token_hash(arcade_session)) if arcade_session else None
    if user is None:
        raise HTTPException(401, "Faça login para continuar")
    return user


class Credentials(BaseModel):
    username: str
    password: str = Field(max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class Registration(Credentials):
    username: str = Field(pattern=r"^[a-z0-9_]{3,20}$")
    player_tag: str = Field(pattern=r"^[A-Z0-9]{1,3}$")
    password: str = Field(min_length=8, max_length=128)

    @field_validator("player_tag", mode="before")
    @classmethod
    def normalize_tag(cls, value):
        return value.strip().upper() if isinstance(value, str) else value


def create_account(username: str, player_tag: str, password_hash: str) -> dict:
    """Cria a conta (pelo site ou pelo arcade). ValueError com a mensagem para o jogador se já existir."""
    try:
        user = db.create_user(username, player_tag, password_hash)
    except errors.UniqueViolation as exc:
        field = "player_tag" if "player_tag" in exc.diag.constraint_name else "username"
        log.info("Cadastro recusado (%s já existe): %s / %s", field, username, player_tag)
        raise ValueError("Essa tag já pertence a outro jogador" if field == "player_tag" else "Esse nome de usuário já existe")
    log.info("Conta criada: %s (%s)", user["username"], user["player_tag"])
    return user


@router.post("/auth/register", status_code=201)
def register(body: Registration, request: Request, response: Response):
    try:
        user = create_account(body.username, body.player_tag, hash_password(body.password))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    start_session(request, response, user["id"])
    return {"username": user["username"], "player_tag": user["player_tag"]}


@router.post("/auth/login")
def login(body: Credentials, request: Request, response: Response):
    user = db.user_by_username(body.username)
    valid = verify_password(body.password, user["password_hash"] if user else DUMMY_HASH)
    if not (user and valid):
        log.info("Login recusado: %s (%s)", body.username, "senha errada" if user else "usuário não existe")
        raise HTTPException(401, "Usuário ou senha incorretos")
    start_session(request, response, user["id"])
    return {"username": user["username"], "player_tag": user["player_tag"]}


@router.post("/auth/logout", status_code=204)
def logout(response: Response, arcade_session: Annotated[str | None, Cookie()] = None):
    if arcade_session:
        db.delete_token(token_hash(arcade_session))
    response.delete_cookie(COOKIE)


@router.get("/me")
def me(user: Annotated[dict, Depends(current_user)]):
    return {"username": user["username"], "player_tag": user["player_tag"]}


@router.get("/me/profile")
def my_profile(user: Annotated[dict, Depends(current_user)]):
    return db.profile(user["id"])


@router.get("/me/achievements")
def my_achievements(user: Annotated[dict, Depends(current_user)]):
    return db.user_achievements(user["id"])


@router.get("/me/saves")
def my_saves(user: Annotated[dict, Depends(current_user)]):
    return db.user_saves(user["id"])


@router.get("/me/saves/{save_id}")
def download_save(save_id: int, user: Annotated[dict, Depends(current_user)]):
    save = db.user_save_file(user["id"], save_id)  # só os saves do próprio jogador
    if save is None:
        raise HTTPException(404, "Save não encontrado")
    return Response(
        save["data"],
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(save['filename'])}"},
    )


# ---------- login no arcade: desafio-resposta via MQTT (a senha nunca sai do Pi) ----------
#
# 1. O Pi pede um desafio para o usuário; o servidor responde com os parâmetros do scrypt da conta (salt, N, r, p)
#    e um nonce de uso único.
# 2. O Pi calcula scrypt(senha, salt) localmente e responde HMAC(esse resultado, nonce:aparelho:pedido).
# 3. O servidor refaz o HMAC com o hash guardado no banco. Quem escuta o broker não vê a senha, e a prova não
#    serve para outro login (nonce de uso único, preso ao aparelho).

CHALLENGE_TTL = 60  # segundos para o Pi responder o desafio
# Pattern do hash que o Pi manda no cadastro: só aceita os mesmos parâmetros do servidor (nada de scrypt fraco).
DEVICE_HASH_PATTERN = rf"^scrypt\${SCRYPT['n']}\${SCRYPT['r']}\${SCRYPT['p']}\$[0-9a-f]{{32}}\$[0-9a-f]{{128}}$"

_challenges: dict[str, tuple[str, str, float]] = {}  # nonce -> (usuário, aparelho, expira em)
_challenges_lock = threading.Lock()
_fake_salt_key = secrets.token_bytes(32)


def device_proof(key: bytes, nonce: str, device_id: str, request_id: str) -> str:
    return hmac.new(key, f"{nonce}:{device_id}:{request_id}".encode(), hashlib.sha256).hexdigest()


def new_challenge(username: str, device_id: str) -> dict:
    """Parâmetros do scrypt da conta e um nonce de uso único.

    Usuário inexistente recebe um salt falso, porém estável: a resposta não revela quem tem conta.
    """
    user = db.user_by_username(username)
    if user:
        _, n, r, p, salt, _ = user["password_hash"].split("$")
    else:
        n, r, p = SCRYPT["n"], SCRYPT["r"], SCRYPT["p"]
        salt = hmac.new(_fake_salt_key, username.encode(), hashlib.sha256).hexdigest()[:32]
    nonce = secrets.token_hex(16)
    now = time.monotonic()
    with _challenges_lock:
        for old in [k for k, v in _challenges.items() if v[2] < now]:
            del _challenges[old]
        _challenges[nonce] = (username, device_id, now + CHALLENGE_TTL)
    return {"salt": salt, "n": int(n), "r": int(r), "p": int(p), "nonce": nonce}


def verify_device_login(username: str, nonce: str, proof: str, device_id: str, request_id: str) -> dict | None:
    with _challenges_lock:
        challenge = _challenges.pop(nonce, None)  # uso único, certo ou errado
    if challenge is None or challenge[2] < time.monotonic() or challenge[:2] != (username, device_id):
        return None
    user = db.user_by_username(username)
    if user is None:
        return None
    digest = bytes.fromhex(user["password_hash"].split("$")[5])
    return user if hmac.compare_digest(device_proof(digest, nonce, device_id, request_id), proof) else None
