#include "vocotype/desktop/streaming_preview.hpp"

#include <algorithm>
#include <cctype>

namespace vocotype::desktop {
namespace {

bool ascii_space(unsigned char value) noexcept {
  return std::isspace(value) != 0;
}

bool utf8_boundary(std::string_view value, std::size_t index) noexcept {
  return index == 0 || index == value.size() ||
         (static_cast<unsigned char>(value[index]) & 0xc0U) != 0x80U;
}

} // namespace

bool preview_text_is_blank(std::string_view text) noexcept {
  return std::all_of(text.begin(), text.end(), [](char value) {
    return ascii_space(static_cast<unsigned char>(value));
  });
}

std::string trim_preview_text(std::string_view text) {
  std::size_t begin = 0;
  while (begin < text.size() &&
         ascii_space(static_cast<unsigned char>(text[begin]))) {
    ++begin;
  }
  std::size_t end = text.size();
  while (end > begin &&
         ascii_space(static_cast<unsigned char>(text[end - 1]))) {
    --end;
  }
  return std::string(text.substr(begin, end - begin));
}

std::string merge_preview_text(std::string_view prefix,
                               std::string_view current) {
  if (prefix.empty())
    return std::string(current);
  if (current.empty())
    return std::string(prefix);
  if (current.starts_with(prefix))
    return std::string(current);
  if (prefix.ends_with(current))
    return std::string(prefix);

  const std::size_t maximum = std::min(prefix.size(), current.size());
  std::size_t overlap = 0;
  for (std::size_t candidate = maximum; candidate > 0; --candidate) {
    const std::size_t prefix_start = prefix.size() - candidate;
    if (!utf8_boundary(prefix, prefix_start) ||
        !utf8_boundary(current, candidate)) {
      continue;
    }
    if (prefix.substr(prefix_start) == current.substr(0, candidate)) {
      overlap = candidate;
      break;
    }
  }
  std::string result(prefix);
  result.append(current.substr(overlap));
  return result;
}

void StreamingPreviewTranscript::reset() {
  committed_.clear();
  session_.clear();
  display_.clear();
}

void StreamingPreviewTranscript::begin_recovery() {
  committed_ = display_;
  session_.clear();
}

std::optional<std::string>
StreamingPreviewTranscript::update_session_text(std::string_view text) {
  if (preview_text_is_blank(text))
    return std::nullopt;
  std::string normalized = trim_preview_text(text);
  if (normalized.empty())
    return std::nullopt;
  session_ = std::move(normalized);
  std::string next = merge_preview_text(committed_, session_);
  if (next == display_)
    return std::nullopt;
  display_ = std::move(next);
  return display_;
}

const std::string &StreamingPreviewTranscript::display_text() const noexcept {
  return display_;
}

bool StreamingPreviewTranscript::has_text() const noexcept {
  return !display_.empty();
}

} // namespace vocotype::desktop
