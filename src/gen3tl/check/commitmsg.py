"""Commit message rules and the checks of one message or a revision range."""

from __future__ import annotations

import re
import subprocess
import sys

from ..core.terminal import line

TYPES = ("feat", "fix", "refactor", "docs", "test", "chore", "perf")
WORDS = 15
RULES = "CONTRIBUTING.md"
SUBJECT = re.compile(r"(?P<type>[^\s(:]+)\((?P<scope>[^()]*)\)!?: (?P<summary>.*)")
SCOPE = re.compile(r"[a-z0-9][a-z0-9./-]*")
REFERENCE = re.compile(r" \(#\d+\)$")
TRAILER = re.compile(r"[A-Za-z][A-Za-z0-9-]*: ")
SIGN_OFF = re.compile(r"Signed-off-by: [^<>]+ <[^<>\s]+>")
BANNER = re.compile(r"\W*generated with\b", re.I)
FORBIDDEN = (
    (re.compile(r"\b(?:(?:task|decision)s?[ -]|(?:doc|draft)-)\d+(?:\.\d+)*\b", re.I), "a local record identifier"),
    (re.compile(r"\bq-[a-z0-9]{6}\b"), "a local request identifier"),
    (re.compile(r"\bsession[_/-][A-Za-z0-9]{12,}", re.I), "a session identifier"),
    (re.compile(r"\b[A-Za-z]+(?:-[A-Za-z]+)*-by:", re.I), "an authorship trailer"),
)
# git's default comment and verbose-diff markers
COMMENT = "#"
SCISSORS = "# ------------------------ >8 ------------------------"
USAGE = "commit-msg FILE | commit-msg --range REVISIONS"


def subject_problems(subject):
    match = SUBJECT.fullmatch(subject)
    if not match:
        return ["the subject is not 'type(scope): summary'"]
    found = []
    if match["type"] not in TYPES:
        found.append(f"type '{match['type']}' is not one of {', '.join(TYPES)}")
    if not SCOPE.fullmatch(match["scope"]):
        found.append(f"scope '{match['scope']}' is not lowercase letters, digits, '.', '/' or '-'")
    summary = REFERENCE.sub("", match["summary"])
    words = summary.split()
    if not words:
        found.append("the summary is empty")
    elif not summary[0].islower():
        found.append("the summary does not begin with a lowercase imperative verb")
    if len(words) > WORDS:
        found.append(f"the summary has {len(words)} words; the limit is {WORDS}")
    if summary.endswith("."):
        found.append("the summary ends with a period")
    return found


def line_problems(number, line, sign_off=False):
    found = [f"names {what} '{hit.group()}'" for pattern, what in FORBIDDEN for hit in [pattern.search(line)] if hit]
    if sign_off:
        found = [problem for problem in found if not problem.startswith("names an authorship trailer")]
    if number == 1:
        return found + subject_problems(line)
    if BANNER.match(line):
        found.append("an authorship line")
    elif TRAILER.match(line) and not found and not sign_off:
        found.append("a trailer; a labelled body line is a list item, '- Label: value'")
    if len(line.split()) > WORDS:
        found.append(f"{len(line.split())} words; a body line holds at most {WORDS}")
    return found


def problems(message):
    """The rule violations of one commit message, or none."""
    lines = [line.rstrip() for line in message.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    if not lines:
        return ["the message is empty"]
    found = []
    if len(lines) > 1 and lines[1]:
        found.append("line 2: a blank line separates the subject from the body")
    for number, line in enumerate(lines, 1):
        sign_off = number == len(lines) and SIGN_OFF.fullmatch(line) is not None
        found += [f"line {number}: {problem}" for problem in line_problems(number, line, sign_off)]
    return found


def strip_comments(text):
    """The message as git stores it under the default cleanup."""
    kept = []
    for line in text.split("\n"):
        if line == SCISSORS:
            break
        if not line.startswith(COMMENT):
            kept.append(line)
    return "\n".join(kept)


def merging():
    try:
        return subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                              capture_output=True).returncode == 0
    except OSError:
        return False


def git_log(revisions):
    """(short id, message) for each non-merge commit of a range."""
    run = subprocess.run(["git", "log", "--no-merges", "-z", "--format=%H%n%B", revisions, "--"],
                         capture_output=True, encoding="utf-8", errors="replace")
    if run.returncode:
        raise ValueError(run.stderr.strip() or f"git log {revisions} failed")
    for record in run.stdout.split("\0"):
        if record.strip():
            commit, _, message = record.partition("\n")
            yield commit[:12], message


def report(where, found):
    for problem in found:
        line("problem", f"{where}: {problem}", file=sys.stderr)
    return len(found)


def finished(count):
    if count:
        line("rules", RULES, file=sys.stderr)
        return 1
    return 0


def check_file(path):
    """One message file; git runs the hook with the prepared message."""
    if merging():
        # git writes the merge message itself
        return 0
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            count = report(path, problems(strip_comments(handle.read())))
    except OSError as exc:
        line("error", str(exc), file=sys.stderr)
        return 2
    return finished(count)


def check_range(revisions):
    """Every non-merge commit of one revision range."""
    try:
        commits = list(git_log(revisions))
    except (OSError, ValueError) as exc:
        line("error", str(exc), file=sys.stderr)
        return 2
    count = sum(report(commit, problems(message)) for commit, message in commits)
    line("commits", len(commits))
    line("problems", count)
    return finished(count)


def main(argv):
    if len(argv) == 1 and not argv[0].startswith("-"):
        return check_file(argv[0])
    if len(argv) == 2 and argv[0] == "--range" and not argv[1].startswith("-"):
        return check_range(argv[1])
    line("usage", USAGE, file=sys.stderr)
    return 2
