"""Shared terminal output for commands and standalone tools."""

from __future__ import annotations

import argparse
import io
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from json import JSONDecodeError
from tomllib import TOMLDecodeError


class CommandError(ValueError):
    """An input error with recovery details."""

    def __init__(self, reason, *, hint=None, valid=None):
        super().__init__(reason)
        self.hint = hint
        self.valid = valid


def require_names(kind, selected, valid):
    """Reject unknown names before doing work."""
    valid = sorted(set(valid))
    unknown = sorted(set(selected) - set(valid))
    if unknown:
        raise CommandError(f"unknown {kind}: {', '.join(unknown)}",
                           hint=f"{kind}: valid names", valid=valid)


def error_details(error):
    """Translate runtime errors into actionable diagnostics."""
    if isinstance(error, CommandError):
        return str(error), error.hint
    cause = error.__cause__
    if (isinstance(cause, (OSError, JSONDecodeError, TOMLDecodeError, UnicodeError))
            or (isinstance(error, OSError) and cause is not None)):
        detail, hint = error_details(cause)
        message = str(error) or type(error).__name__
        raw = str(cause)
        if raw not in message and isinstance(cause, OSError):
            raw = cause.strerror or raw
        if raw and raw in message:
            message = message.replace(raw, detail)
        elif detail not in message:
            message = f"{message}: {detail}"
        return message, hint
    if isinstance(error, OSError):
        reasons = {FileNotFoundError: ("missing path", "path: existing file or directory"),
                   IsADirectoryError: ("expected file", "path: file"),
                   NotADirectoryError: ("expected directory", "path: directory"),
                   PermissionError: ("access denied", "path: permissions"),
                   FileExistsError: ("path exists", "output: unused path")}
        reason, hint = reasons.get(type(error), ("file operation failed", "path: permissions and available space"))
        paths = [str(path) for path in (error.filename, error.filename2) if path is not None]
        return reason + (f": {' -> '.join(paths)}" if paths else ""), hint
    if isinstance(error, JSONDecodeError):
        return f"invalid JSON: line {error.lineno}, column {error.colno}", "input: valid JSON"
    if isinstance(error, TOMLDecodeError):
        return "invalid TOML", "input: valid TOML"
    if isinstance(error, UnicodeError):
        return "invalid text encoding", "input: UTF-8 text"
    if isinstance(error, ValueError):
        message = str(error)
        if message.startswith(("invalid literal for int()", "could not convert string to float")):
            return "invalid numeric value", None
        return message or "invalid input", None
    if type(error).__module__ == "builtins" and not isinstance(error, RuntimeError):
        return f"internal command error: {type(error).__name__}", None
    return str(error) or type(error).__name__, None


@dataclass
class StatusReport:
    """Retain the failure reason across command output."""

    command: str = "command"
    reason: str = ""

    def finish(self):
        line("result", f"failed: {self.reason or self.command + ': checks failed'}", file=sys.stderr)


_REPORT = ContextVar("terminal_report", default=None)


def run_status(callback):
    """Report failures once at the command boundary."""
    report = StatusReport()
    token = _REPORT.set(report)
    try:
        try:
            status = callback(report)
        except SystemExit as exc:
            if exc.code is not None and exc.code != 0:
                if not report.reason:
                    report.reason = "invalid arguments" if exc.code == 2 else f"command exit: {exc.code or 1}"
                report.finish()
                if not isinstance(exc.code, int):
                    raise SystemExit(1) from None
            raise
        except KeyboardInterrupt:
            line("error", "interrupted", file=sys.stderr)
            status = 130
        except Exception as exc:
            line("error", exc, file=sys.stderr)
            status = 2 if isinstance(exc, CommandError) else 1
        if status:
            report.finish()
        return status
    finally:
        _REPORT.reset(token)


def line(label: str, value, *, file=None, width=12) -> None:
    """Keep each value on one terminal line."""
    hint, valid = None, None
    if label == "error" and isinstance(value, Exception):
        valid = getattr(value, "valid", None)
        value, hint = error_details(value)
    report = _REPORT.get()
    if report is not None and label == "error":
        report.reason = str(value)
    text = str(value).replace("\r", r"\r").replace("\n", r"\n").replace("\t", r"\t")
    print(f"{label:<{width}} {text}", file=file, flush=True)
    if valid is not None:
        line("valid", ", ".join(valid) or "none", file=file)
    if hint:
        line("hint", hint, file=file)


def table(headers, rows, *, file=None) -> None:
    """Align records with units in column headings."""
    records = [[str(value).replace("\r", r"\r").replace("\n", r"\n").replace("\t", r"\t")
                for value in row] for row in [headers, *rows]]
    if not records[0] or any(len(row) != len(records[0]) for row in records):
        raise ValueError("table column count mismatch")
    widths = [max(map(len, column)) for column in zip(*records)]
    for row in records:
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip(),
              file=file, flush=True)


class StatusVersion(argparse.Action):
    """Use the same formatter for version output."""

    def __call__(self, parser, namespace, values, option_string=None):
        from .. import __version__
        line("version", __version__)
        parser.exit()


class StatusParser(argparse.ArgumentParser):
    """Keep parser output consistent with command output."""

    help_defaults = None

    def parse_known_args(self, args=None, namespace=None):
        if args == [] and any(isinstance(action, argparse._SubParsersAction) and action.required
                              for action in self._actions):
            self.print_help(file=sys.stderr)
            self.error("command required")
        return super().parse_known_args(args, namespace)

    def format_help(self):
        out = io.StringIO()
        line("command", self.prog, file=out)
        if self.description:
            line("about", " ".join(self.description.split()), file=out)
        usage = " ".join(self.format_usage().removeprefix("usage: ").split())
        line("usage", usage, file=out)
        defaults = self.help_defaults(self) if self.help_defaults else {}
        formatter = self._get_formatter()
        arguments, options, commands = [], [], []
        for action in self._actions:
            if action.help == argparse.SUPPRESS:
                continue
            if isinstance(action, argparse._SubParsersAction):
                for choice in action._choices_actions:
                    if choice.help == argparse.SUPPRESS:
                        continue
                    name = choice.dest
                    child = action.choices[name]
                    commands.append((f"{self.prog} {name}", choice.help or child.description or ""))
                continue
            rows = options if action.option_strings else arguments
            name = " | ".join(action.option_strings)
            if action.nargs != 0:
                value = formatter._format_args(action, action.dest.upper())
                name = f"{name} {value}" if name else value
            rows.append((name, formatter._expand_help(action) if action.help else ""))
            if action.choices is not None:
                rows.extend(("choice", choice) for choice in action.choices)
            if action.nargs != 0 and not action.required:
                default = defaults.get(action.dest, action.default)
                if default != argparse.SUPPRESS:
                    rows.append(("default", "none" if default is None else '""' if default == "" else default))
        for rows in (arguments, options):
            width = max((len(label) for label, _ in rows), default=12)
            for label, value in rows:
                line(label, value, file=out, width=max(12, width))
        width = max((len(name) for name, _ in commands), default=0)
        for name, summary in commands:
            line("command", f"{name:<{width}} {' '.join(summary.split())}", file=out)
        return out.getvalue()

    def error(self, message):
        line("error", message, file=sys.stderr)
        self.exit(2)
