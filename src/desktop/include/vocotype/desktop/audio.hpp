#pragma once
#include "vocotype/desktop/config.hpp"
#include <atomic>
#include <cstdint>
#include <functional>
#include <string>
#include <vector>
namespace vocotype::desktop {
struct AudioDevice {
  int id = -1;
  std::string name;
  int max_input_channels = 0;
  int default_sample_rate = 16000;
  bool is_default = false;
};
struct AudioOutputDevice {
  int id = -1;
  std::string name;
  int max_output_channels = 0;
  int default_sample_rate = 48000;
  bool is_default = false;
};
struct AudioDeviceInventory {
  std::vector<AudioDevice> inputs;
  std::vector<AudioOutputDevice> outputs;
};
class PortAudioRuntime {
public:
  PortAudioRuntime();
  ~PortAudioRuntime();
  PortAudioRuntime(const PortAudioRuntime &) = delete;
  PortAudioRuntime &operator=(const PortAudioRuntime &) = delete;
};
AudioDeviceInventory list_audio_devices();
std::vector<AudioDevice> list_input_devices();
std::vector<AudioOutputDevice> list_output_devices();
AudioDevice resolve_input_device(const AudioConfig &config);
AudioOutputDevice resolve_output_device(int preferred_id = -1);
int resolve_sample_rate(const AudioDevice &device, int preferred_rate);
std::vector<std::int16_t>
resample_linear(const std::vector<std::int16_t> &input, int input_rate,
                int output_rate);
void play_pcm16(const std::vector<std::int16_t> &samples, int sample_rate,
                const AudioOutputDevice &device);
class AudioCapture {
public:
  using BlockCallback = std::function<void(const std::vector<std::int16_t> &)>;
  AudioCapture(AudioDevice device, int sample_rate, int block_ms);
  ~AudioCapture();
  AudioCapture(const AudioCapture &) = delete;
  AudioCapture &operator=(const AudioCapture &) = delete;
  void run(std::atomic_bool &stop, const BlockCallback &callback);
  int sample_rate() const { return sample_rate_; }

private:
  AudioDevice device_;
  int sample_rate_;
  int block_ms_;
  void *stream_ = nullptr;
};
} // namespace vocotype::desktop
