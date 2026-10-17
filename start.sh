#!/usr/bin/env bash
# Entra na pasta do laboratório, mesmo se o comando partir de outro diretório.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# -B evita arquivos __pycache__; "$@" encaminha opções como --port 8766.
exec python3 -B server.py "$@"
