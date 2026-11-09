"""Isolated Office conversion across one private Unix socket."""
import os
from pathlib import Path
import signal
import socketserver
from struct import pack, unpack
import subprocess

from convert_office_stdin import MAX_BYTES, TARGETS

SOCKET = Path('/run/office/convert.sock')
WORKERS = {kind: ('convert_office_stdin.py', kind) for kind in TARGETS}
WORKERS['doc'] = ('convert_doc_stdin.py',)


def convert(kind, content):
    worker, *args = WORKERS[kind]
    command = ['/usr/bin/python3', str(Path(__file__).with_name(worker)), *args]
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, start_new_session=True) as process:
        try:
            output, _ = process.communicate(content, timeout=120)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            return b''
        return output if process.returncode == 0 and 0 < len(output) <= MAX_BYTES else b''


class Request(socketserver.StreamRequestHandler):
    timeout = 10

    def handle(self):
        try:
            kind = self.rfile.readline(10).decode('ascii').strip()
            if kind == 'ping':
                self.wfile.write(b'office-v1\n')
                return
            header = self.rfile.read(4)
            if kind not in WORKERS or len(header) != 4:
                return
            size, = unpack('!I', header)
            if not 0 < size <= MAX_BYTES:
                return
            content = self.rfile.read(size)
            if len(content) != size:
                return
            output = convert(kind, content)
            self.wfile.write(pack('!I', len(output)))
            self.wfile.write(output)
        except (OSError, UnicodeError):
            return


if __name__ == '__main__':
    os.umask(0o077)
    if SOCKET.exists():
        assert SOCKET.is_socket(), 'CONVERTER_SOCKET_PATH_OCCUPIED'
        SOCKET.unlink()
    with socketserver.UnixStreamServer(str(SOCKET), Request) as service:
        service.serve_forever()
