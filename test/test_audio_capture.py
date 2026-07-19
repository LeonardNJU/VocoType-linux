from app.audio_capture import AudioCapture


def test_audio_capture_retries_with_distinct_fallback_device(monkeypatch):
    resolve_calls = []

    def fake_resolve_default_input_device(*, exclude=()):
        resolve_calls.append(exclude)
        return 5 if not exclude else 6

    monkeypatch.setattr(
        "app.audio_capture.resolve_default_input_device",
        fake_resolve_default_input_device,
    )

    streams = []

    class FakeStream:
        def __init__(self, device):
            self.device = device
            self.closed = False
            streams.append(self)

        def start(self):
            if self.device == 5:
                raise RuntimeError("primary failed")

        def close(self):
            self.closed = True

        def stop(self):
            pass

    capture = AudioCapture(sample_rate=16000, block_ms=20)
    monkeypatch.setattr(capture, "_create_stream", lambda device: FakeStream(device))

    capture.start()

    assert resolve_calls == [(), (5,)]
    assert [stream.device for stream in streams] == [5, 6]
    assert streams[0].closed is True
    assert capture._stream is streams[1]
    assert capture._running is True
