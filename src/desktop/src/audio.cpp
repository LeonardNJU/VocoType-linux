#include "vocotype/desktop/audio.hpp"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <fcntl.h>
#include <mutex>
#include <optional>
#include <portaudio.h>
#include <stdexcept>
#include <unistd.h>
#include <utility>
namespace vocotype::desktop {
namespace {
void check(PaError error, const char *operation) {
  if (error != paNoError)
    throw std::runtime_error(std::string(operation) + ": " +
                             Pa_GetErrorText(error));
}

class ScopedStderrSilence {
public:
  ScopedStderrSilence() : lock_(redirect_mutex()) {
    std::fflush(stderr);
    saved_fd_ = dup(STDERR_FILENO);
    if (saved_fd_ < 0)
      return;
    const int null_fd = open("/dev/null", O_WRONLY | O_CLOEXEC);
    if (null_fd < 0 || dup2(null_fd, STDERR_FILENO) < 0) {
      if (null_fd >= 0)
        close(null_fd);
      close(saved_fd_);
      saved_fd_ = -1;
      return;
    }
    close(null_fd);
  }

  ~ScopedStderrSilence() {
    if (saved_fd_ < 0)
      return;
    std::fflush(stderr);
    (void)dup2(saved_fd_, STDERR_FILENO);
    close(saved_fd_);
  }

  ScopedStderrSilence(const ScopedStderrSilence &) = delete;
  ScopedStderrSilence &operator=(const ScopedStderrSilence &) = delete;

private:
  static std::mutex &redirect_mutex() {
    static std::mutex mutex;
    return mutex;
  }

  std::unique_lock<std::mutex> lock_;
  int saved_fd_ = -1;
};

AudioDeviceInventory list_audio_devices_initialized() {
  const int count = Pa_GetDeviceCount();
  if (count < 0)
    check(count, "cannot enumerate audio devices");

  const PaDeviceIndex default_input = Pa_GetDefaultInputDevice();
  const PaDeviceIndex default_output = Pa_GetDefaultOutputDevice();
  AudioDeviceInventory inventory;
  for (int id = 0; id < count; ++id) {
    const PaDeviceInfo *info = Pa_GetDeviceInfo(id);
    if (!info)
      continue;
    if (info->maxInputChannels > 0) {
      inventory.inputs.push_back(
          {id, info->name ? info->name : "Unknown input",
           info->maxInputChannels,
           static_cast<int>(std::lround(info->defaultSampleRate)),
           id == default_input});
    }
    if (info->maxOutputChannels > 0) {
      inventory.outputs.push_back(
          {id, info->name ? info->name : "Unknown output",
           info->maxOutputChannels,
           static_cast<int>(std::lround(info->defaultSampleRate)),
           id == default_output});
    }
  }
  return inventory;
}

AudioDevice resolve_input_device_initialized(const AudioConfig &config) {
  const auto devices = list_audio_devices_initialized().inputs;
  if (!config.device_name.empty()) {
    auto exact =
        std::find_if(devices.begin(), devices.end(), [&](const auto &device) {
          return device.name == config.device_name;
        });
    if (exact != devices.end())
      return *exact;
    auto partial =
        std::find_if(devices.begin(), devices.end(), [&](const auto &device) {
          return device.name.find(config.device_name) != std::string::npos ||
                 config.device_name.find(device.name) != std::string::npos;
        });
    if (partial != devices.end())
      return *partial;
  }
  if (config.device_id) {
    auto found =
        std::find_if(devices.begin(), devices.end(), [&](const auto &device) {
          return device.id == *config.device_id;
        });
    if (found != devices.end())
      return *found;
  }
  auto preferred =
      std::find_if(devices.begin(), devices.end(),
                   [](const auto &device) { return device.is_default; });
  if (preferred != devices.end())
    return *preferred;
  if (!devices.empty())
    return devices.front();
  throw std::runtime_error("no audio input device is available");
}

AudioDevice resolve_current_input(const AudioDevice &selected) {
  AudioConfig config;
  config.device_name = selected.name;
  config.device_id = selected.id;
  return resolve_input_device_initialized(config);
}

AudioOutputDevice resolve_output_device_initialized(int preferred_id) {
  const auto devices = list_audio_devices_initialized().outputs;
  if (preferred_id >= 0) {
    auto found =
        std::find_if(devices.begin(), devices.end(), [&](const auto &device) {
          return device.id == preferred_id;
        });
    if (found != devices.end())
      return *found;
  }
  auto preferred =
      std::find_if(devices.begin(), devices.end(),
                   [](const auto &device) { return device.is_default; });
  if (preferred != devices.end())
    return *preferred;
  if (!devices.empty())
    return devices.front();
  throw std::runtime_error("no audio output device is available");
}

AudioOutputDevice
resolve_current_output(const AudioOutputDevice &selected) {
  const auto devices = list_audio_devices_initialized().outputs;
  if (!selected.name.empty()) {
    auto exact =
        std::find_if(devices.begin(), devices.end(), [&](const auto &device) {
          return device.name == selected.name;
        });
    if (exact != devices.end())
      return *exact;
    auto partial =
        std::find_if(devices.begin(), devices.end(), [&](const auto &device) {
          return device.name.find(selected.name) != std::string::npos ||
                 selected.name.find(device.name) != std::string::npos;
        });
    if (partial != devices.end())
      return *partial;
  }
  return resolve_output_device_initialized(selected.id);
}

std::vector<int> sample_rate_candidates(int preferred, int device_default) {
  std::vector<int> candidates;
  for (const int rate : {preferred, device_default, 48000, 44100, 32000,
                         16000}) {
    if (rate > 0 &&
        std::find(candidates.begin(), candidates.end(), rate) ==
            candidates.end())
      candidates.push_back(rate);
  }
  return candidates;
}

bool input_format_supported(const AudioDevice &device, int channels, int rate) {
  const PaDeviceInfo *info = Pa_GetDeviceInfo(device.id);
  if (!info || channels <= 0 || channels > info->maxInputChannels)
    return false;
  PaStreamParameters parameters{};
  parameters.device = device.id;
  parameters.channelCount = channels;
  parameters.sampleFormat = paInt16;
  parameters.suggestedLatency = info->defaultLowInputLatency;
  return Pa_IsFormatSupported(&parameters, nullptr, rate) ==
         paFormatIsSupported;
}

bool output_format_supported(const AudioOutputDevice &device, int channels,
                             int rate) {
  const PaDeviceInfo *info = Pa_GetDeviceInfo(device.id);
  if (!info || channels <= 0 || channels > info->maxOutputChannels)
    return false;
  PaStreamParameters parameters{};
  parameters.device = device.id;
  parameters.channelCount = channels;
  parameters.sampleFormat = paInt16;
  parameters.suggestedLatency = info->defaultLowOutputLatency;
  return Pa_IsFormatSupported(nullptr, &parameters, rate) ==
         paFormatIsSupported;
}

std::vector<std::int16_t>
downmix_to_mono(const std::vector<std::int16_t> &input, int channels) {
  if (channels <= 0)
    throw std::invalid_argument("audio channel count must be positive");
  if (input.size() % static_cast<std::size_t>(channels) != 0)
    throw std::invalid_argument("interleaved audio contains an incomplete frame");
  if (channels == 1)
    return input;

  const std::size_t frames = input.size() / static_cast<std::size_t>(channels);
  std::vector<std::int16_t> output(frames);
  for (std::size_t frame = 0; frame < frames; ++frame) {
    std::int64_t sum = 0;
    for (int channel = 0; channel < channels; ++channel) {
      sum += input[frame * static_cast<std::size_t>(channels) +
                   static_cast<std::size_t>(channel)];
    }
    output[frame] = static_cast<std::int16_t>(sum / channels);
  }
  return output;
}
} // namespace
PortAudioRuntime::PortAudioRuntime() {
  check(Pa_Initialize(), "PortAudio initialization failed");
}
PortAudioRuntime::~PortAudioRuntime() { (void)Pa_Terminate(); }
AudioDeviceInventory list_audio_devices() {
  // PortAudio probes every compiled backend during initialization. ALSA and
  // JACK print expected diagnostics for unavailable virtual devices directly
  // to stderr even when usable devices are found. Keep those backend-probe
  // messages out of the settings terminal while preserving PaError failures.
  ScopedStderrSilence silence;
  PortAudioRuntime runtime;
  return list_audio_devices_initialized();
}

std::vector<AudioDevice> list_input_devices() {
  return list_audio_devices().inputs;
}

std::vector<AudioOutputDevice> list_output_devices() {
  return list_audio_devices().outputs;
}

AudioDevice resolve_input_device(const AudioConfig &config) {
  ScopedStderrSilence silence;
  PortAudioRuntime runtime;
  return resolve_input_device_initialized(config);
}
AudioOutputDevice resolve_output_device(int preferred_id) {
  ScopedStderrSilence silence;
  PortAudioRuntime runtime;
  return resolve_output_device_initialized(preferred_id);
}
int resolve_sample_rate(const AudioDevice &device, int preferred_rate) {
  ScopedStderrSilence silence;
  PortAudioRuntime runtime;
  const AudioDevice current = resolve_current_input(device);
  for (const int rate :
       sample_rate_candidates(preferred_rate, current.default_sample_rate)) {
    for (const int channels : {1, 2}) {
      if (input_format_supported(current, channels, rate))
        return rate;
    }
  }
  throw std::runtime_error(
      "audio device has no supported mono or stereo PCM16 sample rate");
}
std::vector<std::int16_t>
resample_linear(const std::vector<std::int16_t> &input, int input_rate,
                int output_rate) {
  if (input.empty() || input_rate <= 0 || output_rate <= 0)
    return {};
  if (input_rate == output_rate)
    return input;
  const std::size_t output_size = static_cast<std::size_t>(std::llround(
      static_cast<long double>(input.size()) * output_rate / input_rate));
  std::vector<std::int16_t> output(output_size);
  if (output_size == 1 || input.size() == 1) {
    std::fill(output.begin(), output.end(), input.front());
    return output;
  }
  const long double scale = static_cast<long double>(input.size() - 1) /
                            static_cast<long double>(output_size - 1);
  for (std::size_t index = 0; index < output_size; ++index) {
    const long double position = static_cast<long double>(index) * scale;
    const auto left = static_cast<std::size_t>(position);
    const auto right = std::min(left + 1, input.size() - 1);
    const long double fraction = position - static_cast<long double>(left);
    const long double sample =
        static_cast<long double>(input[left]) * (1.0L - fraction) +
        static_cast<long double>(input[right]) * fraction;
    output[index] = static_cast<std::int16_t>(
        std::clamp<long double>(static_cast<long double>(std::llround(sample)),
                                -32768.0L, 32767.0L));
  }
  return output;
}
void play_pcm16(const std::vector<std::int16_t> &samples, int sample_rate,
                const AudioOutputDevice &device) {
  if (samples.empty() || sample_rate <= 0)
    throw std::invalid_argument("cannot play empty or invalid audio");

  std::optional<PortAudioRuntime> runtime;
  AudioOutputDevice current;
  int output_rate = 0;
  int output_channels = 0;
  {
    ScopedStderrSilence silence;
    runtime.emplace();
    current = resolve_current_output(device);
    for (const int rate :
         sample_rate_candidates(sample_rate, current.default_sample_rate)) {
      for (const int channels : {1, 2}) {
        if (output_format_supported(current, channels, rate)) {
          output_rate = rate;
          output_channels = channels;
          break;
        }
      }
      if (output_channels > 0)
        break;
    }
  }
  if (output_channels == 0)
    throw std::runtime_error(
        "selected output does not support mono or stereo PCM16 playback");

  const auto mono = output_rate == sample_rate
                        ? samples
                        : resample_linear(samples, sample_rate, output_rate);
  std::vector<std::int16_t> rendered;
  if (output_channels == 1) {
    rendered = mono;
  } else {
    rendered.reserve(mono.size() * static_cast<std::size_t>(output_channels));
    for (const auto sample : mono) {
      for (int channel = 0; channel < output_channels; ++channel)
        rendered.push_back(sample);
    }
  }

  const PaDeviceInfo *info = Pa_GetDeviceInfo(current.id);
  if (!info)
    throw std::runtime_error("selected audio output disappeared");
  PaStreamParameters output{};
  output.device = current.id;
  output.channelCount = output_channels;
  output.sampleFormat = paInt16;
  output.suggestedLatency = info->defaultLowOutputLatency;
  PaStream *stream = nullptr;
  check(Pa_OpenStream(&stream, nullptr, &output, output_rate,
                      paFramesPerBufferUnspecified, paClipOff, nullptr,
                      nullptr),
        "cannot open audio output");
  try {
    check(Pa_StartStream(stream), "cannot start audio output");
    check(Pa_WriteStream(stream, rendered.data(),
                         static_cast<unsigned long>(mono.size())),
          "audio playback failed");
    check(Pa_StopStream(stream), "cannot stop audio output");
    check(Pa_CloseStream(stream), "cannot close audio output");
  } catch (...) {
    (void)Pa_AbortStream(stream);
    (void)Pa_CloseStream(stream);
    throw;
  }
}
AudioCapture::AudioCapture(AudioDevice device, int sample_rate, int block_ms)
    : device_(std::move(device)), sample_rate_(sample_rate),
      block_ms_(block_ms) {}
AudioCapture::~AudioCapture() {
  if (stream_) {
    auto *stream = static_cast<PaStream *>(stream_);
    (void)Pa_AbortStream(stream);
    (void)Pa_CloseStream(stream);
  }
}
void AudioCapture::run(std::atomic_bool &stop, const BlockCallback &callback) {
  std::optional<PortAudioRuntime> runtime;
  AudioDevice current;
  int input_channels = 0;
  {
    ScopedStderrSilence silence;
    runtime.emplace();
    current = resolve_current_input(device_);
    for (const int channels : {1, 2}) {
      if (input_format_supported(current, channels, sample_rate_)) {
        input_channels = channels;
        break;
      }
    }
  }
  if (input_channels == 0)
    throw std::runtime_error(
        "selected microphone does not support mono or stereo PCM16 capture at " +
        std::to_string(sample_rate_) + " Hz");

  const PaDeviceInfo *info = Pa_GetDeviceInfo(current.id);
  if (!info)
    throw std::runtime_error("selected audio device disappeared");
  PaStreamParameters input{};
  input.device = current.id;
  input.channelCount = input_channels;
  input.sampleFormat = paInt16;
  input.suggestedLatency = info->defaultLowInputLatency;
  const unsigned long frames =
      static_cast<unsigned long>(std::max(64, sample_rate_ * block_ms_ / 1000));
  PaStream *stream = nullptr;
  check(Pa_OpenStream(&stream, &input, nullptr, sample_rate_, frames, paClipOff,
                      nullptr, nullptr),
        "cannot open microphone");
  stream_ = stream;
  try {
    check(Pa_StartStream(stream), "cannot start microphone");
    std::vector<std::int16_t> interleaved(
        static_cast<std::size_t>(frames) *
        static_cast<std::size_t>(input_channels));
    while (!stop.load(std::memory_order_relaxed)) {
      const PaError error = Pa_ReadStream(stream, interleaved.data(), frames);
      if (error == paInputOverflowed)
        continue;
      check(error, "microphone read failed");
      if (input_channels == 1)
        callback(interleaved);
      else {
        const auto mono = downmix_to_mono(interleaved, input_channels);
        callback(mono);
      }
    }
    check(Pa_StopStream(stream), "cannot stop microphone");
    check(Pa_CloseStream(stream), "cannot close microphone");
    stream_ = nullptr;
  } catch (...) {
    (void)Pa_AbortStream(stream);
    (void)Pa_CloseStream(stream);
    stream_ = nullptr;
    throw;
  }
}
} // namespace vocotype::desktop
