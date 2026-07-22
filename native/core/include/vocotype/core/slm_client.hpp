#pragma once

#include <functional>
#include <mutex>
#include <optional>
#include <string>
#include <utility>

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

struct SlmStreamEvent {
  SlmStreamEvent(std::string event_kind = {}, std::string event_text = {},
                 std::string event_preview = {}, std::string event_reason = {},
                 std::string event_error = {}, double event_latency_ms = 0.0)
      : kind(std::move(event_kind)), text(std::move(event_text)),
        preview(std::move(event_preview)), reason(std::move(event_reason)),
        error(std::move(event_error)), latency_ms(event_latency_ms) {}

  std::string kind;
  std::string text;
  std::string preview;
  std::string reason;
  std::string error;
  double latency_ms = 0.0;
};

using SlmStreamCallback = std::function<bool(const SlmStreamEvent &)>;

class SlmClient {
public:
  explicit SlmClient(SlmConfig config);

  [[nodiscard]] bool enabled() const noexcept;
  [[nodiscard]] bool remote_stream() const noexcept;
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
  [[nodiscard]] PolishResult
  stream_polish(const std::string &text, std::optional<bool> enable_thinking,
                const SlmStreamCallback &callback) const;

private:
  [[nodiscard]] Json build_payload(const std::string &system_prompt,
                                   const std::string &user_text, int max_tokens,
                                   bool stream,
                                   std::optional<bool> enable_thinking) const;
  [[nodiscard]] static std::string extract_content(const Json &response);
  [[nodiscard]] static std::string extract_stream_delta(const Json &response);
  [[nodiscard]] static std::string
  stream_visible_content(const std::string &content);
  [[nodiscard]] static std::string
  strip_thinking_content(const std::string &content);
  [[nodiscard]] static std::string failure_message(const std::string &reason);

  SlmConfig config_;
  mutable std::mutex request_mutex_;
};

} // namespace vocotype::core
