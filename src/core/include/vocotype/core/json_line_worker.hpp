#pragma once

#include <filesystem>
#include <mutex>
#include <string>
#include <vector>

#include <sys/types.h>

#include "vocotype/core/config.hpp"

namespace vocotype::core {

class JsonLineWorker {
public:
  JsonLineWorker();
  JsonLineWorker(const JsonLineWorker &) = delete;
  JsonLineWorker &operator=(const JsonLineWorker &) = delete;
  ~JsonLineWorker();

  [[nodiscard]] Json start(const std::filesystem::path &executable,
                           const std::vector<std::string> &arguments,
                           int startup_timeout_ms);
  [[nodiscard]] Json request(const Json &payload, int timeout_ms);
  [[nodiscard]] bool ready() noexcept;
  void stop() noexcept;

private:
  [[nodiscard]] bool alive_locked() noexcept;
  [[nodiscard]] std::string read_line_locked(int timeout_ms);
  void write_line_locked(const std::string &line, int timeout_ms);
  void stop_locked() noexcept;
  void reset_locked() noexcept;

  std::mutex mutex_;
  pid_t pid_ = -1;
  int input_fd_ = -1;
  int output_fd_ = -1;
  bool ready_ = false;
  std::string read_buffer_;
  Json ready_response_ = Json::object();
};

} // namespace vocotype::core
