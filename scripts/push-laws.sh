#!/usr/bin/env bash
# Push every branch of the built laws repository to GitHub (used by .github/workflows/laws.yml).
#
# The build is deterministic, so commits that did not change keep their hashes and an
# ordinary day is a fast-forward. When a late edition lands before the tip of main (main is
# ordered by In-force), the tail of main is rewritten: the push is forced and a warning is
# printed. Branches gone from the build are deleted on the remote (--prune).
#
# Env: LAWS_REPO (git URL of the target), LAWS_DIR (default build/laws),
#      LAWS_DEPLOY_KEY (optional: private SSH deploy key with write access).
set -euo pipefail

repo="${LAWS_REPO:?set LAWS_REPO, e.g. git@github.com:owner/jurik-laws.git}"
dir="${LAWS_DIR:-build/laws}"

if [[ -n "${LAWS_DEPLOY_KEY:-}" ]]; then
  key="$(mktemp)"
  trap 'rm -f "$key"' EXIT
  printf '%s\n' "$LAWS_DEPLOY_KEY" > "$key"
  chmod 600 "$key"
  export GIT_SSH_COMMAND="ssh -i $key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi

cd "$dir"
new="$(git rev-parse refs/heads/main)"
old="$(git ls-remote "$repo" refs/heads/main | cut -f1)"

if [[ -z "$old" ]]; then
  echo "remote main: none (first push)"
elif [[ "$old" == "$new" ]]; then
  echo "remote main: unchanged ($new)"
elif git cat-file -e "$old^{commit}" 2>/dev/null && git merge-base --is-ancestor "$old" "$new"; then
  echo "remote main: fast-forward $old..$new ($(git rev-list --count "$old..$new") commits)"
else
  echo "::warning::main history rewritten (late edition or changed metadata): $old -> $new"
fi

git push --force --prune --quiet "$repo" 'refs/heads/*:refs/heads/*'
echo "pushed $(git for-each-ref refs/heads | wc -l | tr -d ' ') branches to $repo"
