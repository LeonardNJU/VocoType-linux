#include "vocotype/desktop/audio_startup_diagnostics.hpp"
#include <iostream>
#include <stdexcept>
using namespace vocotype::desktop;
int main() {
  try {
    if (!is_known_system_audio_capture_helper("/Applications/ChatGPT.app/Contents/Resources/native/system-audio-spectrum"))
      throw std::runtime_error("known helper not recognized");
    for (const auto path : {"", "/tmp/system-audio-spectrum", "/Applications/Other.app/Contents/Resources/native/system-audio-spectrum", "/Applications/ChatGPT.app/Contents/Resources/native/system-audio-spectrum-other"})
      if (is_known_system_audio_capture_helper(path)) throw std::runtime_error("unrelated process matched");
    if (microphone_startup_error(true).find("可能冲突") == std::string::npos)
      throw std::runtime_error("presence incorrectly described as proof");
    if (microphone_startup_error(false).find("ChatGPT") != std::string::npos)
      throw std::runtime_error("specific component blamed without detection");
    std::cout << "PASS specific, advisory-only audio startup diagnostics\n";
    return 0;
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
