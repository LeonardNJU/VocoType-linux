#include <csignal>
#include <cstdlib>
#include <chrono>
#include <fstream>
#include <iostream>
#include <thread>
namespace { volatile std::sig_atomic_t cancelled = 0; void stop(int) { cancelled = 1; } }
int main() {
  std::signal(SIGTERM, stop);
  std::cout << "{\"type\":\"test_ready\"}\n" << std::flush;
  while (!cancelled) std::this_thread::sleep_for(std::chrono::milliseconds(5));
  // Model a driver unwinding an already in-flight startup. No microphone is
  // opened by this test helper. 250-ms SIGKILL must not interrupt cleanup.
  std::this_thread::sleep_for(std::chrono::milliseconds(600));
  const char *path = std::getenv("VOCOTYPE_TEST_CLEANUP_MARKER");
  if (!path) return 2;
  std::ofstream marker(path); marker << "cleaned\n";
  return marker ? 0 : 3;
}
