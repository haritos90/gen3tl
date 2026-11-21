#!/usr/bin/env bash
# Turn a toolkit submission issue into a pull request.
# runs at the repository root, on the base branch.
# env: GITHUB_EVENT_PATH, GH_TOKEN, GH_REPO, PYTHON, SUBMISSION_*.
set -euo pipefail

python="${PYTHON:-python}"
event="${GITHUB_EVENT_PATH:?GITHUB_EVENT_PATH names no issues event}"
marker='<!-- gen3tl-submission -->'
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

remote() {
  if [ -n "${SUBMISSION_DRY_RUN:-}" ]; then
    printf 'dry run:' >&2
    printf ' %q' "$@" >&2
    printf '\n' >&2
  else
    "$@"
  fi
}

changed_paths() {
  git status --porcelain "$@" | awk '{print $2}' | sort
}

revert_paths() {
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    if git cat-file -e "HEAD:$file" 2>/dev/null; then
      git restore --source=HEAD --worktree -- "$file"
    else
      rm -f -- "$file"
    fi
  done
}

refuse() {
  local log="$1" reason="$2"
  {
    printf '%s\n' "$marker" "This submission cannot be applied: $reason." ""
    printf '%s\n' '```'
    tail -n 40 "$log"
    printf '%s\n' '```'
  } >"$out/comment.md"
  remote gh issue comment "$number" --body-file "$out/comment.md"
}

# Exit 1 means refused; the status file tells.
"$python" -m gen3tl web submission --event "$event" --out "$out" --dry-run || true
status="$(cat "$out/status" 2>/dev/null || true)"
case "$status" in
  valid | invalid) ;;
  *)
    printf '%s\n' "submission: web submission wrote no status" >&2
    exit 1
    ;;
esac
number="$(cat "$out/issue")"
pack=0
if [ "$status" = invalid ]; then
  if [ -f "$out/export.json" ]; then
    # a pack-only export is valid: web import applies it
    if "$python" - "$out" "$event" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

from gen3tl.web.submission import parse

out = Path(sys.argv[1])
event = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
issue = event.get("issue") or {}
number = issue.get("number")
try:
    doc = parse((out / "export.json").read_bytes())
except ValueError as exc:
    raise SystemExit(f"error: {exc}") from None
profile, language = doc.get("profile"), doc.get("language")
if profile == "site" or doc.get("entries") or doc.get("terms") or doc.get("pictures") \
        or not (doc.get("glyphs") or doc.get("keyboard")):
    raise SystemExit("the export is not a pack-only submission")
glyphs = doc.get("glyphs") or []
keyboard = doc.get("keyboard") or {}
rows = keyboard.get("rows") or {}
pages = keyboard.get("pages")
keys = len(rows) + (1 if pages is not None else 0)
parts = []
if glyphs:
    parts.append(f"{len(glyphs)} glyph {'redraw' if len(glyphs) == 1 else 'redraws'}")
if keys:
    parts.append(f"{keys} keyboard {'change' if keys == 1 else 'changes'}")
summary = " and ".join(parts) + f" of {profile}/{language}"
sha256 = hashlib.sha256((out / "export.json").read_bytes()).hexdigest()
(out / "profile").write_text(f"{profile}\n", encoding="utf-8")
(out / "language").write_text(f"{language}\n", encoding="utf-8")
(out / "summary").write_text(summary + "\n", encoding="utf-8")
(out / "title").write_text(f"Editor submission #{number}: {profile}/{language}\n", encoding="utf-8")
body = [f"Language pack edits submitted with the editor in #{number}.", "",
        f"- Project: `{profile}/{language}`"]
if glyphs:
    body.append(f"- Glyph redraws: {len(glyphs)}, written to the pack art")
if keys:
    body.append(f"- Keyboard changes: {keys}, written to the pack layout")
body += [f"- Export SHA-256: `{sha256}`", "", f"Resolves #{number}", ""]
(out / "pr.md").write_text("\n".join(body) + "\n", encoding="utf-8")
notes = (doc.get("notes") or "").strip()
if notes:
    (out / "notes.md").write_text(f"Notes for the reviewers:\n\n```text\n{notes}\n```\n", encoding="utf-8")
else:
    (out / "notes.md").write_text("", encoding="utf-8")
(out / "commit.txt").write_text(
    f"fix(lang): apply editor submission (#{number})\n\n" + "\n".join(body) + "\n", encoding="utf-8")
PY
    then
      pack=1
    fi
  fi
  if [ "$pack" = 0 ]; then
    remote gh issue comment "$number" --body-file "$out/comment.md"
    exit 0
  fi
fi

if [ "$pack" = 1 ]; then
  profile="$(cat "$out/profile")"
  language="$(cat "$out/language")"
  if ! "$python" -m gen3tl --profile "$profile" --lang "$language" web import "$out/export.json" --dry-run \
      >"$out/import-check.txt" 2>&1; then
    refuse "$out/import-check.txt" "web import (dry run) refuses the export"
    exit 0
  fi
  if ! "$python" -m gen3tl --profile "$profile" --lang "$language" web import "$out/export.json" \
      >"$out/import.txt" 2>&1; then
    changed_paths assets/lang >"$out/changed.txt"
    revert_paths <"$out/changed.txt"
    refuse "$out/import.txt" "web import refuses the export"
    exit 0
  fi

  changed_paths assets/lang >"$out/changed.txt"
  changed_paths >"$out/all.txt"
  if ! cmp -s "$out/changed.txt" "$out/all.txt"; then
    revert_paths <"$out/all.txt"
    {
      printf '%s\n' "$marker" "This submission cannot be applied: web import would change more than the submitted pack." ""
      printf '%s\n' '```'
      cat "$out/all.txt"
      printf '%s\n' '```'
    } >"$out/comment.md"
    remote gh issue comment "$number" --body-file "$out/comment.md"
    exit 0
  fi
  [ -s "$out/changed.txt" ] || {
    {
      printf '%s\n' "$marker" "This submission cannot be applied: web import wrote no pack files." ""
      printf '%s\n' '```'
      tail -n 40 "$out/import.txt"
      printf '%s\n' '```'
    } >"$out/comment.md"
    remote gh issue comment "$number" --body-file "$out/comment.md"
    exit 0
  }
  files="$out/changed.txt"
else
  profile="$(cat "$out/profile")"
  if [ "$profile" != site ]; then
    remote gh issue comment "$number" --body-file "$out/comment.md"
    exit 0
  fi
  # Import checks the export before the apply writes it.
  if ! "$python" -m gen3tl web import "$out/export.json" --dry-run >"$out/import-check.txt" 2>&1; then
    refuse "$out/import-check.txt" "web import (dry run) refuses the export"
    exit 0
  fi

  # Apply the checked export.
  "$python" -m gen3tl web submission --event "$event" --out "$out" || true
  status="$(cat "$out/status" 2>/dev/null || true)"
  if [ "$status" != valid ]; then
    remote gh issue comment "$number" --body-file "$out/comment.md"
    exit 0
  fi

  # sync must change nothing beyond the export
  if ! "$python" -m gen3tl web site-catalog --sync --data "$out/site-data" >"$out/ui-check.txt" 2>&1; then
    changed_paths web/i18n assets/lang | revert_paths
    refuse "$out/ui-check.txt" "the site catalog check (web site-catalog --sync) fails"
    exit 0
  fi

  changed_paths web/i18n assets/lang >"$out/changed.txt"
  extra=0
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    grep -qxF "$file" "$out/files" || extra=1
  done <"$out/changed.txt"
  if [ "$extra" = 1 ]; then
    revert_paths <"$out/changed.txt"
    {
      printf '%s\n' "$marker" "This submission cannot be applied: web site-catalog --sync would change more than the submitted catalog." ""
      printf '%s\n' '```'
      cat "$out/changed.txt"
      printf '%s\n' '```'
    } >"$out/comment.md"
    remote gh issue comment "$number" --body-file "$out/comment.md"
    exit 0
  fi
  files="$out/files"
fi

base="${SUBMISSION_BASE:-$(git rev-parse --abbrev-ref HEAD)}"
branch="submission/$number"
title="$(cat "$out/title")"
git checkout -q -B "$branch"
git add --pathspec-from-file="$files"
git -c user.name='github-actions[bot]' \
  -c user.email='41898282+github-actions[bot]@users.noreply.github.com' \
  commit -q -F "$out/commit.txt"
# The branch belongs to the issue; edits replace it.
remote git push --force origin "HEAD:refs/heads/$branch"
existing="$(remote gh pr list --head "$branch" --base "$base" --state open --json url --jq '.[0].url // empty')"
if [ -n "$existing" ]; then
  remote gh pr edit "$existing" --title "$title" --body-file "$out/pr.md"
  url="$existing"
else
  url="$(remote gh pr create --base "$base" --head "$branch" --title "$title" --body-file "$out/pr.md")"
fi
{
  printf '%s\n' "$marker" "Pull request ${url:-(dry run)} applies the $(cat "$out/summary")." ""
  printf '%s\n' "Edit the issue to change the submission; the pull request follows the edit."
  # Empty without notes: no section.
  if [ -s "$out/notes.md" ]; then
    printf '\n'
    cat "$out/notes.md"
  fi
} >"$out/answer.md"
remote gh issue comment "$number" --body-file "$out/answer.md"
git checkout -q "$base"
printf 'submission: #%s -> %s\n' "$number" "${url:-(dry run)}"
