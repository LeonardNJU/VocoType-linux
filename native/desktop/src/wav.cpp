#include "vocotype/desktop/wav.hpp"
#include <cstdlib>
#include <fstream>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <type_traits>
#include <unistd.h>
namespace vocotype::desktop {
namespace {
template <typename T> void write_le(std::ofstream &out, T value) {
  using U = std::make_unsigned_t<T>;
  const U converted = static_cast<U>(value);
  for (std::size_t i = 0; i < sizeof(T); ++i)
    out.put(static_cast<char>((converted >> (8U * i)) & 0xffU));
}
} // namespace
std::filesystem::path create_secure_wav_path() {
  std::filesystem::path directory = "/tmp";
  if (const char *runtime = std::getenv("XDG_RUNTIME_DIR"); runtime && *runtime)
    directory = runtime;
  std::filesystem::create_directories(directory);
  std::string pattern = (directory / "vocotype-recording-XXXXXX.wav").string();
  std::vector<char> buffer(pattern.begin(), pattern.end());
  buffer.push_back('\0');
  const int fd = mkstemps(buffer.data(), 4);
  if (fd < 0)
    throw std::runtime_error("cannot create secure temporary WAV file");
  (void)fchmod(fd, 0600);
  close(fd);
  return buffer.data();
}
void write_pcm16_wav(const std::filesystem::path &path,
                     const std::vector<std::int16_t> &samples, int sample_rate,
                     int channels) {
  if (sample_rate <= 0 || channels <= 0)
    throw std::invalid_argument("invalid WAV format");
  std::ofstream out(path, std::ios::binary | std::ios::trunc);
  if (!out)
    throw std::runtime_error("cannot write WAV file: " + path.string());
  const auto bytes = samples.size() * sizeof(std::int16_t);
  if (bytes > 0xffffffffU)
    throw std::runtime_error("recording is too large for WAV");
  const std::uint32_t data_size = static_cast<std::uint32_t>(bytes);
  out.write("RIFF", 4);
  write_le<std::uint32_t>(out, 36U + data_size);
  out.write("WAVE", 4);
  out.write("fmt ", 4);
  write_le<std::uint32_t>(out, 16U);
  write_le<std::uint16_t>(out, 1U);
  write_le<std::uint16_t>(out, static_cast<std::uint16_t>(channels));
  write_le<std::uint32_t>(out, static_cast<std::uint32_t>(sample_rate));
  write_le<std::uint32_t>(
      out, static_cast<std::uint32_t>(sample_rate * channels * 2));
  write_le<std::uint16_t>(out, static_cast<std::uint16_t>(channels * 2));
  write_le<std::uint16_t>(out, 16U);
  out.write("data", 4);
  write_le<std::uint32_t>(out, data_size);
  out.write(reinterpret_cast<const char *>(samples.data()),
            static_cast<std::streamsize>(data_size));
  if (!out)
    throw std::runtime_error("failed writing WAV samples");
}
} // namespace vocotype::desktop
