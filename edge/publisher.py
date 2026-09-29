"""Publicador manual de partidas no broker MQTT (TLS, QoS 1), no contrato arcade/scores/submit.

No arcade de verdade quem publica é o cliente do Batocera (edge/batocera/); este script serve para
testar o backend sem o Pi, de qualquer máquina com o .env.

Uso:
    python edge/publisher.py                      # modo interativo
    python edge/publisher.py --tag THI --game "Street Fighter II" --score 142500 --stage 8
"""

import argparse
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MQTT_HOST = os.environ["MQTT_HOST"]
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MQTT_USER = os.environ["MQTT_USER"]
MQTT_PASS = os.environ["MQTT_PASS"]
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "arcade/scores/submit")
TAG_RE = re.compile(r"[A-Z0-9]{1,3}")  # mesma regra do backend


def connect(device_id: str) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=device_id)
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set()

    connected = threading.Event()
    result = {}

    def on_connect(client, userdata, flags, reason_code, properties):
        result["rc"] = reason_code
        connected.set()

    client.on_connect = on_connect
    try:
        client.connect(MQTT_HOST, MQTT_PORT)
    except OSError as exc:  # DNS, rede ou handshake TLS
        sys.exit(f"Erro ao conectar no broker MQTT: {exc}")
    client.loop_start()
    # Credencial errada só aparece no CONNACK, por isso esperamos o on_connect.
    if not connected.wait(timeout=10) or result["rc"].is_failure:
        client.loop_stop()
        sys.exit(f"Broker recusou a conexão: {result.get('rc', 'sem resposta')} (confira o .env)")
    return client


def publish(client: mqtt.Client, device_id: str, tag: str, game: str, score: int, stage: int) -> None:
    payload = {
        "device_id": device_id,
        "player_tag": tag,
        "game": game,
        "score": score,
        "stage_reached": stage,
        "timestamp": int(time.time()),
    }
    info = client.publish(MQTT_TOPIC, json.dumps(payload), qos=1)
    info.wait_for_publish(timeout=10)  # QoS 1: aguarda o PUBACK do broker
    if not info.is_published():
        sys.exit("Broker não confirmou a publicação (PUBACK)")
    print("Enviado ✔")


def normalize_tag(tag: str) -> str:
    return tag.strip().upper()[:3]


def non_negative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("deve ser >= 0")
    return number


def ask(prompt: str, valid) -> str:
    while not valid(value := input(prompt).strip()):
        print("  valor inválido, tente de novo")
    return value


def interactive(client: mqtt.Client, device_id: str) -> None:
    print("Modo interativo — Enter vazio na TAG encerra.")
    while True:
        tag = normalize_tag(input("TAG: "))
        if not tag:
            return
        if not TAG_RE.fullmatch(tag):
            print("  TAG deve ter de 1 a 3 letras/números")
            continue
        game = ask("Jogo: ", bool)
        score = int(ask("Score: ", str.isdecimal))
        stage = int(ask("Fase: ", str.isdecimal))
        publish(client, device_id, tag, game, score, stage)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publica partidas do arcade via MQTT")
    parser.add_argument("--device", default="arcade-pi-01", help="device_id, também usado como client_id MQTT")
    parser.add_argument("--tag")
    parser.add_argument("--game")
    parser.add_argument("--score", type=non_negative)
    parser.add_argument("--stage", type=non_negative)
    args = parser.parse_args()

    single = [args.tag, args.game, args.score, args.stage]
    one_shot = any(v is not None for v in single)
    if one_shot:
        if any(v is None for v in single):
            parser.error("o modo não interativo exige --tag, --game, --score e --stage")
        args.tag, args.game = normalize_tag(args.tag), args.game.strip()
        if not TAG_RE.fullmatch(args.tag):
            parser.error("--tag deve ter de 1 a 3 letras/números")
        if not args.game:
            parser.error("--game não pode ser vazio")

    client = connect(args.device)
    try:
        if one_shot:
            publish(client, args.device, args.tag, args.game, args.score, args.stage)
        else:
            interactive(client, args.device)
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
