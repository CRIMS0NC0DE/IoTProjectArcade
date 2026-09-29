#!/usr/bin/env bash
# Instala (ou atualiza) o cliente do Arcade Cloud Score num Batocera, via SSH.
# Uso, na raiz do projeto: edge/batocera/install.sh [ip-do-pi] [device_id]
set -euo pipefail

PI_HOST=${1:-192.168.1.94}
DEVICE=${2:-arcade-pi-01}
KEY=${PI_KEY:-$HOME/.ssh/arcade_pi}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
PI="ssh -i $KEY -o BatchMode=yes -o IdentitiesOnly=yes -o LogLevel=ERROR root@$PI_HOST"
APP=/userdata/system/arcade
PAHO_DIR=$("$ROOT/.venv/bin/python" -c "import paho, os; print(os.path.dirname(os.path.dirname(paho.__file__)))")

echo "==> Pastas"
ACH_HOOKS=/userdata/system/configs/emulationstation/scripts/achievements
$PI "mkdir -p $APP/lib $APP/spool $APP/state /userdata/system/scripts /userdata/system/services /userdata/roms/pygame/jogadores $ACH_HOOKS"

echo "==> Código e paho-mqtt (Python puro, o Batocera não tem pip)"
tar -C "$HERE" -cf - common.py hook.py saves.py achievement.py arcaded.py memory_maps.json | $PI "tar -C $APP -xf -"
tar -C "$PAHO_DIR" --exclude=__pycache__ -cf - paho | $PI "rm -rf $APP/lib/paho && tar -C $APP/lib -xf -"
$PI "cat > /userdata/system/scripts/arcade-score.sh" < "$HERE/arcade-score.sh"
$PI "cat > $ACH_HOOKS/arcade-score.sh" < "$HERE/arcade-achievement.sh"
$PI "cat > /userdata/system/services/arcade_score" < "$HERE/arcade_score"
$PI "cat > /userdata/roms/pygame/jogadores/jogadores.pygame" < "$HERE/jogadores.pygame"
$PI "chmod +x /userdata/system/scripts/arcade-score.sh $ACH_HOOKS/arcade-score.sh /userdata/system/services/arcade_score"

echo "==> Configuração (só as chaves MQTT do Pi; nada é exibido)"
# Mesmo parser do backend (python-dotenv): entende aspas e comentários no fim da linha do .env.
"$ROOT/.venv/bin/python" - "$ROOT/.env" "$DEVICE" <<'PY' | $PI "umask 077 && cat > $APP/arcade.conf"
import sys
from dotenv import dotenv_values
env = dotenv_values(sys.argv[1])
for key in ("MQTT_HOST", "MQTT_PORT", "MQTT_USER", "MQTT_PASS"):
    print(f"{key}={env[key]}")
print(f"DEVICE_ID={sys.argv[2]}")
PY

echo "==> Entrada \"Login do Arcade\" no sistema PYGAME"
$PI "python3 -" <<'PY'
import xml.etree.ElementTree as ET
path = "/userdata/roms/pygame/gamelist.xml"
tree = ET.parse(path)
root = tree.getroot()
game = next((g for g in root.iter("game") if g.findtext("path") == "./jogadores/jogadores.pygame"), None)
if game is None:
    game = ET.SubElement(root, "game")
for tag, text in [("path", "./jogadores/jogadores.pygame"), ("name", "Login do Arcade"),
                  ("desc", "Entre com usuário e senha (ou crie sua conta) para suas partidas, conquistas e saves irem para o seu perfil."),
                  ("developer", "Arcade Cloud Score"), ("genre", "Login")]:
    (game.find(tag) if game.find(tag) is not None else ET.SubElement(game, tag)).text = text
ET.indent(root)
tree.write(path, encoding="utf-8", xml_declaration=True)
PY
$PI "curl -s -m 5 http://localhost:1234/reloadgames >/dev/null || true"
# Com o EmulationStation aberto, ele regrava o gamelist.xml com o que tem em memória: o nome vai pela API dele.
$PI "python3 -" <<'PY'
import json, time, urllib.request
ES = "http://localhost:1234"
time.sleep(2)
try:
    games = json.load(urllib.request.urlopen(f"{ES}/systems/pygame/games", timeout=5))
    game = next(g for g in games if g.get("path", "").endswith("jogadores/jogadores.pygame"))
    body = json.dumps({"name": "Login do Arcade", "desc": "Entre com usuário e senha (ou crie sua conta) para suas "
                       "partidas, conquistas e saves irem para o seu perfil."}).encode()
    urllib.request.urlopen(urllib.request.Request(f"{ES}/systems/pygame/games/{game['id']}", data=body, method="POST",
                                                  headers={"Content-Type": "application/json"}), timeout=5)
except Exception as exc:
    print(f"(nome no menu não atualizado pela API do ES: {exc})")
PY

echo "==> Batocera: comandos de rede do RetroArch (leitura de score) e modo encore (conquistas por perfil)"
$PI "batocera-settings-set global.retroarch.network_cmd_enable true && batocera-settings-set global.retroarch.network_cmd_port 55355 && batocera-settings-set global.retroachievements.encore 1"

echo "==> Serviço"
$PI "batocera-services enable arcade_score >/dev/null; batocera-services stop arcade_score; batocera-services start arcade_score"
sleep 3
$PI "echo \"serviço: \$(bash /userdata/system/services/arcade_score status)\"; tail -n 5 $APP/arcaded.log"
