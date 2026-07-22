#pragma once
#include <cstdint>
#include <filesystem>
#include <vector>
namespace vocotype::desktop {
struct Pcm16Wav {
  std::vector<std::int16_t> samples;
  int sample_rate = 0;
  int channels = 0;
};
std::filesystem::path create_secure_wav_path();
Pcm16Wav read_pcm16_wav(const std::filesystem::path &path);
void write_pcm16_wav(const std::filesystem::path &path,
                     const std::vector<std::int16_t> &samples, int sample_rate,
                     int channels = 1);
} // namespace vocotype::desktop
