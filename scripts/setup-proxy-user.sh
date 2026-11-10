#!/usr/bin/env bash
# Run as root on the proxy VPS (it must reach data.rada.gov.ua, see README «CI»):
#   sudo bash setup-proxy-user.sh 'ssh-ed25519 AAAA… jurik-ci-proxy'
#
# Creates the user jurik-proxy without a shell whose key may only open SSH tunnels to the
# Rada hosts (`ssh -N -D`, a SOCKS proxy for the GitHub Actions runner): no commands, no
# forwarding to anything else, no new open ports on the server.
set -euo pipefail

pubkey="${1:?usage: setup-proxy-user.sh '<ssh public key>'}"
user=jurik-proxy
hosts=(data.rada.gov.ua:443 itd.rada.gov.ua:443 w1.c1.rada.gov.ua:80)

if ! id "$user" >/dev/null 2>&1; then
  useradd --create-home --shell /usr/sbin/nologin "$user"
fi
home="$(getent passwd "$user" | cut -d: -f6)"
install -d -m 700 -o "$user" -g "$user" "$home/.ssh"

opts="restrict,port-forwarding"
for h in "${hosts[@]}"; do opts+=",permitopen=\"$h\""; done
printf '%s %s\n' "$opts" "$pubkey" > "$home/.ssh/authorized_keys"
chown "$user:$user" "$home/.ssh/authorized_keys"
chmod 600 "$home/.ssh/authorized_keys"

# sshd must allow TCP forwarding (the default); report if it is switched off
if sshd -T 2>/dev/null | grep -qi '^allowtcpforwarding no'; then
  echo "WARNING: AllowTcpForwarding is 'no' in sshd_config: tunnels will not work" >&2
fi
echo "ok: $user may tunnel to ${hosts[*]}"
