#include "vocotype/desktop/wav.hpp"
#include <array>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <type_traits>
#include <unistd.h>
namespace vocotype::desktop {
namespace {
template <typename T> T read_le(std::istream &input) {
  using U = std::make_unsigned_t<T>;
  U value = 0;
  for (std::size_t index = 0; index < sizeof(T); ++index) {
    const int byte = input.get();
    if (byte == EOF)
      throw std::runtime_error("truncated WAV file");
    value |= static_cast<U>(static_cast<unsigned char>(byte)) << (8U * index);
  }
  return static_cast<T>(value);
}
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
Pcm16Wav read_pcm16_wav(const std::filesystem::path &path) {
  std::ifstream input(path, std::ios::binary);
  if (!input)
    throw std::runtime_error("cannot read WAV file: " + path.string());
  std::array<char, 4> tag{};
  input.read(tag.data(), 4);
  if (std::memcmp(tag.data(), "RIFF", 4) != 0)
    throw std::runtime_error("not a RIFF WAV file");
  (void)read_le<std::uint32_t>(input);
  input.read(tag.data(), 4);
  if (std::memcmp(tag.data(), "WAVE", 4) != 0)
    throw std::runtime_error("not a WAVE file");
  int sample_rate = 0;
  int channels = 0;
  int bits = 0;
  std::vector<std::int16_t> samples;
  while (input && !input.eof()) {
    input.read(tag.data(), 4);
    if (input.gcount() == 0)
      break;
    if (input.gcount() != 4)
      throw std::runtime_error("truncated WAV chunk");
    const std::uint32_t size = read_le<std::uint32_t>(input);
    const std::string name(tag.data(), 4);
    if (name == "fmt ") {
      const auto format = read_le<std::uint16_t>(input);
      channels = read_le<std::uint16_t>(input);
      sample_rate = static_cast<int>(read_le<std::uint32_t>(input));
      (void)read_le<std::uint32_t>(input);
      (void)read_le<std::uint16_t>(input);
      bits = read_le<std::uint16_t>(input);
      if (size > 16)
        input.seekg(static_cast<std::streamoff>(size - 16), std::ios::cur);
      if (format != 1 || bits != 16)
        throw std::runtime_error("only PCM16 WAV files are supported");
    } else if (name == "data") {
      if (size % sizeof(std::int16_t) != 0)
        throw std::runtime_error("invalid PCM16 data size");
      samples.resize(size / sizeof(std::int16_t));
      input.read(reinterpret_cast<char *>(samples.data()),
                 static_cast<std::streamsize>(size));
      if (!input)
        throw std::runtime_error("truncated PCM16 samples");
    } else {
      input.seekg(static_cast<std::streamoff>(size), std::ios::cur);
    }
    if (size % 2U != 0)
      input.seekg(1, std::ios::cur);
  }
  if (sample_rate <= 0 || channels <= 0 || samples.empty())
    throw std::runtime_error("WAV file is missing format or audio data");
  if (channels > 1) {
    std::vector<std::int16_t> mono;
    mono.reserve(samples.size() / static_cast<std::size_t>(channels));
    for (std::size_t frame = 0;
         frame + static_cast<std::size_t>(channels) <= samples.size();
         frame += static_cast<std::size_t>(channels)) {
      long sum = 0;
      for (int channel = 0; channel < channels; ++channel)
        sum += samples[frame + static_cast<std::size_t>(channel)];
      mono.push_back(static_cast<std::int16_t>(sum / channels));
    }
    samples = std::move(mono);
    channels = 1;
  }
  return {std::move(samples), sample_rate, channels};
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
