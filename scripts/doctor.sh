#!/usr/bin/env bash
set -u

failures=0

check_command() {
  if "$@"; then
    printf '[OK] %s\n' "$*"
  else
    printf '[FAIL] %s\n' "$*" >&2
    failures=$((failures + 1))
  fi
}

check_command test -r /etc/bite-hunt/config.json
check_command test -x /opt/bite-hunt-careers/venv/bin/uvicorn
check_command systemctl is-active --quiet bite-hunt-careers.service
check_command systemctl is-active --quiet nginx
check_command nginx -t
check_command curl --fail --silent --show-error --max-time 4 http://127.0.0.1:8000/health

if [[ "${failures}" -gt 0 ]]; then
  printf '%s check(s) failed.\n' "${failures}" >&2
  exit 1
fi
printf 'All deployment checks passed.\n'
