#!/usr/bin/env bash
set -u

failures=0
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

check_command() {
  if "$@"; then
    printf '[OK] %s\n' "$*"
  else
    printf '[FAIL] %s\n' "$*" >&2
    failures=$((failures + 1))
  fi
}

check_json() {
  python3 -m json.tool "$1" >/dev/null
}

check_service_imports() {
  runuser -u bitehunt -- \
    /opt/bite-hunt-careers/venv/bin/python \
    -c 'import fastapi, jinja2, uvicorn' >/dev/null
}

check_command test -r /etc/bite-hunt/config.json
check_command check_json /etc/bite-hunt/config.json
check_command test -r /etc/bite-hunt/deepseek.json
check_command check_json /etc/bite-hunt/deepseek.json
check_command test -L /opt/bite-hunt-careers/current
check_command runuser -u bitehunt -- test -r /etc/bite-hunt/config.json
check_command runuser -u bitehunt -- test -x /opt/bite-hunt-careers/venv/bin/uvicorn
check_command check_service_imports
check_command systemctl is-active --quiet bite-hunt-careers.service
check_command systemctl is-active --quiet nginx
check_command nginx -t
check_command curl --fail --silent --show-error --max-time 4 http://127.0.0.1:8000/health
check_command bash "${script_dir}/smoke_test.sh" http://127.0.0.1:8000 127.0.0.1 "Uvicorn backend"
check_command bash "${script_dir}/smoke_test.sh" http://127.0.0.1 127.0.0.1 "Nginx public route"

if [[ "${failures}" -gt 0 ]]; then
  printf '%s check(s) failed.\n' "${failures}" >&2
  printf '\nRecent application log:\n' >&2
  journalctl -u bite-hunt-careers.service -n 60 --no-pager >&2 || true
  exit 1
fi
printf 'All deployment checks passed.\n'
