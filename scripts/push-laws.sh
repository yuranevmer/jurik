#!/usr/bin/env bash
# Push every branch of the built laws repository to GitHub (used by .github/workflows/laws.yml).
#
# The build is deterministic, so commits that did not change keep their hashes and an
# ordinary day is a fast-forward. When a late edition lands before the tip of main (main is
# ordered by In-force), the tail of main is rewritten: the push is forced and a warning is
# printed.
#
# GitHub drops a single push of ~10 000 refs, so main goes first (it carries most objects),
# then only the branches whose commit differs from the remote, in batches; branches gone
# from the build are deleted on the remote, in batches too.
#
# Env: LAWS_REPO (git URL or path of the target), LAWS_DIR (default build/laws),
#      LAWS_DEPLOY_KEY (optional: private SSH deploy key with write access),
#      PUSH_BATCH (refs per push, default 300).
set -euo pipefail

repo="${LAWS_REPO:?set LAWS_REPO, e.g. git@github.com:owner/jurik-laws.git}"
dir="${LAWS_DIR:-build/laws}"
batch="${PUSH_BATCH:-300}"

if [[ -n "${LAWS_DEPLOY_KEY:-}" ]]; then
  key="$(mktemp)"
  trap 'rm -f "$key"' EXIT
  printf '%s\n' "$LAWS_DEPLOY_KEY" > "$key"
  chmod 600 "$key"
  export GIT_SSH_COMMAND="ssh -i $key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi

cd "$dir"
work="$(mktemp -d)"
trap 'rm -rf "$work" ${key:+"$key"}' EXIT

git ls-remote --heads "$repo" | awk '{print $2, $1}' | sort > "$work/remote"
git for-each-ref --format='%(refname) %(objectname)' refs/heads | sort > "$work/local"

new="$(git rev-parse refs/heads/main)"
old="$(awk '$1 == "refs/heads/main" {print $2}' "$work/remote")"
if [[ -z "$old" ]]; then
  echo "remote main: none (first push)"
elif [[ "$old" == "$new" ]]; then
  echo "remote main: unchanged ($new)"
elif git cat-file -e "$old^{commit}" 2>/dev/null && git merge-base --is-ancestor "$old" "$new"; then
  echo "remote main: fast-forward $old..$new ($(git rev-list --count "$old..$new") commits)"
else
  echo "::warning::main history rewritten (late edition or changed metadata): $old -> $new"
fi
[[ "$old" == "$new" ]] || git push --force --quiet "$repo" "refs/heads/main:refs/heads/main"

# branches to create or move: local lines that are not identical on the remote
comm -23 "$work/local" "$work/remote" | awk '$1 != "refs/heads/main" {print "+" $1 ":" $1}' \
  > "$work/update"
# branches to delete: on the remote, not in the build
cut -d' ' -f1 "$work/local" > "$work/local-names"
cut -d' ' -f1 "$work/remote" | comm -23 - "$work/local-names" | awk '{print ":" $1}' \
  > "$work/delete"

push_batches() {  # $1: refspec file, $2: label
  local n
  n="$(wc -l < "$1" | tr -d ' ')"
  [[ "$n" -gt 0 ]] || return 0
  split -l "$batch" "$1" "$work/chunk-"
  for chunk in "$work"/chunk-*; do
    xargs git push --quiet "$repo" < "$chunk"
    rm -f "$chunk"
  done
  echo "$2: $n branches"
}
push_batches "$work/update" "pushed"
push_batches "$work/delete" "deleted"
echo "remote now has $(wc -l < "$work/local" | tr -d ' ') branches"
