#pragma once

#include <string>

#include "vocotype/core/config.hpp"

namespace vocotype::core {

struct PolishResult {
  bool success = false;
  std::string text;
  std::string original_text;
  std::string reason;
  std::string error;
  double latency_ms = 0.0;
};

class SlmClient {
public:
  explicit SlmClient(SlmConfig config);

  [[nodiscard]] bool enabled() const noexcept;
  [[nodiscard]] PolishResult polish(const std::string &text) const;

private:
  [[nodiscard]] Json build_payload(const std::string &text) const;
  [[nodiscard]] static std::string extract_content(const Json &response);

  SlmConfig config_;
};

} // namespace vocotype::core
