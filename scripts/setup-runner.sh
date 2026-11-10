#!/usr/bin/env bash
# Install the self-hosted GitHub Actions runner for .github/workflows/laws.yml on a Linux VPS
# that data.rada.gov.ua answers. Run as root:
#
#   sudo bash setup-runner.sh <registration-token>
#
# The token: `gh api -X POST repos/yuranevmer/jurik/actions/runners/registration-token -q .token`
# (valid for one hour). The runner runs as the user jurik-runner under systemd with the
# labels self-hosted, linux, jurik; the download cache is ~jurik-runner/jurik-cache.
set -euo pipefail

token="${1:?usage: setup-runner.sh <registration-token>}"
repo_url="${RUNNER_REPO_URL:-https://github.com/yuranevmer/jurik}"
user=jurik-runner
probe=https://data.rada.gov.ua/laws/card/2947-14.json

code="$(curl -s -o /dev/null -m 30 -A OpenData -w '%{http_code}' "$probe" || true)"
if [[ "$code" != 200 ]]; then
  echo "ERROR: $probe answered '$code' from this server; the build needs 200" >&2
  exit 1
fi

if command -v apt-get >/dev/null; then
  apt-get update -qq
  apt-get install -y -qq git curl tar ca-certificates openssh-client >/dev/null
fi

id "$user" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$user"
home="$(getent passwd "$user" | cut -d: -f6)"
dir="$home/actions-runner"
install -d -o "$user" -g "$user" "$dir" "$home/jurik-cache"

arch="$(uname -m)"; case "$arch" in x86_64) arch=x64 ;; aarch64) arch=arm64 ;; esac
version="$(curl -sS https://api.github.com/repos/actions/runner/releases/latest \
  | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p')"
tarball="actions-runner-linux-${arch}-${version}.tar.gz"
curl -sSL -o "/tmp/$tarball" \
  "https://github.com/actions/runner/releases/download/v${version}/${tarball}"
tar -xzf "/tmp/$tarball" -C "$dir" && rm -f "/tmp/$tarball"
chown -R "$user:$user" "$dir"
"$dir/bin/installdependencies.sh" >/dev/null

sudo -u "$user" "$dir/config.sh" --unattended --replace --url "$repo_url" --token "$token" \
  --name "$(hostname)-jurik" --labels jurik --work _work
cd "$dir" && ./svc.sh install "$user" && ./svc.sh start
echo "ok: runner $(hostname)-jurik is up; cache: $home/jurik-cache"
