#!/bin/sh
# Gancho do Batocera (gameStart/gameStop): repassa os argumentos para o hook.py do Arcade Cloud Score.
cd /userdata/system/arcade && exec python3 hook.py "$@"
