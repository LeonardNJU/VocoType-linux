#pragma once

#include <memory>
#include <string>

#include "vocotype/core/config.hpp"

namespace vocotype::core {

class TextNormalizer {
public:
  explicit TextNormalizer(NormalizationConfig config = {});
  TextNormalizer(const TextNormalizer &) = delete;
  TextNormalizer &operator=(const TextNormalizer &) = delete;
  TextNormalizer(TextNormalizer &&) noexcept;
  TextNormalizer &operator=(TextNormalizer &&) noexcept;
  ~TextNormalizer();

  [[nodiscard]] std::string normalize(const std::string &text);
  [[nodiscard]] std::string
  build_native_hotwords(const std::string &extra_hotwords = "");

private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

} // namespace vocotype::core
