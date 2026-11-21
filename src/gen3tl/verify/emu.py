"""Client for the mGBA Lua bridge (src/gen3tl/verify/emu_bridge.lua)."""

from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

MGBA_APP = Path("/Applications/mGBA.app/Contents/MacOS/mGBA")
BRIDGE = Path(__file__).with_name("emu_bridge.lua")


class EmuError(RuntimeError):
    pass


class Emu:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765, timeout: float = 120.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.file = self.sock.makefile("rw", encoding="utf-8", newline="\n")

    def cmd(self, line: str) -> str:
        self.file.write(line + "\n")
        self.file.flush()
        ans = self.file.readline().strip()
        if ans.startswith("error"):
            raise EmuError(f"{line}: {ans}")
        return ans

    def frames(self, n: int) -> None:
        self.cmd(f"frames {n}")

    def press(self, keys: str, n: int = 4, after: int = 8) -> None:
        self.cmd(f"press {keys} {n}")
        if after:
            self.frames(after)

    def shot(self, path: str | Path) -> Path:
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.cmd(f"shot {path}")
        return path

    def save_state(self, path: str | Path) -> None:
        self.cmd(f"save {Path(path).resolve()}")

    def load_state(self, path: str | Path) -> None:
        self.cmd(f"load {Path(path).resolve()}")

    def read32(self, addr: int) -> int:
        return int(self.cmd(f"r32 {addr:x}"))

    def close(self) -> None:
        self.sock.close()


def launch(rom: Path, scale: int = 3) -> subprocess.Popen:
    """Start mGBA with a ROM; the bridge script is loaded from the Scripting window."""
    return subprocess.Popen([str(MGBA_APP), f"-{scale}", str(rom)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for_bridge(timeout: float = 60.0, port: int = 8765) -> Emu:
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            emu = Emu(port=port, timeout=30)
            emu.cmd("ping")
            return emu
        except OSError as e:
            last = e
            time.sleep(1)
    raise EmuError(f"bridge not reachable on port {port}: {last}")


def load_bridge_via_ui() -> str:
    """Open Tools > Scripting and load the bridge through macOS UI scripting."""
    script = f'''
tell application "mGBA" to activate
delay 1
tell application "System Events"
  tell process "mGBA"
    if not (exists window "Scripting") then
      click menu item "Scripting..." of menu "Tools" of menu bar 1
      delay 1.5
    end if
    perform action "AXRaise" of window "Scripting"
    delay 0.5
    click menu item "Load script..." of menu "File" of menu bar 1
    delay 1.5
    keystroke "g" using {{command down, shift down}}
    delay 1
    keystroke "{BRIDGE}"
    delay 0.5
    key code 36
    delay 1
    key code 36
  end tell
end tell
'''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()
