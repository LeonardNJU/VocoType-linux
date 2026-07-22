#include "vocotype/desktop/audio.hpp"
#include <algorithm>
#include <cmath>
#include <portaudio.h>
#include <stdexcept>
#include <utility>
namespace vocotype::desktop {
namespace {
void check(PaError error, const char *operation) {
  if (error != paNoError)
    throw std::runtime_error(std::string(operation) + ": " +
                             Pa_GetErrorText(error));
}
} // namespace
PortAudioRuntime::PortAudioRuntime() {
  check(Pa_Initialize(), "PortAudio initialization failed");
}
PortAudioRuntime::~PortAudioRuntime() { (void)Pa_Terminate(); }
std::vector<AudioDevice> list_input_devices() {
  PortAudioRuntime runtime;
  const int count = Pa_GetDeviceCount();
  if (count < 0)
    check(count, "cannot enumerate audio devices");
  const PaDeviceIndex default_id = Pa_GetDefaultInputDevice();
  std::vector<AudioDevice> result;
  for (int id = 0; id < count; ++id) {
    const PaDeviceInfo *info = Pa_GetDeviceInfo(id);
    if (!info || info->maxInputChannels <= 0)
      continue;
    result.push_back({id, info->name ? info->name : "Unknown input",
                      info->maxInputChannels,
                      static_cast<int>(std::lround(info->defaultSampleRate)),
                      id == default_id});
  }
  return result;
}
AudioDevice resolve_input_device(const AudioConfig &config) {
  const auto devices = list_input_devices();
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
int resolve_sample_rate(const AudioDevice &device, int preferred_rate) {
  PortAudioRuntime runtime;
  const PaDeviceInfo *info = Pa_GetDeviceInfo(device.id);
  if (!info)
    throw std::runtime_error("selected audio device disappeared");
  PaStreamParameters parameters{};
  parameters.device = device.id;
  parameters.channelCount = 1;
  parameters.sampleFormat = paInt16;
  parameters.suggestedLatency = info->defaultLowInputLatency;
  if (preferred_rate > 0 &&
      Pa_IsFormatSupported(&parameters, nullptr, preferred_rate) ==
          paFormatIsSupported)
    return preferred_rate;
  if (Pa_IsFormatSupported(&parameters, nullptr, device.default_sample_rate) ==
      paFormatIsSupported)
    return device.default_sample_rate;
  for (int rate : {48000, 44100, 32000, 16000}) {
    if (Pa_IsFormatSupported(&parameters, nullptr, rate) == paFormatIsSupported)
      return rate;
  }
  throw std::runtime_error(
      "audio device has no supported mono PCM16 sample rate");
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
        std::clamp<long double>(std::llround(sample), -32768.0L, 32767.0L));
  }
  return output;
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
  PortAudioRuntime runtime;
  const PaDeviceInfo *info = Pa_GetDeviceInfo(device_.id);
  if (!info)
    throw std::runtime_error("selected audio device disappeared");
  PaStreamParameters input{};
  input.device = device_.id;
  input.channelCount = 1;
  input.sampleFormat = paInt16;
  input.suggestedLatency = info->defaultLowInputLatency;
  const unsigned long frames =
      static_cast<unsigned long>(std::max(64, sample_rate_ * block_ms_ / 1000));
  PaStream *stream = nullptr;
  check(Pa_OpenStream(&stream, &input, nullptr, sample_rate_, frames, paClipOff,
                      nullptr, nullptr),
        "cannot open microphone");
  stream_ = stream;
  check(Pa_StartStream(stream), "cannot start microphone");
  std::vector<std::int16_t> block(frames);
  while (!stop.load(std::memory_order_relaxed)) {
    const PaError error = Pa_ReadStream(stream, block.data(), frames);
    if (error == paInputOverflowed)
      continue;
    check(error, "microphone read failed");
    callback(block);
  }
  check(Pa_StopStream(stream), "cannot stop microphone");
  check(Pa_CloseStream(stream), "cannot close microphone");
  stream_ = nullptr;
}
} // namespace vocotype::desktop
