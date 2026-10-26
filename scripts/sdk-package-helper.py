"""Owned pip gate. The parent attaches the Job Object before sending a command."""
import json
from pathlib import Path
import subprocess
import sys


def main():
    command = json.loads(sys.stdin.buffer.readline(65536))
    if (not isinstance(command, list) or len(command) < 4
            or any(not isinstance(v, str) for v in command) or not Path(command[0]).is_absolute()):
        return 2
    pip = command[1:3] == ['-m', 'pip']
    candidate = (len(command)==8 and command[1:3]==['-I','-B']
                 and Path(command[3]).resolve()==Path(__file__).with_name('prepare-workbuddy-package.py').resolve()
                 and command[4]=='--request' and Path(command[5]).is_absolute()
                 and command[6]=='--request-sha256' and len(command[7])==64)
    if not (pip or candidate):return 2
    return subprocess.run(command, stdin=subprocess.DEVNULL, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
