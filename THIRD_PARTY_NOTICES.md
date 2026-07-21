# Third-Party Notices

This project depends on third-party software and models. Each component is
covered by its own license; consult the upstream project for the authoritative
license text.

Runtime dependencies (from requirements.txt / pyproject.toml):
- sounddevice 0.5.2 - https://github.com/spatialaudio/python-sounddevice
- NumPy 1.26.4 - https://numpy.org/
- SciPy 1.16.3 - https://scipy.org/
- soundfile 0.13.1 - https://github.com/bastibe/python-soundfile
- funasr_onnx 0.4.2 - https://github.com/modelscope/FunASR
- jieba 0.42.1 - https://github.com/fxsjy/jieba
- PyGObject >=3.46, <3.52 - https://pygobject.gnome.org/
- modelscope 1.30.0 - https://github.com/modelscope/modelscope

Models (downloaded via ModelScope):
- iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-onnx
- iic/speech_fsmn_vad_zh-cn-16k-common-onnx
- iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx

Check the corresponding model cards and licenses on ModelScope before
redistribution or commercial use.

`funasr_onnx` may declare additional decoder packages transitively. VoCoType itself decodes with soundfile, resamples with SciPy, and passes NumPy waveforms directly to the ONNX model.
