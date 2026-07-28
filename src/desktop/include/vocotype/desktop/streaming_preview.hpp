#pragma once

#include <optional>
#include <string>
#include <string_view>

namespace vocotype::desktop {

[[nodiscard]] bool preview_text_is_blank(std::string_view text) noexcept;
[[nodiscard]] std::string trim_preview_text(std::string_view text);
[[nodiscard]] std::string merge_preview_text(std::string_view prefix,
                                             std::string_view current);

class StreamingPreviewTranscript {
public:
  void reset();
  void begin_recovery();
  [[nodiscard]] std::optional<std::string>
  update_session_text(std::string_view text);
  [[nodiscard]] const std::string &display_text() const noexcept;
  [[nodiscard]] bool has_text() const noexcept;

private:
  std::string committed_;
  std::string session_;
  std::string display_;
};

} // namespace vocotype::desktop
