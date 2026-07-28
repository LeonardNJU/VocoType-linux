#pragma once

#include <functional>
#include <memory>
#include <string>

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
  void cancel_async();
  void cancel();
  [[nodiscard]] bool running() const;

private:
  struct State;
  std::shared_ptr<State> state_;
};

} // namespace vocotype::desktop
