#pragma once
#include <functional>
#include <string>
#include <sys/types.h>
namespace vocotype::desktop {
class RecorderProcess {
public:
  using EventCallback =
      std::function<void(const std::string &, const std::string &)>;
  RecorderProcess() = default;
  ~RecorderProcess();
  RecorderProcess(const RecorderProcess &) = delete;
  RecorderProcess &operator=(const RecorderProcess &) = delete;
  void start(const std::string &executable, EventCallback callback);
  std::string stop();
  void cancel();
  bool running() const { return pid_ > 0; }

private:
  pid_t pid_ = -1;
  int stdin_fd_ = -1;
  int stdout_fd_ = -1;
  std::string audio_path_;
  std::function<void()> join_output_;
};
} // namespace vocotype::desktop
