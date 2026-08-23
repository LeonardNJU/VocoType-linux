#include "recorder_shutdown.hpp"

#include <cerrno>
#include <csignal>
#include <thread>
#include <sys/wait.h>
#include <unistd.h>

namespace vocotype::fcitx5 {
namespace {

enum class ReapState { Running, Reaped, Error };

ReapState tryReap(pid_t pid) {
  if (pid <= 0)
    return ReapState::Reaped;
  int status = 0;
  for (;;) {
    const pid_t result = ::waitpid(pid, &status, WNOHANG);
    if (result == pid)
      return ReapState::Reaped;
    if (result == 0)
      return ReapState::Running;
    if (errno == EINTR)
      continue;
    if (errno == ECHILD)
      return ReapState::Reaped;
    return ReapState::Error;
  }
}

bool waitUntil(pid_t pid, std::chrono::steady_clock::time_point deadline) {
  while (std::chrono::steady_clock::now() < deadline) {
    const auto state = tryReap(pid);
    if (state == ReapState::Reaped)
      return true;
    if (state == ReapState::Error)
      return false;
    std::this_thread::sleep_for(std::chrono::milliseconds(25));
  }
  return tryReap(pid) == ReapState::Reaped;
}

bool forceTerminate(pid_t pid, const RecorderShutdownPolicy &policy,
                    RecorderShutdownResult &result) {
  if (pid <= 0)
    return true;
  result.forced_terminate = true;
  if (::kill(pid, SIGTERM) != 0 && errno == ESRCH)
    return tryReap(pid) == ReapState::Reaped;
  if (waitUntil(pid, std::chrono::steady_clock::now() +
                         policy.terminate_grace))
    return true;

  result.forced_kill = true;
  if (::kill(pid, SIGKILL) != 0 && errno == ESRCH)
    return tryReap(pid) == ReapState::Reaped;

  int status = 0;
  for (;;) {
    const pid_t waited = ::waitpid(pid, &status, 0);
    if (waited == pid)
      return true;
    if (waited < 0 && errno == EINTR)
      continue;
    if (waited < 0 && errno == ECHILD)
      return true;
    return false;
  }
}

} // namespace

RecorderShutdownResult shutdownRecorderProcess(
    pid_t pid, const std::function<bool()> &output_ready,
    const std::function<void()> &on_output_ready, RecorderShutdownPolicy policy) {
  RecorderShutdownResult result;
  if (pid <= 0) {
    result.reaped = true;
    return result;
  }

  bool output_callback_fired = false;
  const auto mark_output_ready = [&] {
    if (!result.output_ready && output_ready && output_ready()) {
      result.output_ready = true;
      if (on_output_ready && !output_callback_fired) {
        output_callback_fired = true;
        on_output_ready();
      }
    }
  };

  const auto output_deadline =
      std::chrono::steady_clock::now() + policy.output_timeout;
  while (std::chrono::steady_clock::now() < output_deadline) {
    mark_output_ready();
    const auto state = tryReap(pid);
    if (state == ReapState::Reaped) {
      result.reaped = true;
      return result;
    }
    if (state == ReapState::Error)
      break;
    if (result.output_ready)
      break;
    std::this_thread::sleep_for(std::chrono::milliseconds(25));
  }
  mark_output_ready();

  if (!result.output_ready) {
    result.reaped = forceTerminate(pid, policy, result);
    return result;
  }

  if (waitUntil(pid, std::chrono::steady_clock::now() +
                         policy.cleanup_timeout)) {
    result.reaped = true;
    return result;
  }

  result.reaped = forceTerminate(pid, policy, result);
  return result;
}

} // namespace vocotype::fcitx5
