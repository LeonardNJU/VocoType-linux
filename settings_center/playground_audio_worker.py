"""Private-runtime audio worker for the settings-center Playground."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping


def _emit(payload: Mapping[str, Any]) -> None:
    print(json.dumps(dict(payload), ensure_ascii=False), flush=True)


def _request() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("音频 worker 请求必须是 JSON 对象")
    return value


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: playground_audio_worker.py COMMAND", file=sys.stderr)
        return 64
    command = args[0]
    try:
        from . import playground_service as service

        if command == "probe":
            import sounddevice  # noqa: F401
            import soundfile  # noqa: F401

            _emit({"event": "result", "runtime": "ready"})
        elif command == "list-inputs":
            _emit(
                {
                    "event": "result",
                    "devices": [
                        asdict(item)
                        for item in service._list_input_devices_direct()
                    ],
                }
            )
        elif command == "list-outputs":
            _emit(
                {
                    "event": "result",
                    "devices": [
                        asdict(item)
                        for item in service._list_portaudio_outputs_direct()
                    ],
                }
            )
        elif command == "record":
            request = _request()

            def waveform_callback(
                envelope: tuple[tuple[float, float], ...],
            ) -> None:
                _emit({"event": "waveform", "envelope": envelope})

            recording = service._record_audio_direct(
                device_id=int(request["device_id"]),
                device_name=str(request.get("device_name") or ""),
                sample_rate=int(request["sample_rate"]),
                duration_seconds=float(request.get("duration_seconds", 3.0)),
                output_path=Path(str(request["output_path"])).expanduser(),
                waveform_callback=waveform_callback,
            )
            payload = asdict(recording)
            payload["path"] = str(recording.path)
            _emit({"event": "result", "recording": payload})
        elif command == "play":
            request = _request()
            raw_output = request.get("output_device")
            output = None
            if isinstance(raw_output, dict):
                output = service.OutputDevice(
                    device_id=str(raw_output.get("device_id") or ""),
                    name=str(raw_output.get("name") or ""),
                    backend=str(raw_output.get("backend") or "portaudio"),
                    is_default=bool(raw_output.get("is_default", False)),
                )
            result = service._play_recording_direct(
                Path(str(request["path"])).expanduser(),
                output_device=output,
            )
            _emit({"event": "result", "playback": asdict(result)})
        else:
            raise ValueError(f"未知音频 worker 命令：{command}")
    except Exception as exc:  # noqa: BLE001
        _emit(
            {
                "event": "error",
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
