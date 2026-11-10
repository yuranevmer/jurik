#!/usr/bin/env bash
# Install the self-hosted GitHub Actions runner for .github/workflows/laws.yml on a Linux
# machine that data.rada.gov.ua answers. No root needed: run as the user that will own the
# runner (it needs git, curl, tar and libicu):
#
#   bash setup-runner.sh <registration-token>
#
# The token: `gh api -X POST repos/yuranevmer/jurik/actions/runners/registration-token -q .token`
# (valid for one hour). The runner is a systemd user service (actions-runner-jurik) with
# lingering on, so it starts at boot without a login; labels self-hosted, linux, jurik; the
# download cache is ~/jurik-cache (seed it with `rsync -a cache/ <host>:jurik-cache/`).
set -euo pipefail

token="${1:?usage: setup-runner.sh <registration-token>}"
repo_url="${RUNNER_REPO_URL:-https://github.com/yuranevmer/jurik}"
dir="$HOME/actions-runner-jurik"
probe=https://data.rada.gov.ua/laws/card/2947-14.json

ok=0
for _ in 1 2 3; do  # the site sometimes stalls for a while
  code="$(curl -s -o /dev/null -m 30 -A OpenData -w '%{http_code}' "$probe" || true)"
  [[ "$code" == 200 ]] && { ok=1; break; }
done
if [[ "$ok" != 1 ]]; then
  echo "ERROR: $probe does not answer 200 from this machine (last: '$code')" >&2
  exit 1
fi

mkdir -p "$dir" "$HOME/jurik-cache" "$HOME/.config/systemd/user"
arch="$(uname -m)"; case "$arch" in x86_64) arch=x64 ;; aarch64) arch=arm64 ;; esac
version="$(curl -sS https://api.github.com/repos/actions/runner/releases/latest \
  | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p')"
curl -sSL "https://github.com/actions/runner/releases/download/v${version}/actions-runner-linux-${arch}-${version}.tar.gz" \
  | tar -xz -C "$dir"

"$dir/config.sh" --unattended --replace --url "$repo_url" --token "$token" \
  --name "$(hostname)-jurik" --labels jurik --work _work

cat > "$HOME/.config/systemd/user/actions-runner-jurik.service" <<UNIT
[Unit]
Description=GitHub Actions runner for $repo_url (laws workflow)
After=network-online.target

[Service]
WorkingDirectory=%h/actions-runner-jurik
ExecStart=%h/actions-runner-jurik/run.sh
Environment=JURIK_CACHE=%h/jurik-cache
Restart=always
RestartSec=30
KillMode=process
TimeoutStopSec=5min

[Install]
WantedBy=default.target
UNIT
loginctl enable-linger "$(whoami)"
systemctl --user daemon-reload
systemctl --user enable --now actions-runner-jurik.service
echo "ok: runner $(hostname)-jurik (runner $version) is up; cache: $HOME/jurik-cache"
