#!/usr/bin/env bash
set -Eeuo pipefail

umask 027

if [[ "${EUID}" -ne 0 ]]; then
  printf 'Run this installer as root: sudo bash scripts/install_ubuntu.sh\n' >&2
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  printf 'Unable to identify this operating system. Ubuntu 22.04 or newer is required.\n' >&2
  exit 1
fi

# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]] || ! dpkg --compare-versions "${VERSION_ID:-0}" ge "22.04"; then
  printf 'Ubuntu 22.04 or newer is required. Detected: %s %s\n' "${ID:-unknown}" "${VERSION_ID:-unknown}" >&2
  exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source_root="$(cd -- "${script_dir}/.." && pwd)"
install_root="/opt/bite-hunt-careers"
config_root="/etc/bite-hunt"
config_file="${config_root}/config.json"
ai_config_file="${config_root}/deepseek.json"
env_file="${config_root}/bite-hunt.env"
data_root="/var/lib/bite-hunt"
service_name="bite-hunt-careers.service"
server_name="${BITEHUNT_SERVER_NAME:-_}"
base_url="${BITEHUNT_BASE_URL:-http://localhost}"

if [[ ! "${server_name}" =~ ^[_A-Za-z0-9.-]+$ ]]; then
  printf 'BITEHUNT_SERVER_NAME must be one hostname, IP address, or _.\n' >&2
  exit 1
fi

admin_username="${BITEHUNT_ADMIN_USERNAME:-}"
admin_password="${BITEHUNT_ADMIN_PASSWORD:-}"
deepseek_api_key="${BITEHUNT_DEEPSEEK_API_KEY:-}"
if [[ ! -f "${config_file}" || "${BITEHUNT_RECONFIGURE:-0}" == "1" ]]; then
  if [[ -z "${admin_username}" ]]; then
    if [[ -t 0 ]]; then
      read -r -p 'Administrator username [admin]: ' admin_username
      admin_username="${admin_username:-admin}"
    else
      admin_username="admin"
    fi
  fi
  if [[ -z "${admin_password}" ]]; then
    if [[ ! -t 0 ]]; then
      printf 'Set BITEHUNT_ADMIN_PASSWORD for a non-interactive installation.\n' >&2
      exit 1
    fi
    read -r -s -p 'Administrator password (12+ characters): ' admin_password
    printf '\n'
    read -r -s -p 'Confirm administrator password: ' admin_password_confirmation
    printf '\n'
    if [[ "${admin_password}" != "${admin_password_confirmation}" ]]; then
      printf 'Passwords do not match.\n' >&2
      exit 1
    fi
  fi
  if (( ${#admin_password} < 12 )); then
    printf 'Administrator password must contain at least 12 characters.\n' >&2
    exit 1
  fi
fi

if [[ ! -f "${ai_config_file}" || "${BITEHUNT_RECONFIGURE_AI:-0}" == "1" ]]; then
  if [[ -z "${deepseek_api_key}" && -t 0 ]]; then
    read -r -s -p 'DeepSeek API key (leave blank to configure later): ' deepseek_api_key
    printf '\n'
  fi
fi

printf 'Installing operating-system packages…\n'
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl nginx python3 python3-pip python3-venv sqlite3

if ! id -u bitehunt >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "${data_root}" --shell /usr/sbin/nologin bitehunt
fi

install -d -o root -g root -m 0755 "${install_root}" "${install_root}/releases"
install -d -o root -g bitehunt -m 0750 "${config_root}"
install -d -o bitehunt -g bitehunt -m 0750 "${data_root}" "${data_root}/data" "${data_root}/uploads"

release_id="$(date -u +%Y%m%d%H%M%S)-$$"
release_dir="${install_root}/releases/${release_id}"
install -d -o root -g root -m 0755 "${release_dir}"
cp -a "${source_root}/app" "${source_root}/config" "${source_root}/deploy" "${source_root}/scripts" "${release_dir}/"
install -o root -g root -m 0644 "${source_root}/requirements.txt" "${release_dir}/requirements.txt"

if [[ ! -d "${install_root}/venv" ]]; then
  python3 -m venv "${install_root}/venv"
fi
"${install_root}/venv/bin/python" -m pip install --upgrade pip wheel
"${install_root}/venv/bin/python" -m pip install -r "${release_dir}/requirements.txt"
"${install_root}/venv/bin/python" -m compileall -q "${release_dir}/app"

if [[ ! -f "${config_file}" || "${BITEHUNT_RECONFIGURE:-0}" == "1" ]]; then
  if [[ -f "${config_file}" ]]; then
    install -o root -g bitehunt -m 0640 "${config_file}" "${config_file}.backup-${release_id}"
  fi
  trusted_host="*"
  if [[ "${server_name}" != "_" ]]; then
    trusted_host="${server_name}"
  fi
  printf '%s\n' "${admin_password}" | \
    /usr/bin/python3 "${release_dir}/scripts/render_config.py" \
      --output "${config_file}" \
      --data-dir "${data_root}" \
      --admin-username "${admin_username}" \
      --base-url "${base_url}" \
      --trusted-host "${trusted_host}" \
      --password-stdin \
      --force
  chown root:bitehunt "${config_file}"
  chmod 0640 "${config_file}"
fi
unset admin_password

if [[ ! -f "${ai_config_file}" || "${BITEHUNT_RECONFIGURE_AI:-0}" == "1" ]]; then
  if [[ -f "${ai_config_file}" ]]; then
    install -o root -g bitehunt -m 0640 "${ai_config_file}" "${ai_config_file}.backup-${release_id}"
  fi
  printf '%s\n' "${deepseek_api_key}" | \
    /usr/bin/python3 "${release_dir}/scripts/render_deepseek_config.py" \
      --output "${ai_config_file}" \
      --api-key-stdin \
      --force
  chown root:bitehunt "${ai_config_file}"
  chmod 0640 "${ai_config_file}"
fi
unset deepseek_api_key

if [[ ! -f "${env_file}" ]]; then
  install -o root -g bitehunt -m 0640 "${release_dir}/config/bite-hunt.env.example" "${env_file}"
fi

ln -sfn "${release_dir}" "${install_root}/current.next"
mv -Tf "${install_root}/current.next" "${install_root}/current"

sed \
  -e "s|@@INSTALL_ROOT@@|${install_root}|g" \
  -e "s|@@CONFIG_FILE@@|${config_file}|g" \
  -e "s|@@ENV_FILE@@|${env_file}|g" \
  -e "s|@@DATA_ROOT@@|${data_root}|g" \
  "${release_dir}/deploy/bite-hunt-careers.service" \
  > "/etc/systemd/system/${service_name}"
chmod 0644 "/etc/systemd/system/${service_name}"

sed "s|@@SERVER_NAME@@|${server_name}|g" \
  "${release_dir}/deploy/nginx.conf" \
  > /etc/nginx/sites-available/bite-hunt-careers
chmod 0644 /etc/nginx/sites-available/bite-hunt-careers
ln -sfn /etc/nginx/sites-available/bite-hunt-careers /etc/nginx/sites-enabled/bite-hunt-careers
if [[ -L /etc/nginx/sites-enabled/default ]]; then
  unlink /etc/nginx/sites-enabled/default
fi

nginx -t
systemctl daemon-reload
systemctl enable --now "${service_name}"
systemctl restart "${service_name}"
systemctl enable --now nginx
systemctl reload nginx

healthy=0
for _attempt in {1..20}; do
  if curl --fail --silent --max-time 2 http://127.0.0.1:8000/health >/dev/null; then
    healthy=1
    break
  fi
  sleep 1
done
if [[ "${healthy}" -ne 1 ]]; then
  printf 'The service did not become healthy. Inspect: journalctl -u %s -n 100 --no-pager\n' "${service_name}" >&2
  exit 1
fi

printf '\nBite Hunt Careers is installed.\n'
printf 'Public site: http://SERVER_IP/\n'
printf 'Hiring desk: http://SERVER_IP/admin/login\n'
printf 'Configuration: %s\n' "${config_file}"
printf 'DeepSeek configuration: %s\n' "${ai_config_file}"
printf 'Service logs: journalctl -u %s -f\n' "${service_name}"
