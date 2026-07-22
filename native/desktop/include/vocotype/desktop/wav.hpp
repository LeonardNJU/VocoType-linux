#pragma once
#include <cstdint>
#include <filesystem>
#include <vector>
namespace vocotype::desktop {
std::filesystem::path create_secure_wav_path();
void write_pcm16_wav(const std::filesystem::path &path,
                     const std::vector<std::int16_t> &samples, int sample_rate,
                     int channels = 1);
} // namespace vocotype::desktop
