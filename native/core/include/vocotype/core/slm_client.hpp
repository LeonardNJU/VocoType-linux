#pragma once

#include <mutex>
#include <optional>
#include <string>

#include "vocotype/core/config.hpp"

namespace vocotype::core {

struct CompletionResult {
  bool success = false;
  std::string text;
  std::string reason;
  std::string error;
  double latency_ms = 0.0;
};

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
  [[nodiscard]] bool edit_enabled() const noexcept;
  [[nodiscard]] int edit_max_tokens() const noexcept;
  [[nodiscard]] bool should_polish(const std::string &text,
                                   int min_chars_override = -1) const;
  [[nodiscard]] CompletionResult
  complete(const std::string &system_prompt, const std::string &user_text,
           int max_tokens,
           std::optional<bool> enable_thinking = std::nullopt) const;
  [[nodiscard]] PolishResult
  polish(const std::string &text,
         std::optional<bool> enable_thinking = std::nullopt) const;

private:
  [[nodiscard]] Json build_payload(const std::string &system_prompt,
                                   const std::string &user_text, int max_tokens,
                                   std::optional<bool> enable_thinking) const;
  [[nodiscard]] static std::string extract_content(const Json &response);

  SlmConfig config_;
  mutable std::mutex request_mutex_;
};

} // namespace vocotype::core
