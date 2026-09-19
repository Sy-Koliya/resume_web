#!/usr/bin/env bash
set -Eeuo pipefail

base_url="${1:-http://127.0.0.1:8000}"
host_header="${2:-127.0.0.1}"
label="${3:-application}"
base_url="${base_url%/}"

curl_args=(
  --fail
  --silent
  --show-error
  --max-time 12
  --header "Host: ${host_header}"
)

expect_text() {
  local body="$1"
  local expected="$2"
  local description="$3"
  if [[ "${body}" != *"${expected}"* ]]; then
    printf '[FAIL] %s did not contain %s\n' "${label}" "${description}" >&2
    return 1
  fi
}

health_body="$(curl "${curl_args[@]}" "${base_url}/health")"
expect_text "${health_body}" '"status":"ok"' 'a healthy status response'

home_body="$(curl "${curl_args[@]}" "${base_url}/")"
expect_text "${home_body}" '<title>Bite Hunt - Careers</title>' 'the careers page title'
expect_text "${home_body}" 'href="/static/site.css"' 'the stylesheet link'
expect_text "${home_body}" 'data-application-form' 'the application form'

css_headers="$(curl "${curl_args[@]}" --head "${base_url}/static/site.css")"
expect_text "${css_headers,,}" 'content-type: text/css' 'a CSS content type'
css_body="$(curl "${curl_args[@]}" "${base_url}/static/site.css")"
expect_text "${css_body}" ':root {' 'the site stylesheet'

javascript_body="$(curl "${curl_args[@]}" "${base_url}/static/site.js")"
expect_text "${javascript_body}" 'data-application-form' 'the site JavaScript'

printf '[OK] %s rendered HTML and served CSS/JavaScript correctly.\n' "${label}"
