import wave

import numpy as np

from app.funasr_server import FunASRServer


class _FailIfCalledASR:
    def __call__(self, _audio):
        raise AssertionError("ASR must not run for an audio file shorter than one frame")


class _RecordingASR:
    def __init__(self):
        self.calls = 0

    def __call__(self, _audio):
        self.calls += 1
        return [{"preds": "测试"}]


def _write_wav(path, sample_count: int, sample_rate: int = 16000) -> None:
    samples = np.zeros(sample_count, dtype=np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(samples.tobytes())


def _server_with_model(model):
    server = FunASRServer.__new__(FunASRServer)
    server.asr_model = model
    server.vad_model = None
    server.punc_model = None
    server.initialized = True
    server.transcription_count = 0
    server.total_audio_duration = 0.0
    server.model_names = {
        "asr": "test-onnx",
        "vad": "test-vad",
        "punc": "test-punc",
    }
    return server


def test_short_audio_returns_empty_result_without_entering_funasr(tmp_path):
    audio_path = tmp_path / "too-short.wav"
    _write_wav(audio_path, sample_count=399)
    server = _server_with_model(_FailIfCalledASR())

    result = server.transcribe_audio(str(audio_path), options={"use_punc": False})

    assert result["success"] is True
    assert result["text"] == ""
    assert result["raw_text"] == ""
    assert result["reason"] == "audio_too_short"
    assert result["duration"] == 399 / 16000
    assert server.transcription_count == 1


def test_one_frontend_window_still_reaches_asr(tmp_path):
    audio_path = tmp_path / "one-window.wav"
    _write_wav(audio_path, sample_count=400)
    model = _RecordingASR()
    server = _server_with_model(model)

    result = server.transcribe_audio(str(audio_path), options={"use_punc": False})

    assert model.calls == 1
    assert result["success"] is True
    assert result["raw_text"] == "测试"
