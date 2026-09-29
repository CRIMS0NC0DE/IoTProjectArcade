#!/bin/sh
# Gancho de conquistas do Batocera: repassa o desbloqueio para o achievement.py do Arcade Cloud Score.
cd /userdata/system/arcade && exec python3 achievement.py "$@"
