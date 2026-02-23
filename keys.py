#!/usr/bin/env python3
"""Run this to see raw bytes sent by any keypress. Ctrl+C to quit."""
import os, sys, termios, tty

fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setraw(fd)
sys.stdout.write("Press keys (Ctrl+C to quit):\r\n")
sys.stdout.flush()
try:
    while True:
        ch = os.read(fd, 32)
        if ch == b"\x03":
            break
        sys.stdout.write(repr(ch) + "\r\n")
        sys.stdout.flush()
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
