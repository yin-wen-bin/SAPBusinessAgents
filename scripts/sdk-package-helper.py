"""Owned pip gate. The parent attaches the Job Object before sending a command."""
import json
from pathlib import Path
import subprocess
import sys


def main():
    command = json.loads(sys.stdin.buffer.readline(65536))
    if (not isinstance(command, list) or len(command) < 4 or command[1:3] != ['-m', 'pip']
            or not Path(command[0]).is_absolute() or any(not isinstance(v, str) for v in command)):
        return 2
    return subprocess.run(command, stdin=subprocess.DEVNULL, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
