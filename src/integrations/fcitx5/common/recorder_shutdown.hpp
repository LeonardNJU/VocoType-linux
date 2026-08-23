#pragma once

#include <chrono>
#include <functional>
#include <sys/types.h>

namespace vocotype::fcitx5 {

struct RecorderShutdownPolicy {
  std::chrono::milliseconds output_timeout{3000};
  std::chrono::milliseconds cleanup_timeout{15000};
  std::chrono::milliseconds terminate_grace{250};
};

struct RecorderShutdownResult {
  bool reaped = false;
  bool output_ready = false;
  bool forced_terminate = false;
  bool forced_kill = false;
};

RecorderShutdownResult shutdownRecorderProcess(
    pid_t pid, const std::function<bool()> &output_ready,
    const std::function<void()> &on_output_ready = {},
    RecorderShutdownPolicy policy = {});

} // namespace vocotype::fcitx5
