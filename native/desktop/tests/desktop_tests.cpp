#include "vocotype/desktop/audio.hpp"
#include "vocotype/desktop/ipc.hpp"
#include "vocotype/desktop/wav.hpp"
#include <cassert>
#include <filesystem>
#include <fstream>
#include <iostream>
int main() {
  using namespace vocotype::desktop;
  const std::vector<std::int16_t> source{0, 1000, 2000, 3000};
  const auto doubled = resample_linear(source, 4, 8);
  assert(doubled.size() == 8);
  assert(doubled.front() == 0 && doubled.back() == 3000);
  const std::string encoded =
      base64_encode(reinterpret_cast<const unsigned char *>("abc"), 3);
  assert(encoded == "YWJj");
  const auto path = create_secure_wav_path();
  write_pcm16_wav(path, source, 16000);
  std::ifstream input(path, std::ios::binary);
  char header[4]{};
  input.read(header, 4);
  assert(std::string(header, 4) == "RIFF");
  std::filesystem::remove(path);
  std::cout << "desktop tests passed\n";
}
