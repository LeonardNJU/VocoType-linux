#pragma once

#include <filesystem>
#include <string>
#include <vector>

#include "vocotype/core/config.hpp"
#include "vocotype/core/json_line_worker.hpp"

namespace vocotype::core {

class OfflineAsrProcess {
public:
  explicit OfflineAsrProcess(OfflineAsrConfig config);
  OfflineAsrProcess(const OfflineAsrProcess &) = delete;
  OfflineAsrProcess &operator=(const OfflineAsrProcess &) = delete;

  [[nodiscard]] bool enabled() const noexcept;
  [[nodiscard]] bool ready() noexcept;
  [[nodiscard]] Json initialize();
  [[nodiscard]] Json transcribe(const Json &request);

private:
  [[nodiscard]] Json ensure_worker();
  [[nodiscard]] std::filesystem::path resolve_worker_path() const;
  [[nodiscard]] std::filesystem::path resolve_model_dir(
      const std::string &environment_name, const std::string &configured_dir,
      const std::string &model_name, const std::string &label) const;
  [[nodiscard]] std::vector<std::string> worker_arguments() const;

  OfflineAsrConfig config_;
  JsonLineWorker worker_;
};

} // namespace vocotype::core
