import numpy as np
import sounddevice as sd

from app.audio_utils import resolve_default_input_device, resample_audio


def test_resolve_default_input_device_prefers_server_virtual_default(monkeypatch):
    devices = [
        {"name": "Built-in Mic", "max_input_channels": 2},
        {"name": "pulse", "max_input_channels": 32},
        {"name": "default", "max_input_channels": 32},
    ]
    monkeypatch.setattr(sd, "query_devices", lambda *args, **kwargs: devices)
    monkeypatch.setattr(sd.default, "device", (0, 0))

    assert resolve_default_input_device() == 2
    assert resolve_default_input_device(exclude=(2,)) == 1
    assert resolve_default_input_device(exclude=("default", "pulse")) == 0


def test_resolve_default_input_device_uses_portaudio_default(monkeypatch):
    devices = [
        {"name": "Built-in Mic", "max_input_channels": 2},
        {"name": "USB Mic", "max_input_channels": 1},
    ]
    monkeypatch.setattr(sd, "query_devices", lambda *args, **kwargs: devices)
    monkeypatch.setattr(sd.default, "device", (1, 0))

    assert resolve_default_input_device() == 1


def test_resample_audio_preserves_int16_and_expected_length():
    source = (np.sin(np.linspace(0, 20 * np.pi, 4410)) * 20000).astype(np.int16)

    result = resample_audio(source, orig_sr=44100, target_sr=16000)

    assert result.dtype == np.int16
    assert len(result) == 1600
    assert np.max(np.abs(result.astype(np.int32))) <= 32767


def test_resample_audio_rejects_invalid_sample_rates():
    source = np.zeros(100, dtype=np.int16)

    for orig_sr, target_sr in ((0, 0), (0, 16000), (44100, 0), (-1, 16000)):
        try:
            resample_audio(source, orig_sr=orig_sr, target_sr=target_sr)
        except ValueError as exc:
            assert "采样率必须为正整数" in str(exc)
        else:
            raise AssertionError("invalid sample rate must fail")
