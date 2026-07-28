#include "vocotype/desktop/recorder_process.hpp"

#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <string>
#include <thread>

namespace {
void require(bool condition, const char *message) {
  if (!condition) {
    std::cerr << "FAIL: " << message << '\n';
    std::exit(1);
  }
}
} // namespace

int main(int argc, char **argv) {
  require(argc == 2, "missing fake recorder path");
  vocotype::desktop::RecorderProcess recorder;
  recorder.start(argv[1], {});
  const auto started = std::chrono::steady_clock::now();
  const std::string audio_path = recorder.stop();
  const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::steady_clock::now() - started);
  require(!audio_path.empty(), "stop did not return the announced WAV");
  require(std::filesystem::exists(audio_path), "announced WAV does not exist");
  require(elapsed < std::chrono::milliseconds(900),
          "stop waited for feedback cleanup instead of WAV readiness");
  require(recorder.running(),
          "fake recorder should still be finishing its feedback cleanup");
  std::this_thread::sleep_for(std::chrono::milliseconds(1700));
  require(!recorder.running(), "background recorder cleanup did not finish");
  std::filesystem::remove(audio_path);

  vocotype::desktop::RecorderProcess cancelled;
  cancelled.start(argv[1], {});
  const auto cancel_started = std::chrono::steady_clock::now();
  cancelled.cancel_async();
  const auto cancel_elapsed =
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::steady_clock::now() - cancel_started);
  require(cancel_elapsed < std::chrono::milliseconds(200),
          "cancel_async blocked while the recorder was starting");
  const auto cancel_deadline =
      std::chrono::steady_clock::now() + std::chrono::seconds(2);
  while (cancelled.running() &&
         std::chrono::steady_clock::now() < cancel_deadline)
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  require(!cancelled.running(),
          "asynchronously cancelled recorder was not reaped");

  std::cout << "fast stop elapsed_ms=" << elapsed.count()
            << " cancel_async_ms=" << cancel_elapsed.count() << '\n';
}
