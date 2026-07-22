#pragma once

#include <string>

#include "vocotype/core/config.hpp"
#include "vocotype/core/slm_client.hpp"

namespace vocotype::core {

class VoiceEditPlanner {
public:
  explicit VoiceEditPlanner(const SlmClient &slm);

  [[nodiscard]] Json plan(const Json &request,
                          const std::string &instruction) const;
  [[nodiscard]] static Json
  validate_model_output(const std::string &output,
                        const std::string &original_text);

private:
  [[nodiscard]] static std::string build_request_text(
      const std::string &context_text, const std::string &instruction,
      int cursor_pos, int anchor_pos, const std::string &selected_text,
      bool supports_surrounding, const std::string &replace_state);
  [[nodiscard]] static std::string format_failure(const std::string &reason,
                                                  const std::string &detail);

  const SlmClient &slm_;
};

} // namespace vocotype::core
