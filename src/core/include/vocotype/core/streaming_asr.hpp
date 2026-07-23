#pragma once

#include <filesystem>
#include <string>
#include <vector>

#include "vocotype/core/config.hpp"
#include "vocotype/core/json_line_worker.hpp"

namespace vocotype::core {

class StreamingAsrProcess {
public:
  explicit StreamingAsrProcess(StreamingAsrConfig config);
  StreamingAsrProcess(const StreamingAsrProcess &) = delete;
  StreamingAsrProcess &operator=(const StreamingAsrProcess &) = delete;

  [[nodiscard]] bool enabled() const noexcept;
  [[nodiscard]] bool ready() noexcept;
  [[nodiscard]] Json start_session();
  [[nodiscard]] Json feed(const Json &request);
  [[nodiscard]] Json close_session(const Json &request);

private:
  [[nodiscard]] Json ensure_worker();
  [[nodiscard]] std::filesystem::path resolve_worker_path() const;
  [[nodiscard]] std::filesystem::path resolve_model_dir() const;
  [[nodiscard]] std::vector<std::string>
  worker_arguments(const std::filesystem::path &model_dir) const;

  StreamingAsrConfig config_;
  JsonLineWorker worker_;
};

} // namespace vocotype::core
