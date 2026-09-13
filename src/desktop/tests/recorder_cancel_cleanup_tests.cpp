#include "vocotype/desktop/recorder_process.hpp"
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <unistd.h>
int main(int argc, char **argv) {
  using namespace std::chrono_literals;
  if (argc != 2) return 2;
  char pattern[] = "/tmp/vocotype-cancel-cleanup-XXXXXX";
  const int fd = mkstemp(pattern);
  if (fd < 0) return 3;
  close(fd);
  struct Cleanup { const char *path; ~Cleanup() { std::remove(path); unsetenv("VOCOTYPE_TEST_CLEANUP_MARKER"); } } cleanup{pattern};
  setenv("VOCOTYPE_TEST_CLEANUP_MARKER", pattern, 1);
  try {
    std::atomic_bool ready{false};
    vocotype::desktop::RecorderProcess recorder;
    recorder.start(argv[1], [&](const std::string &type, const std::string &) {
      if (type == "test_ready") ready.store(true);
    });
    const auto deadline = std::chrono::steady_clock::now() + 2s;
    while (!ready.load() && std::chrono::steady_clock::now() < deadline)
      std::this_thread::sleep_for(5ms);
    if (!ready.load()) throw std::runtime_error("fake recorder never became ready");
    const auto started = std::chrono::steady_clock::now();
    recorder.cancel_async();
    if (std::chrono::steady_clock::now() - started > 200ms)
      throw std::runtime_error("cancel blocked the UI");
    vocotype::desktop::RecorderProcess overlapping;
    bool overlap_blocked = false;
    try { overlapping.start(argv[1], {}); }
    catch (const std::exception &) { overlap_blocked = true; }
    if (!overlap_blocked) throw std::runtime_error("new HAL start overlapped cancelled cleanup");
    while (recorder.running() && std::chrono::steady_clock::now() - started < 2s)
      std::this_thread::sleep_for(10ms);
    if (recorder.running()) throw std::runtime_error("cancelled recorder was not reaped");
    std::ifstream marker(pattern); std::string text; std::getline(marker, text);
    if (text != "cleaned") throw std::runtime_error("startup cleanup was interrupted by early SIGKILL");
    std::cout << "PASS cancellation returns immediately and allows startup cleanup\n";
  } catch (const std::exception &e) {
    std::cerr << "FAIL " << e.what() << '\n'; return 1;
  }
}
