#include <chrono>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>
#include <unistd.h>

int main() {
  char buffer[64];
  while (::read(STDIN_FILENO, buffer, sizeof(buffer)) > 0) {
  }
  const std::string path =
      "/tmp/vocotype-fast-wav-" + std::to_string(::getpid()) + ".wav";
  std::ofstream(path, std::ios::binary).put('\0');
  std::cout << "{\"type\":\"audio\",\"path\":\"" << path
            << "\"}\n"
            << std::flush;
  std::this_thread::sleep_for(std::chrono::milliseconds(1500));
  return 0;
}
