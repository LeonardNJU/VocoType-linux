"""Real recorded-speech inference; deliberately separate from test doubles."""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
import urllib.request
import wave


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((Path(__file__).parent / 'speech-fixture.json').read_text())
    fixture = args.config.parent / '公开中文语音.wav'
    with urllib.request.urlopen(manifest['url'], timeout=60) as response:
        data = response.read(2 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != manifest['sha256']:
        raise RuntimeError('Public speech fixture checksum mismatch')
    fixture.write_bytes(data)
    # This is the shipping CLI, shipping shared core, and real native decoder.
    run = subprocess.run(
        [str(args.bundle / 'vocotype-windows.exe'), '--config', str(args.config),
         '--transcribe', str(fixture)],
        capture_output=True, text=True, encoding='utf-8', timeout=240,
    )
    if run.returncode:
        raise RuntimeError(f'Real offline recognition failed: {run.stdout}\n{run.stderr}')
    offline = json.loads(run.stdout)
    if not offline.get('success') or not offline.get('text', '').strip():
        raise RuntimeError(f'Real offline recognition returned no text: {offline}')
    print('REAL_OFFLINE_ASR', json.dumps(offline, ensure_ascii=False), flush=True)

    # The preview uses the same production JSONL core transport and model.
    process = subprocess.Popen(
        [str(args.bundle / 'vocotype-core.exe'), '--config', str(args.config),
         '--role', 'preview'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1,
    )
    lines: queue.Queue[str | None] = queue.Queue()
    def read_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)
        lines.put(None)
    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    def receive(timeout: int = 30) -> dict:
        line = lines.get(timeout=timeout)
        if line is None:
            raise RuntimeError(f'Native streaming core exited: {process.poll()}')
        response = json.loads(line)
        if not response.get('success'):
            raise RuntimeError(f'Native streaming error: {response}')
        return response
    def request(value: dict) -> dict:
        assert process.stdin is not None
        process.stdin.write(json.dumps(value, ensure_ascii=False) + '\n')
        process.stdin.flush()
        return receive()
    try:
        receive(210)
        session = request({'type': 'asr_preview_start'})
        session_id = session['session_id']
        chunk = session.get('chunk_samples', 9600)
        with wave.open(str(fixture), 'rb') as source:
            if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (16000, 1, 2):
                raise RuntimeError('Fixture must be 16kHz mono PCM16')
            pcm = source.readframes(source.getnframes())
        texts = []
        for offset in range(0, len(pcm), chunk * 2):
            block = pcm[offset:offset + chunk * 2]
            response = request({'type': 'asr_preview_feed', 'session_id': session_id,
                                'pcm16': base64.b64encode(block).decode('ascii'),
                                'is_final': offset + len(block) == len(pcm)})
            if response.get('text', '').strip():
                texts.append(response['text'])
        request({'type': 'asr_preview_close', 'session_id': session_id, 'flush': False})
        if not texts:
            raise RuntimeError('Real streaming ASR returned no partial text')
        report = {'offline': offline, 'streaming_updates': len(texts),
                  'streaming_text': texts[-1], 'fixture_sha256': manifest['sha256'],
                  'physical_microphone_tested': False}
        (args.config.parent / 'real-asr-report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('REAL_STREAMING_ASR', json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            # The native core owns a kill-on-close job for its decoder tree.
            process.kill()
            process.wait(timeout=3)
        reader.join(timeout=3)


if __name__ == '__main__':
    main()
