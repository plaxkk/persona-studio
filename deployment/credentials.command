#!/bin/zsh
set -e
cd "${0:A:h:h}"
.venv/bin/python deployment/studio_credentials.py
read 'reply?按回车关闭。'
