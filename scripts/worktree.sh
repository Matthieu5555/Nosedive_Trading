#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
WT="$ROOT/.claude/worktrees"
MAIN=main

die() { printf 'worktree: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*" >&2; }

slug_ok() {
  [[ "$1" =~ ^[a-z0-9][a-z0-9-]+$ ]] || return 1
  [[ "$1" =~ ^worktree-agent- ]] && return 1
  [[ "$1" =~ ^[0-9a-f]{12,}$ ]] && return 1
  return 0
}

base_ref() {
  if git -C "$ROOT" rev-parse --verify -q "origin/$MAIN" >/dev/null 2>&1 \
     && git -C "$ROOT" merge-base --is-ancestor "$MAIN" "origin/$MAIN" 2>/dev/null; then
    printf 'origin/%s\n' "$MAIN"
  else
    printf '%s\n' "$MAIN"
  fi
}

wt_paths() {
  git -C "$ROOT" worktree list --porcelain | awk '/^worktree /{print $2}' | grep -vxF "$ROOT" || true
}

classify() {
  local p="$1" b ahead behind dirty state
  b="$(git -C "$p" symbolic-ref --short HEAD 2>/dev/null || echo DETACHED)"
  ahead="$(git -C "$ROOT" rev-list --count "$MAIN..$b" 2>/dev/null || echo 0)"
  behind="$(git -C "$p" rev-list --count "HEAD..$MAIN" 2>/dev/null || echo 0)"
  dirty="$(git -C "$p" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  if   [ "$dirty" -gt 0 ]; then state=DIRTY
  elif [ "$ahead" -gt 0 ]; then state=UNMERGED
  else state=DONE; fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$state" "$b" "$ahead" "$behind" "$dirty" "$p"
}

cmd_new() {
  local slug="${1:-}"
  [ -n "$slug" ] || die "usage: worktree.sh new <task-slug>"
  slug_ok "$slug" || die "bad slug '$slug' — lowercase task words like 'ibkr-reauth', never a hash or 'worktree-agent-*'"
  git -C "$ROOT" show-ref --verify --quiet "refs/heads/$slug" && die "branch '$slug' already exists"
  [ -e "$WT/$slug" ] && die "path already exists: $WT/$slug"
  git -C "$ROOT" fetch --quiet origin "$MAIN" 2>/dev/null || true
  local base; base="$(base_ref)"
  git -C "$ROOT" worktree add -b "$slug" "$WT/$slug" "$base" >&2
  note "fresh worktree on $base ($(git -C "$ROOT" rev-parse --short "$base"))"
  printf '%s\n' "$WT/$slug"
}

cmd_land() {
  local msg="" slug="" wt branch base
  while [ $# -gt 0 ]; do
    case "$1" in
      -m) msg="${2:-}"; shift 2 ;;
      *) slug="$1"; shift ;;
    esac
  done
  if [ -n "$slug" ]; then wt="$WT/$slug"; else wt="$(git rev-parse --show-toplevel)"; fi
  [ -d "$wt" ] || die "no worktree at $wt"
  [ "$wt" != "$ROOT" ] || die "run land from inside a task worktree, not the main checkout"
  branch="$(git -C "$wt" symbolic-ref --short HEAD)" || die "$wt is in detached HEAD"
  [ "$branch" != "$MAIN" ] || die "refusing to land $MAIN onto itself"

  if [ -n "$(git -C "$wt" status --porcelain)" ]; then
    [ -n "$msg" ] || die "worktree dirty — commit it first, or pass -m \"msg\" (inside a worktree, 'git add -A' is fine, it is yours)"
    git -C "$wt" add -A
    git -C "$wt" commit -m "$msg" >&2
  fi

  base="$(base_ref)"
  git -C "$ROOT" fetch --quiet origin "$MAIN" 2>/dev/null || true
  if ! git -C "$wt" rebase "$base" >&2; then
    git -C "$wt" rebase --abort 2>/dev/null || true
    die "rebase onto $base hit conflicts — resolve them in $wt, then re-run land"
  fi

  [ -z "$(git -C "$ROOT" status --porcelain)" ] \
    || die "main checkout is dirty ($ROOT) — commit/stash its changes first; the shared checkout must stay clean"
  git -C "$ROOT" switch --quiet "$MAIN"
  git -C "$ROOT" merge --ff-only "$branch" >&2 \
    || die "main not fast-forwardable to $branch after rebase — inspect manually, nothing was deleted"
  if git -C "$ROOT" push --quiet origin "$MAIN" 2>/dev/null; then
    note "pushed origin/$MAIN"
  else
    note "WARN: local $MAIN advanced but push failed — push manually (work is safe on local $MAIN)"
  fi
  git -C "$ROOT" worktree remove "$wt"
  git -C "$ROOT" branch -d "$branch"
  printf 'landed %s -> %s (worktree + branch removed)\n' "$branch" "$MAIN"
}

cmd_status() {
  printf '%-10s %-46s %5s %6s %5s\n' STATE BRANCH AHEAD BEHIND DIRTY
  local p line
  for p in $(wt_paths); do
    line="$(classify "$p")"
    IFS=$'\t' read -r st br ah bh dt _ <<<"$line"
    printf '%-10s %-46s %5s %6s %5s\n' "$st" "$br" "$ah" "$bh" "$dt"
  done
  printf '\n%s local branches, %s task worktrees\n' \
    "$(git -C "$ROOT" for-each-ref --format='x' refs/heads/ | wc -l | tr -d ' ')" \
    "$(wt_paths | wc -l | tr -d ' ')"
}

cmd_gc() {
  local dry=0; [ "${1:-}" = "--dry-run" ] && dry=1
  local p st br ah bh dt _
  for p in $(wt_paths); do
    IFS=$'\t' read -r st br ah bh dt _ <<<"$(classify "$p")"
    case "$st" in
      DONE)
        if [ "$dry" = 1 ]; then note "would remove DONE worktree: $br ($p)"
        else git -C "$ROOT" worktree remove "$p" && note "removed DONE worktree: $br"; fi
        if [[ "$br" =~ ^worktree-agent- ]]; then
          if [ "$dry" = 1 ]; then note "would delete husk branch: $br"
          else git -C "$ROOT" branch -d "$br" 2>/dev/null && note "deleted husk branch: $br" || true; fi
        fi ;;
      DIRTY)    note "HOLD  $br — $dt uncommitted files, $bh behind main; commit + land, won't touch" ;;
      UNMERGED) note "HOLD  $br — $ah commits unmerged; land it, won't touch" ;;
    esac
  done
  local b
  for b in $(git -C "$ROOT" for-each-ref --format='%(refname:short)' refs/heads/); do
    [[ "$b" =~ ^worktree-agent- ]] || [ "$b" = "integration/core-fleet" ] || continue
    [ "$(git -C "$ROOT" rev-list --count "$MAIN..$b" 2>/dev/null || echo 1)" = 0 ] || continue
    git -C "$ROOT" worktree list --porcelain | grep -q "branch refs/heads/$b$" && continue
    if [ "$dry" = 1 ]; then note "would delete merged husk branch: $b"
    else git -C "$ROOT" branch -d "$b" 2>/dev/null && note "deleted merged husk branch: $b" || true; fi
  done
}

usage() {
  cat >&2 <<'EOF'
worktree.sh — the only sanctioned way to run a task branch's whole life.

  new <slug>      fresh worktree+branch off the freshest main (never a stale base)
  land [-m msg]   from inside a worktree: commit (with -m), rebase onto main,
                  fast-forward main, push, then delete the worktree + branch
  status          one line per task worktree: merged / unmerged / dirty + staleness
  gc [--dry-run]  remove merged-and-done worktrees and husk branches; never touches
                  dirty or unmerged work

One task = one fresh branch off main = land = gone. Nothing lives between tasks but main.
EOF
  exit 1
}

case "${1:-}" in
  new)    shift; cmd_new "$@" ;;
  land)   shift; cmd_land "$@" ;;
  status) shift; cmd_status "$@" ;;
  gc)     shift; cmd_gc "$@" ;;
  *)      usage ;;
esac
