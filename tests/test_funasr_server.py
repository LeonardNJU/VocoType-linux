import wave

import numpy as np

from app.funasr_server import (
    FunASRServer,
    _empty_contextual_hotword_tensors,
    _is_contextual_onnx_model,
    _prepare_contextual_onnx_layout,
)


class _FailIfCalledASR:
    def __call__(self, _audio):
        raise AssertionError("ASR must not run for an audio file shorter than one frame")


class _RecordingASR:
    def __init__(self):
        self.calls = 0

    def __call__(self, _audio):
        self.calls += 1
        return [{"preds": "测试"}]


class _WaveformRecordingASR:
    def __init__(self):
        self.waveform = None
        self.frontend = type(
            "Frontend",
            (),
            {"opts": type("Opts", (), {"frame_opts": type("FrameOpts", (), {"samp_freq": 16000})()})()},
        )()

    def __call__(self, waveform):
        self.waveform = waveform
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
    server.asr_supports_hotword = False
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


def test_onnx_transcription_passes_numpy_waveform_without_librosa_loader(tmp_path):
    audio_path = tmp_path / "pcm.wav"
    _write_wav(audio_path, sample_count=800)
    model = _WaveformRecordingASR()
    server = _server_with_model(model)

    result = server.transcribe_audio(str(audio_path), options={"use_punc": False})

    assert result["success"] is True
    assert isinstance(model.waveform, np.ndarray)
    assert model.waveform.dtype == np.float32
    assert model.waveform.ndim == 1
    assert model.waveform.shape == (800,)


class _RecordingContextualASR:
    def __init__(self):
        self.hotwords = None

    def __call__(self, _audio, *, hotwords):
        self.hotwords = hotwords
        return [{"preds": "鬼斯提"}]


def test_contextual_model_detection():
    assert _is_contextual_onnx_model("iic/foo-contextual-onnx") is True
    assert _is_contextual_onnx_model("iic/foo-seaco-onnx") is True
    assert _is_contextual_onnx_model("iic/plain-paraformer-onnx") is False


def test_contextual_layout_links_quantized_backbone(tmp_path):
    (tmp_path / "model_quant.onnx").write_bytes(b"backbone")
    (tmp_path / "model_eb.onnx").write_bytes(b"embedding")

    assert _prepare_contextual_onnx_layout(str(tmp_path)) == str(tmp_path)
    assert (tmp_path / "model.onnx").exists()
    assert (tmp_path / "model.onnx").read_bytes() == b"backbone"


def test_empty_contextual_hotword_uses_only_sentinel():
    hotwords, lengths = _empty_contextual_hotword_tensors()
    assert hotwords.shape == (1, 10)
    assert hotwords[0, 0] == 1
    assert hotwords[0, 1:].tolist() == [0] * 9
    assert lengths.tolist() == [0]


def test_contextual_asr_receives_terms_and_explicit_hotwords(
    tmp_path, monkeypatch
):
    terms_path = tmp_path / "terms.yaml"
    terms_path.write_text(
        """
terms:
  - canonical: Ghostty
    aliases: [鬼斯提]
    hotword: true
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("VOCOTYPE_TERMS_FILE", str(terms_path))
    from app import term_lexicon

    term_lexicon._reset_term_lexicon_cache()
    audio_path = tmp_path / "audio.wav"
    _write_wav(audio_path, sample_count=400)
    model = _RecordingContextualASR()
    server = _server_with_model(model)
    server.asr_supports_hotword = True

    result = server.transcribe_audio(
        str(audio_path),
        options={"use_punc": False, "hotword": "VoCoType"},
    )

    assert model.hotwords == "Ghostty VoCoType"
    assert result["text"] == "Ghostty"
    term_lexicon._reset_term_lexicon_cache()
