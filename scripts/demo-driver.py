#!/usr/bin/env python3
"""Drive the csm tui demo — sends keystrokes while the TUI runs in the foreground.

Usage (standalone test):
    uv run python scripts/demo-driver.py

Usage (with asciinema):
    asciinema rec --cols 120 --rows 36 --overwrite \
        --command "uv run python scripts/demo-driver.py" docs/demo.cast

Source tree (sandbox, plugins=false, all expanded by default):
  Line 0: Local (11)
  Line 1:   data-tools (4)
  Line 2:     ○ backtest
  Line 3:     ○ detect-regime
  Line 4:     ○ fetch-data
  Line 5:     ○ risk-monitor
  Line 6:   dev-tools (3)
  Line 7:     ○ code-review
  Line 8:     ○ db-migrate
  Line 9:     ○ deploy-helper
  Line 10:  productivity (3)
  Line 11:    ○ dream-journal
  Line 12:    ● meeting-prep      (installed in user + daily-planner)
  Line 13:    ○ weekly-digest

Target tree:
  Line 0: User scope (~/.claude) 1
  Line 1: ● Project scope 6
  Line 2:   ▼ projects
  Line 3:     daily-planner 1
  Line 4:     my-website 1
  Line 5:     quant-lab 3+1?
  Line 6:     research-notes 0
"""

import os
import pty
import select
import sys
import time
import subprocess
import struct
import fcntl
import termios

ROWS, COLS = 42, 130


def main():
    master_fd, slave_fd = pty.openpty()
    winsize = struct.pack("HHHH", ROWS, COLS, 0, 0)
    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

    proc = subprocess.Popen(
        [sys.executable, "scripts/demo-sandbox.py"],
        stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)

    def send(key: str, delay: float = 0.5):
        os.write(master_fd, key.encode())
        drain(delay)

    import re
    # Filter out terminal query responses that leak after TUI exits
    _ansi_query = re.compile(rb'\x1b\[\?[0-9;]*\$y|\x1b\[\?[0-9;]*[a-zA-Z]|\x1b\[I')

    def drain(seconds: float):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            r, _, _ = select.select([master_fd], [], [], min(remaining, 0.05))
            if r:
                try:
                    data = os.read(master_fd, 4096)
                    if data:
                        # Filter terminal query responses
                        clean = _ansi_query.sub(b'', data)
                        if clean:
                            os.write(sys.stdout.fileno(), clean)
                except OSError:
                    break

    def caption():
        """Send Ctrl+G to advance to next caption."""
        send("\x07", 0.3)

    # Wait for TUI to render
    drain(5)

    # ═══ Scene 1: Browse sources ═════════════════════════════
    caption()  # "j/k to browse skills"
    for _ in range(6):
        send("j", 0.3)
    for _ in range(3):
        send("k", 0.3)
    drain(1)

    # ═══ Scene 2: Browse targets ═════════════════════════════
    caption()  # "Tab → targets panel"
    send("\t", 0.8)
    for _ in range(7):
        send("j", 0.4)
    drain(1)

    # ═══ Scene 3: Select "research-notes" (empty project) ════
    caption()  # "Space → select empty target"
    send(" ", 1.5)

    # ═══ Scene 4: Toggle install skills ══════════════════════
    caption()  # "x → toggle install"
    send("j", 0.4)
    send("j", 0.4)
    send("x", 0.6)
    send("j", 0.4)
    send("j", 0.4)
    send("x", 0.6)
    drain(1)

    # ═══ Scene 5: Pending changes ════════════════════════════
    caption()  # "Pending changes"
    send("\t", 0.5)
    send("\t", 1.5)

    # ═══ Scene 6: Apply ══════════════════════════════════════
    caption()  # "a → apply, Enter → confirm"
    send("a", 2)
    send("\r", 3)
    drain(1.5)

    # ═══ Scene 7: Help ═══════════════════════════════════════
    caption()  # "? → help"
    send("?", 3)
    send("\x1b", 0.8)

    # ═══ Scene 8: Settings ═══════════════════════════════════
    caption()  # "s → settings"
    send("s", 3)
    send("\x1b", 0.8)

    # ═══ Scene 9: Quit ═══════════════════════════════════════
    drain(0.5)
    # Escape any modal, then quit
    for key in ["\x1b", "\x1b", "\t", "q"]:
        try:
            os.write(master_fd, key.encode())
            time.sleep(0.3)
        except OSError:
            break

    # Wait for process to exit
    for _ in range(20):
        if proc.poll() is not None:
            break
        # Drain output silently
        r, _, _ = select.select([master_fd], [], [], 0.2)
        if r:
            try:
                os.read(master_fd, 4096)
            except OSError:
                break
    else:
        proc.terminate()
        proc.wait()

    # Close PTY
    try:
        os.close(master_fd)
    except OSError:
        pass

    # Wait for any straggling terminal responses to arrive, then discard
    time.sleep(0.3)
    # Flush stdin (discard leaked escape responses)
    os.system("stty sane 2>/dev/null")
    import tty
    try:
        old = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin)
        while True:
            r, _, _ = select.select([sys.stdin], [], [], 0.1)
            if not r:
                break
            os.read(sys.stdin.fileno(), 4096)  # discard
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
    except (termios.error, OSError):
        pass
    # Full terminal reset
    os.system("printf '\\033[0m\\033[?25h\\033c' 2>/dev/null")


if __name__ == "__main__":
    main()
