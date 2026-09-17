#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
case "${1:-doctor}" in
  doctor)
    command -v python3 >/dev/null || { echo 'Python 3 is required.'; exit 2; }
    python3 -c 'import sys; print(sys.version); assert sys.version_info >= (3,10)'
    command -v docker >/dev/null || {
      echo 'Docker is missing in this shell. Enable Docker Desktop WSL integration or use a Docker-equipped host.'
      echo 'No files in your existing projects have been changed.'; exit 2;
    }
    docker info >/dev/null
    docker compose version
    docker compose config --quiet
    echo 'Preflight OK. Dedicated lab ports: broker 18883, Home Assistant 18123 (loopback only).'
    ;;
  up)
    bash "$0" doctor
    docker compose pull
    docker compose up -d
    echo 'Open http://127.0.0.1:18123 and perform the dedicated lab onboarding in README.md.'
    ;;
  check)
    docker compose exec -T homeassistant python -m homeassistant --script check_config --config /config
    ;;
  logs)
    docker compose logs --no-color --tail=120 broker homeassistant
    ;;
  test)
    .venv/bin/python -m unittest discover -s tests -v
    ;;
  run)
    shift
    : "${HA_TOKEN:?Set HA_TOKEN from the dedicated lab instance. Never share it.}"
    .venv/bin/python run.py "$@"
    ;;
  stop)
    docker compose stop
    echo 'Stopped the dedicated lab. Named volumes and evidence are preserved.'
    ;;
  *)
    echo 'Usage: bash lab.sh {doctor|up|check|logs|test|run [--repetitions N]|stop}'
    exit 2
    ;;
esac
