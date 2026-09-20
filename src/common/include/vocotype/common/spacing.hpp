#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace vocotype::common {
namespace detail {

inline bool is_cjk_codepoint(std::uint32_t codepoint) {
  return (codepoint >= 0x3400 && codepoint <= 0x4DBF) ||
         (codepoint >= 0x4E00 && codepoint <= 0x9FFF) ||
         (codepoint >= 0xF900 && codepoint <= 0xFAFF) || codepoint == 0x3007 ||
         (codepoint >= 0x20000 && codepoint <= 0x2A6DF) ||
         (codepoint >= 0x2A700 && codepoint <= 0x2B73F) ||
         (codepoint >= 0x2B740 && codepoint <= 0x2B81F) ||
         (codepoint >= 0x2B820 && codepoint <= 0x2CEAF) ||
         (codepoint >= 0x2CEB0 && codepoint <= 0x2EBEF) ||
         (codepoint >= 0x2EBF0 && codepoint <= 0x2EE5D) ||
         (codepoint >= 0x2F800 && codepoint <= 0x2FA1F) ||
         (codepoint >= 0x30000 && codepoint <= 0x3134A) ||
         (codepoint >= 0x31350 && codepoint <= 0x323AF);
}

inline bool is_ascii_word_codepoint(std::uint32_t codepoint) {
  return (codepoint >= 'A' && codepoint <= 'Z') ||
         (codepoint >= 'a' && codepoint <= 'z') ||
         (codepoint >= '0' && codepoint <= '9');
}

inline std::size_t utf8_codepoint(const std::string &text, std::size_t offset,
                                  std::uint32_t &codepoint) {
  const auto byte = static_cast<unsigned char>(text[offset]);
  if (byte < 0x80) {
    codepoint = byte;
    return 1;
  }
  const std::size_t size =
      (byte & 0xE0) == 0xC0 ? 2 : (byte & 0xF0) == 0xE0 ? 3
                                                    : (byte & 0xF8) == 0xF0 ? 4
                                                                           : 1;
  if (size == 1 || offset + size > text.size()) {
    codepoint = byte;
    return 1;
  }
  std::uint32_t value = byte & ((1U << (8U - size - 1U)) - 1U);
  for (std::size_t index = 1; index < size; ++index) {
    const auto continuation = static_cast<unsigned char>(text[offset + index]);
    if ((continuation & 0xC0) != 0x80) {
      codepoint = byte;
      return 1;
    }
    value = (value << 6U) | (continuation & 0x3FU);
  }
  codepoint = value;
  return size;
}

} // namespace detail

// 在相邻中文与 ASCII 英文/数字之间补一个空格，不修改英文、数字和标点内部结构。
inline std::string space_between_cjk_and_ascii(std::string text) {
  enum class Kind { Other, Cjk, AsciiWord };
  std::string result;
  result.reserve(text.size() + text.size() / 8);
  Kind previous = Kind::Other;
  for (std::size_t offset = 0; offset < text.size();) {
    std::uint32_t codepoint = 0;
    const std::size_t length = detail::utf8_codepoint(text, offset, codepoint);
    const Kind current = detail::is_cjk_codepoint(codepoint)
                             ? Kind::Cjk
                             : detail::is_ascii_word_codepoint(codepoint)
                                   ? Kind::AsciiWord
                                   : Kind::Other;
    if ((previous == Kind::Cjk && current == Kind::AsciiWord) ||
        (previous == Kind::AsciiWord && current == Kind::Cjk)) {
      result.push_back(' ');
    }
    result.append(text, offset, length);
    previous = current;
    offset += length;
  }
  return result;
}

} // namespace vocotype::common
