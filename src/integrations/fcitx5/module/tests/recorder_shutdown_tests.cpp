#include "recorder_shutdown.hpp"

#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <thread>
#include <sys/types.h>
#include <unistd.h>

namespace {

void require(bool condition, const char *message) {
  if (!condition) {
    std::cerr << "FAIL: " << message << '\n';
    std::exit(1);
  }
}

pid_t forkSleepingChild(std::chrono::milliseconds duration,
                        bool ignore_terminate) {
  const pid_t child = ::fork();
  if (child != 0)
    return child;
  if (ignore_terminate)
    std::signal(SIGTERM, SIG_IGN);
  std::this_thread::sleep_for(duration);
  _exit(0);
}

} // namespace

int main() {
  using namespace std::chrono_literals;
  using vocotype::fcitx5::RecorderShutdownPolicy;
  using vocotype::fcitx5::shutdownRecorderProcess;

  {
    const pid_t child = forkSleepingChild(80ms, false);
    require(child > 0, "failed to fork normal recorder fixture");
    const auto result = shutdownRecorderProcess(
        child, [] { return false; }, {},
        RecorderShutdownPolicy{500ms, 500ms, 50ms});
    require(result.reaped, "normal recorder was not reaped");
    require(!result.forced_terminate,
            "normal recorder was terminated unnecessarily");
  }

  {
    std::atomic_bool released{false};
    const pid_t child = forkSleepingChild(120ms, false);
    require(child > 0, "failed to fork ready recorder fixture");
    const auto result = shutdownRecorderProcess(
        child, [] { return true; }, [&] { released.store(true); },
        RecorderShutdownPolicy{500ms, 500ms, 50ms});
    require(result.reaped, "ready recorder was not reaped");
    require(result.output_ready, "ready recorder output was not observed");
    require(released.load(), "output-ready callback was not fired");
    require(!result.forced_terminate,
            "ready recorder was terminated unnecessarily");
  }

  {
    const pid_t child = forkSleepingChild(30s, true);
    require(child > 0, "failed to fork stuck recorder fixture");
    const auto started = std::chrono::steady_clock::now();
    const auto result = shutdownRecorderProcess(
        child, [] { return false; }, {},
        RecorderShutdownPolicy{100ms, 100ms, 75ms});
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - started);
    require(result.reaped, "stuck recorder was not reaped");
    require(result.forced_terminate,
            "stuck recorder did not enter forced termination");
    require(result.forced_kill,
            "SIGTERM-ignoring recorder did not escalate to SIGKILL");
    require(elapsed < 1500ms, "stuck recorder recovery exceeded its bound");
  }

  std::cout << "RECORDER_SHUTDOWN_OK\n";
  return 0;
}
