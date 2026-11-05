"""Exercise the real owner CLI on synthetic material in a temporary POSIX directory."""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import signal
import sys
import tempfile
import time


def terminal(tool, args, responses=()):
    pid, fd = pty.fork()
    if pid == 0:
        os.execv(sys.executable, [sys.executable, str(tool), *map(str, args)])
    deadline = time.monotonic() + 30
    prompts = iter(responses)
    next_response = next(prompts, None)
    transcript = bytearray()
    consumed = 0
    reaped = False
    try:
        while time.monotonic() < deadline:
            if not select.select([fd], [], [], 0.25)[0]:
                continue
            try:
                chunk = os.read(fd, 8192)
            except OSError as error:
                if error.errno != errno.EIO:
                    raise
                break
            if not chunk:
                break
            transcript.extend(chunk)
            if len(transcript) > 32768:
                raise ValueError('unexpected terminal output size')
            if next_response:
                prompt, response = next_response
                position = transcript.find(prompt, consumed)
                if position >= 0:
                    consumed = position + len(prompt)
                    os.write(fd, response + b'\n')
                    next_response = next(prompts, None)
        else:
            raise TimeoutError('synthetic terminal deadline')
        while time.monotonic() < deadline:
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                reaped = True
                if next_response:
                    raise ValueError('expected terminal prompt not reached')
                return os.waitstatus_to_exitcode(status), bytes(transcript).replace(b'\r\n', b'\n')
            time.sleep(0.01)
        raise TimeoutError('synthetic process did not exit')
    finally:
        os.close(fd)
        if not reaped:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)


def receipt_from_terminal(transcript):
    start = transcript.index(b'{\n  "verified":')
    return json.loads(transcript[start:])


def run():
    tool = Path(__file__).resolve().with_name('lite_operational_materials.py')
    password = b'synthetic-cli-passphrase-only-2026'
    incorrect = b'synthetic-incorrect-passphrase-2026'
    transcripts = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        backup_dir, pin_dir = root / 'backup', root / 'independent-receipt'
        backup_dir.mkdir(mode=0o700)
        pin_dir.mkdir(mode=0o700)
        backup, pins = backup_dir / 'backup.json', pin_dir / 'pins.json'
        scope = root / 'scope.json'
        scope.write_text(json.dumps(dict(project='synthetic-project', environment='owner-sandbox',
            namespace='synthetic-native', audience='com.example.synthetic', issuer='https://appleid.apple.com',
            media_bucket='synthetic-media')))
        create = ['create-new', '--scope', scope, '--output', backup, '--receipt', pins]
        verify = ['verify', '--backup', backup, '--receipt', pins]
        code, transcript = terminal(tool, create, [(b'new installation only: ', b'CREATE_NEW_KEYS'),
            (b'Recovery passphrase (store separately): ', password), (b'Repeat recovery passphrase: ', password)])
        transcripts.append(transcript)
        assert code == 0, 'synthetic CLI create failed'
        first = receipt_from_terminal(transcript)
        original = backup.read_bytes(), pins.read_bytes()
        code, transcript = terminal(tool, verify, [(b'Recovery passphrase: ', password)])
        transcripts.append(transcript)
        assert code == 0 and receipt_from_terminal(transcript) == first
        code, transcript = terminal(tool, verify, [(b'Recovery passphrase: ', incorrect)])
        transcripts.append(transcript)
        assert code != 0 and b'authentication failed' in transcript
        code, transcript = terminal(tool, create)
        transcripts.append(transcript)
        assert code != 0 and b'destination already exists' in transcript
        altered = backup_dir / 'altered-backup.json'
        changed = json.loads(original[0])
        changed['ciphertext'] = ('A' if changed['ciphertext'][0] != 'A' else 'B') + changed['ciphertext'][1:]
        altered.write_text(json.dumps(changed))
        altered.chmod(0o600)
        code, transcript = terminal(tool, ['verify', '--backup', altered, '--receipt', pins])
        transcripts.append(transcript)
        assert code != 0 and b'backup and recovery receipt differ' in transcript and b'Recovery passphrase: ' not in transcript
        assert original == (backup.read_bytes(), pins.read_bytes())
        assert all(password not in item and incorrect not in item for item in transcripts)
        assert first['backup_sha256'] == hashlib.sha256(original[0]).hexdigest() and first['verified']
    return dict(schema='lite-operational-cli-probe/1', synthetic_only=True, processes=len(transcripts),
                create_and_restart_verified=True, wrong_passphrase_refused=True, overwrite_refused=True,
                altered_backup_refused_before_password=True, password_echoed=False, original_pair_preserved=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    destination = parser.parse_args().output
    if destination.exists():
        parser.error('choose a new evidence path')
    result = run()
    with destination.open('x', encoding='utf-8') as output:
        json.dump(result, output, indent=2)
        output.write('\n')
