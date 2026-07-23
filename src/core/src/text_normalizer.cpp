#include "vocotype/core/text_normalizer.hpp"
#include "vocotype/common/terms_yaml.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <charconv>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <limits>
#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace vocotype::core {
namespace {

using UString = std::u32string;
using UView = std::u32string_view;
using Span = std::pair<std::size_t, std::size_t>;

UString decode_utf8(const std::string &source) {
  UString output;
  output.reserve(source.size());
  for (std::size_t index = 0; index < source.size();) {
    const unsigned char first = static_cast<unsigned char>(source[index]);
    char32_t value = 0;
    std::size_t width = 0;
    if (first < 0x80U) {
      value = first;
      width = 1;
    } else if ((first & 0xE0U) == 0xC0U) {
      value = first & 0x1FU;
      width = 2;
    } else if ((first & 0xF0U) == 0xE0U) {
      value = first & 0x0FU;
      width = 3;
    } else if ((first & 0xF8U) == 0xF0U) {
      value = first & 0x07U;
      width = 4;
    } else {
      output.push_back(U'\uFFFD');
      ++index;
      continue;
    }
    if (index + width > source.size()) {
      output.push_back(U'\uFFFD');
      break;
    }
    bool valid = true;
    for (std::size_t offset = 1; offset < width; ++offset) {
      const unsigned char continuation =
          static_cast<unsigned char>(source[index + offset]);
      if ((continuation & 0xC0U) != 0x80U) {
        valid = false;
        break;
      }
      value = (value << 6U) | (continuation & 0x3FU);
    }
    if (!valid || value > 0x10FFFFU || (value >= 0xD800U && value <= 0xDFFFU)) {
      output.push_back(U'\uFFFD');
      ++index;
      continue;
    }
    output.push_back(value);
    index += width;
  }
  return output;
}

std::string encode_utf8(UView source) {
  std::string output;
  output.reserve(source.size() * 3U);
  for (char32_t value : source) {
    if (value <= 0x7FU) {
      output.push_back(static_cast<char>(value));
    } else if (value <= 0x7FFU) {
      output.push_back(static_cast<char>(0xC0U | (value >> 6U)));
      output.push_back(static_cast<char>(0x80U | (value & 0x3FU)));
    } else if (value <= 0xFFFFU) {
      output.push_back(static_cast<char>(0xE0U | (value >> 12U)));
      output.push_back(static_cast<char>(0x80U | ((value >> 6U) & 0x3FU)));
      output.push_back(static_cast<char>(0x80U | (value & 0x3FU)));
    } else {
      output.push_back(static_cast<char>(0xF0U | (value >> 18U)));
      output.push_back(static_cast<char>(0x80U | ((value >> 12U) & 0x3FU)));
      output.push_back(static_cast<char>(0x80U | ((value >> 6U) & 0x3FU)));
      output.push_back(static_cast<char>(0x80U | (value & 0x3FU)));
    }
  }
  return output;
}

char32_t ascii_fold(char32_t value) {
  if (value >= U'A' && value <= U'Z') {
    return value + (U'a' - U'A');
  }
  return value;
}

UString ascii_casefold(UView value) {
  UString result(value);
  std::transform(result.begin(), result.end(), result.begin(), ascii_fold);
  return result;
}

bool ascii_word(char32_t value) {
  return (value >= U'a' && value <= U'z') || (value >= U'A' && value <= U'Z') ||
         (value >= U'0' && value <= U'9') || value == U'_';
}

bool starts_with(UView text, UView prefix) {
  return text.size() >= prefix.size() &&
         std::equal(prefix.begin(), prefix.end(), text.begin());
}

bool ends_with(UView text, UView suffix) {
  return text.size() >= suffix.size() &&
         std::equal(suffix.rbegin(), suffix.rend(), text.rbegin());
}

template <typename Container>
bool starts_with_any(UView text, const Container &prefixes) {
  return std::any_of(prefixes.begin(), prefixes.end(), [&](const auto &prefix) {
    return starts_with(text, prefix);
  });
}

template <typename Container>
bool ends_with_any(UView text, const Container &suffixes) {
  return std::any_of(suffixes.begin(), suffixes.end(), [&](const auto &suffix) {
    return ends_with(text, suffix);
  });
}

UString trim(UView value) {
  std::size_t first = 0;
  while (first < value.size() &&
         (value[first] == U' ' || value[first] == U'\t' ||
          value[first] == U'\r' || value[first] == U'\n')) {
    ++first;
  }
  std::size_t last = value.size();
  while (last > first &&
         (value[last - 1] == U' ' || value[last - 1] == U'\t' ||
          value[last - 1] == U'\r' || value[last - 1] == U'\n')) {
    --last;
  }
  return UString(value.substr(first, last - first));
}

bool has_whitespace(UView value) {
  return std::any_of(value.begin(), value.end(), [](char32_t ch) {
    return ch == U' ' || ch == U'\t' || ch == U'\r' || ch == U'\n';
  });
}

struct TermEntry {
  TermEntry() = default;
  explicit TermEntry(UString value) : canonical(std::move(value)) {}

  UString canonical;
  std::vector<UString> aliases;
  std::vector<UString> hotwords;
  bool protect = true;
};

struct AliasRule {
  UString alias;
  UString folded;
  UString canonical;
  bool prefix_boundary = false;
  bool suffix_boundary = false;
};

struct Lexicon {
  std::vector<TermEntry> entries;
  std::vector<AliasRule> aliases;
  std::vector<UString> protected_phrases;
};

struct TermRewriteResult {
  UString text;
  std::vector<Span> protected_spans;
};

std::vector<Span> merge_spans(std::vector<Span> spans) {
  spans.erase(std::remove_if(
                  spans.begin(), spans.end(),
                  [](const Span &span) { return span.first >= span.second; }),
              spans.end());
  std::sort(spans.begin(), spans.end());
  if (spans.empty()) {
    return {};
  }
  std::vector<Span> result;
  Span current = spans.front();
  for (std::size_t index = 1; index < spans.size(); ++index) {
    if (spans[index].first <= current.second) {
      current.second = std::max(current.second, spans[index].second);
    } else {
      result.push_back(current);
      current = spans[index];
    }
  }
  result.push_back(current);
  return result;
}

bool overlaps(std::size_t start, std::size_t end,
              const std::vector<Span> &spans) {
  return std::any_of(spans.begin(), spans.end(), [&](const Span &span) {
    return span.first < end && start < span.second;
  });
}

bool contained_by(std::size_t start, std::size_t end,
                  const std::vector<Span> &spans) {
  return std::any_of(spans.begin(), spans.end(), [&](const Span &span) {
    return span.first <= start && end <= span.second;
  });
}

bool equal_folded(UView source, std::size_t offset, UView folded) {
  if (offset + folded.size() > source.size()) {
    return false;
  }
  for (std::size_t index = 0; index < folded.size(); ++index) {
    if (ascii_fold(source[offset + index]) != folded[index]) {
      return false;
    }
  }
  return true;
}

std::vector<Span> find_phrase_spans(UView source,
                                    const std::vector<UString> &phrases) {
  std::vector<Span> spans;
  for (const UString &phrase : phrases) {
    if (phrase.empty() || phrase.size() > source.size()) {
      continue;
    }
    const bool fold =
        std::any_of(phrase.begin(), phrase.end(), [](char32_t ch) {
          return (ch >= U'A' && ch <= U'Z') || (ch >= U'a' && ch <= U'z');
        });
    const UString folded = fold ? ascii_casefold(phrase) : UString();
    for (std::size_t offset = 0; offset + phrase.size() <= source.size();
         ++offset) {
      const bool match =
          fold ? equal_folded(source, offset, folded)
               : std::equal(phrase.begin(), phrase.end(),
                            source.begin() +
                                static_cast<std::ptrdiff_t>(offset));
      if (match) {
        spans.emplace_back(offset, offset + phrase.size());
      }
    }
  }
  return spans;
}

TermRewriteResult rewrite_terms(const Lexicon &lexicon, UView source) {
  TermRewriteResult result;
  if (source.empty()) {
    return result;
  }
  result.text.reserve(source.size());
  std::vector<Span> replacement_spans;
  for (std::size_t index = 0; index < source.size();) {
    const AliasRule *matched = nullptr;
    for (const AliasRule &rule : lexicon.aliases) {
      if (!equal_folded(source, index, rule.folded)) {
        continue;
      }
      if (rule.prefix_boundary && index > 0 && ascii_word(source[index - 1])) {
        continue;
      }
      const std::size_t end = index + rule.alias.size();
      if (rule.suffix_boundary && end < source.size() &&
          ascii_word(source[end])) {
        continue;
      }
      matched = &rule;
      break;
    }
    if (matched == nullptr) {
      result.text.push_back(source[index]);
      ++index;
      continue;
    }
    const std::size_t output_start = result.text.size();
    result.text.append(matched->canonical);
    replacement_spans.emplace_back(output_start, result.text.size());
    index += matched->alias.size();
  }
  std::vector<Span> phrase_spans =
      find_phrase_spans(result.text, lexicon.protected_phrases);
  replacement_spans.insert(replacement_spans.end(), phrase_spans.begin(),
                           phrase_spans.end());
  result.protected_spans = merge_spans(std::move(replacement_spans));
  return result;
}

Lexicon build_lexicon(std::vector<TermEntry> entries,
                      std::vector<UString> explicit_protected) {
  Lexicon result;
  result.entries = std::move(entries);
  std::unordered_set<UString> canonical_keys;
  for (const TermEntry &entry : result.entries) {
    canonical_keys.insert(ascii_casefold(entry.canonical));
  }

  std::unordered_map<UString, UString> replacements;
  std::vector<UString> alias_order;
  std::unordered_set<UString> protected_set;
  for (UString &phrase : explicit_protected) {
    protected_set.insert(std::move(phrase));
  }
  for (const TermEntry &entry : result.entries) {
    if (entry.protect) {
      protected_set.insert(entry.canonical);
    }
    for (const UString &alias : entry.aliases) {
      const UString folded = ascii_casefold(alias);
      if (canonical_keys.contains(folded) &&
          folded != ascii_casefold(entry.canonical)) {
        continue;
      }
      if (!replacements.contains(folded)) {
        replacements.emplace(folded, entry.canonical);
        alias_order.push_back(alias);
      }
    }
  }
  std::sort(alias_order.begin(), alias_order.end(),
            [](const UString &left, const UString &right) {
              if (left.size() != right.size()) {
                return left.size() > right.size();
              }
              return ascii_casefold(left) < ascii_casefold(right);
            });
  for (const UString &alias : alias_order) {
    const UString folded = ascii_casefold(alias);
    result.aliases.push_back({alias, folded, replacements.at(folded),
                              !alias.empty() && ascii_word(alias.front()),
                              !alias.empty() && ascii_word(alias.back())});
  }
  result.protected_phrases.assign(protected_set.begin(), protected_set.end());
  std::sort(result.protected_phrases.begin(), result.protected_phrases.end(),
            [](const UString &left, const UString &right) {
              if (left.size() != right.size()) {
                return left.size() > right.size();
              }
              return ascii_casefold(left) < ascii_casefold(right);
            });
  return result;
}

Lexicon parse_terms_yaml(const std::filesystem::path &path) {
  const vocotype::common::TermsDocument document =
      vocotype::common::parse_terms_yaml(path);
  std::vector<TermEntry> entries;
  entries.reserve(document.terms.size());
  for (const auto &definition : document.terms) {
    TermEntry entry;
    entry.canonical = decode_utf8(definition.canonical);
    entry.protect = definition.protect;
    for (const std::string &alias : definition.aliases) {
      entry.aliases.push_back(decode_utf8(alias));
    }
    for (const std::string &hotword : definition.hotwords) {
      entry.hotwords.push_back(decode_utf8(hotword));
    }
    entries.push_back(std::move(entry));
  }
  std::vector<UString> protected_phrases;
  protected_phrases.reserve(document.protected_phrases.size());
  for (const std::string &phrase : document.protected_phrases) {
    protected_phrases.push_back(decode_utf8(phrase));
  }
  return build_lexicon(std::move(entries), std::move(protected_phrases));
}

std::filesystem::path terms_path() {
  if (const char *override_path = std::getenv("VOCOTYPE_TERMS_FILE")) {
    if (*override_path != '\0') {
      return expand_user_path(override_path);
    }
  }
  std::filesystem::path base;
  if (const char *config_home = std::getenv("XDG_CONFIG_HOME")) {
    if (*config_home != '\0') {
      base = expand_user_path(config_home);
    }
  }
  if (base.empty()) {
    if (const char *home = std::getenv("HOME")) {
      base = std::filesystem::path(home) / ".config";
    }
  }
  const std::filesystem::path directory = base / "vocotype";
  const std::filesystem::path preferred = directory / "terms.yaml";
  const std::filesystem::path legacy = directory / "user-dictionary.yaml";
  if (!std::filesystem::exists(preferred) && std::filesystem::exists(legacy)) {
    return legacy;
  }
  return preferred;
}

struct FileSignature {
  std::uintmax_t size = 0;
  std::filesystem::file_time_type modified{};
  bool operator==(const FileSignature &) const = default;
};

std::optional<FileSignature> file_signature(const std::filesystem::path &path) {
  std::error_code error;
  const auto size = std::filesystem::file_size(path, error);
  if (error) {
    return std::nullopt;
  }
  const auto modified = std::filesystem::last_write_time(path, error);
  if (error) {
    return std::nullopt;
  }
  return FileSignature{size, modified};
}

const std::array<UView, 950> kFixedNonNumericPhrases = {
#include "fixed_non_numeric_phrases.inc"
};

std::vector<Span> fixed_phrase_spans(UView source) {
  std::vector<Span> result;
  for (UView phrase : kFixedNonNumericPhrases) {
    if (phrase.empty() || phrase.size() > source.size()) {
      continue;
    }
    std::size_t offset = 0;
    while (offset + phrase.size() <= source.size()) {
      const auto found = source.find(phrase, offset);
      if (found == UView::npos) {
        break;
      }
      result.emplace_back(found, found + phrase.size());
      offset = found + 1U;
    }
  }
  return merge_spans(std::move(result));
}

std::optional<char> value_digit(char32_t value) {
  switch (value) {
  case U'零':
  case U'〇':
  case U'○':
    return '0';
  case U'一':
    return '1';
  case U'二':
  case U'两':
  case U'俩':
    return '2';
  case U'三':
    return '3';
  case U'四':
    return '4';
  case U'五':
    return '5';
  case U'六':
    return '6';
  case U'七':
    return '7';
  case U'八':
    return '8';
  case U'九':
    return '9';
  default:
    return std::nullopt;
  }
}

std::optional<char> sequence_digit(char32_t value) {
  if (const auto digit = value_digit(value)) {
    return digit;
  }
  switch (value) {
  case U'幺':
    return '1';
  case U'洞':
    return '0';
  case U'拐':
    return '7';
  case U'勾':
    return '9';
  default:
    return std::nullopt;
  }
}

bool value_zero(char32_t value) {
  return value == U'零' || value == U'〇' || value == U'○';
}

bool zero_digit(char32_t value) { return value_zero(value) || value == U'洞'; }

std::optional<long long> small_unit(char32_t value) {
  switch (value) {
  case U'十':
    return 10;
  case U'百':
    return 100;
  case U'千':
    return 1000;
  default:
    return std::nullopt;
  }
}

std::optional<long long> large_unit(char32_t value) {
  if (value == U'万') {
    return 10000;
  }
  if (value == U'亿') {
    return 100000000;
  }
  return std::nullopt;
}

bool integer_char(char32_t value) {
  return value_digit(value).has_value() || small_unit(value).has_value() ||
         large_unit(value).has_value();
}

bool general_char(char32_t value) {
  return sequence_digit(value).has_value() || small_unit(value).has_value() ||
         large_unit(value).has_value();
}

bool is_digit_sequence(UView body) {
  return !body.empty() &&
         std::all_of(body.begin(), body.end(), [](char32_t value) {
           return sequence_digit(value).has_value();
         });
}

bool is_structured_body(UView body) {
  return !body.empty() && std::all_of(body.begin(), body.end(), integer_char);
}

bool contains_unit(UView body) {
  return std::any_of(body.begin(), body.end(), [](char32_t value) {
    return small_unit(value).has_value() || large_unit(value).has_value();
  });
}

std::optional<long long> parse_integer_value(UView body);

std::optional<long long> parse_section_value(UView body) {
  if (body.empty()) {
    return std::nullopt;
  }
  if (std::all_of(body.begin(), body.end(), [](char32_t value) {
        return value_digit(value).has_value();
      })) {
    long long value = 0;
    for (char32_t character : body) {
      value = value * 10 + (*value_digit(character) - '0');
    }
    return value;
  }
  long long total = 0;
  long long number = 0;
  bool saw_unit = false;
  long long last_unit = 1;
  bool trailing_zero = false;
  for (char32_t character : body) {
    if (const auto digit = value_digit(character)) {
      const long long numeric = *digit - '0';
      if (saw_unit && numeric == 0) {
        trailing_zero = true;
      }
      number = numeric;
      continue;
    }
    const auto unit = small_unit(character);
    if (!unit.has_value()) {
      return std::nullopt;
    }
    if (number == 0) {
      number = total == 0 ? 1 : 0;
    }
    if (number > std::numeric_limits<long long>::max() / *unit ||
        total > std::numeric_limits<long long>::max() - number * *unit) {
      return std::nullopt;
    }
    total += number * *unit;
    number = 0;
    saw_unit = true;
    last_unit = *unit;
    trailing_zero = false;
  }
  total += number;
  if (saw_unit && number != 0 && last_unit >= 100 && !trailing_zero) {
    total += number * ((last_unit / 10) - 1);
  }
  return total;
}

std::optional<long long> parse_large_tail(UView body, long long unit) {
  if (body.empty()) {
    return 0;
  }
  if (body.size() <= 3U &&
      std::all_of(body.begin(), body.end(), [](char32_t value) {
        return value_digit(value).has_value() && !value_zero(value);
      })) {
    long long digits = 0;
    for (char32_t value : body) {
      digits = digits * 10 + (*value_digit(value) - '0');
    }
    long long divisor = 1;
    for (std::size_t index = 0; index < body.size(); ++index) {
      divisor *= 10;
    }
    return digits * (unit / divisor);
  }
  return parse_integer_value(body);
}

std::optional<long long> parse_integer_value(UView body) {
  if (body.empty()) {
    return std::nullopt;
  }
  const std::size_t yi = body.find(U'亿');
  if (yi != UView::npos) {
    const auto left =
        parse_integer_value(yi == 0 ? UView(U"一") : body.substr(0, yi));
    const auto right = parse_large_tail(body.substr(yi + 1U), 100000000LL);
    if (!left || !right ||
        *left >
            (std::numeric_limits<long long>::max() - *right) / 100000000LL) {
      return std::nullopt;
    }
    return *left * 100000000LL + *right;
  }
  const std::size_t wan = body.find(U'万');
  if (wan != UView::npos) {
    const auto left =
        parse_integer_value(wan == 0 ? UView(U"一") : body.substr(0, wan));
    const auto right = parse_large_tail(body.substr(wan + 1U), 10000LL);
    if (!left || !right ||
        *left > (std::numeric_limits<long long>::max() - *right) / 10000LL) {
      return std::nullopt;
    }
    return *left * 10000LL + *right;
  }
  return parse_section_value(body);
}

std::string digit_string(UView body, bool sequence) {
  std::string output;
  output.reserve(body.size());
  for (char32_t value : body) {
    const auto digit = sequence ? sequence_digit(value) : value_digit(value);
    if (!digit.has_value()) {
      return {};
    }
    output.push_back(*digit);
  }
  return output;
}

bool short_large_tail(UView body) {
  return !body.empty() && body.size() <= 3U &&
         std::all_of(body.begin(), body.end(), [](char32_t value) {
           return value_digit(value).has_value() && !value_zero(value);
         });
}

bool wan_level_tail(UView body) {
  return !body.empty() && body.back() == U'万' &&
         std::all_of(body.begin(), body.end(),
                     [](char32_t value) { return !value_zero(value); });
}

std::string format_large_unit(long long value, long long unit,
                              const std::string &suffix) {
  const long long whole = value / unit;
  const long long remainder = value % unit;
  if (remainder == 0) {
    return std::to_string(whole) + suffix;
  }
  const std::size_t width = std::to_string(unit).size() - 1U;
  std::string fraction = std::to_string(remainder);
  if (fraction.size() < width) {
    fraction.insert(fraction.begin(), width - fraction.size(), '0');
  }
  while (!fraction.empty() && fraction.back() == '0') {
    fraction.pop_back();
  }
  return std::to_string(whole) + "." + fraction + suffix;
}

std::optional<std::string> convert_integer_part(UView body) {
  if (body.empty() || !is_structured_body(body)) {
    return std::nullopt;
  }
  if (std::all_of(body.begin(), body.end(), [](char32_t value) {
        return value_digit(value).has_value();
      })) {
    return digit_string(body, false);
  }
  const auto value = parse_integer_value(body);
  return value ? std::optional<std::string>(std::to_string(*value))
               : std::nullopt;
}

std::optional<std::string> convert_large_display(UView body,
                                                 bool force_full_number) {
  if (force_full_number) {
    return convert_integer_part(body);
  }
  const std::size_t yi = body.find(U'亿');
  if (yi != UView::npos) {
    const auto left =
        convert_integer_part(yi == 0 ? UView(U"一") : body.substr(0, yi));
    if (!left) {
      return std::nullopt;
    }
    const UView right = body.substr(yi + 1U);
    if (right.empty()) {
      return *left + "亿";
    }
    const auto total = parse_integer_value(body);
    if (!total) {
      return std::nullopt;
    }
    if (short_large_tail(right) || wan_level_tail(right)) {
      return format_large_unit(*total, 100000000LL, "亿");
    }
    return std::to_string(*total);
  }
  const std::size_t wan = body.find(U'万');
  if (wan == UView::npos) {
    return std::nullopt;
  }
  if (wan == 0) {
    return std::nullopt;
  }
  const auto left = convert_integer_part(body.substr(0, wan));
  if (!left) {
    return std::nullopt;
  }
  const UView right = body.substr(wan + 1U);
  if (right.empty()) {
    return *left + "万";
  }
  const auto total = parse_integer_value(body);
  if (!total) {
    return std::nullopt;
  }
  if (short_large_tail(right)) {
    return format_large_unit(*total, 10000LL, "万");
  }
  return std::to_string(*total);
}

std::optional<std::string> convert_structured(UView body,
                                              bool force_full_number = false) {
  if (body.empty()) {
    return std::nullopt;
  }
  const std::size_t point = body.find(U'点');
  if (point != UView::npos) {
    const UView integer = point == 0 ? UView(U"零") : body.substr(0, point);
    const UView fraction = body.substr(point + 1U);
    if (fraction.empty() ||
        !std::all_of(fraction.begin(), fraction.end(), [](char32_t value) {
          return value_digit(value).has_value();
        })) {
      return std::nullopt;
    }
    const auto integer_value = convert_integer_part(integer);
    if (!integer_value) {
      return std::nullopt;
    }
    return *integer_value + "." + digit_string(fraction, false);
  }
  if (const auto display = convert_large_display(body, force_full_number)) {
    return display;
  }
  return convert_integer_part(body);
}

std::optional<std::string> convert_general(UView body,
                                           bool force_full_number = false) {
  if (is_digit_sequence(body)) {
    return digit_string(body, true);
  }
  return convert_structured(body, force_full_number);
}

std::optional<std::string> convert_time_minute(UView body) {
  if (body.empty() || !is_structured_body(body)) {
    return std::nullopt;
  }
  if (std::all_of(body.begin(), body.end(), [](char32_t value) {
        return value_digit(value).has_value();
      })) {
    return digit_string(body, false);
  }
  const auto value = parse_integer_value(body);
  if (!value) {
    return std::nullopt;
  }
  if (value_zero(body.front()) && *value < 10) {
    return "0" + std::to_string(*value);
  }
  return std::to_string(*value);
}

const std::vector<UString> kApproxMeasureTokens = {
    U"小时", U"分钟", U"秒钟", U"公里", U"厘米", U"毫米", U"公斤",
    U"千克", U"毫升", U"页",   U"章",   U"节",   U"集",   U"篇",
    U"句",   U"行",   U"列",   U"版",   U"代",   U"层",   U"楼",
    U"次",   U"笔",   U"项",   U"套",   U"场",   U"遍",   U"周",
    U"天",   U"年",   U"月",   U"日",   U"号",   U"点",   U"分",
    U"秒",   U"米",   U"人",   U"斤",   U"元",   U"块",   U"度",
    U"折",   U"个",   U"岁",   U"下",   U"%",    U"％",   U"℃"};
const std::vector<UString> kCountClassifiers = {U"个", U"盒", U"件",
                                                U"行", U"关", U"台"};
const std::vector<UString> kNumericSuffixes = {U"以内", U"以上", U"以下",
                                               U"左右"};
const std::vector<UString> kSemanticPrefixes = {
    U"库存", U"库存还有", U"宽度",     U"高度", U"内存限制",
    U"限制", U"评分",     U"日志保留", U"保留", U"活动持续",
    U"持续", U"总价",     U"预算",     U"坐标", U"逗号"};
const std::vector<UString> kQuantityUnits = {
    U"平方米", U"立方米", U"小时", U"分钟", U"秒钟", U"公里", U"厘米", U"毫米",
    U"公斤",   U"毫升",   U"平方", U"立方", U"页",   U"章",   U"节",   U"集",
    U"篇",     U"列",     U"版",   U"代",   U"层",   U"楼",   U"次",   U"笔",
    U"项",     U"套",     U"场",   U"遍",   U"周",   U"米",   U"人",   U"斤",
    U"元",     U"块",     U"度",   U"折",   U"秒",   U"克",   U"兆",   U"岁",
    U"%",      U"％",     U"℃"};
const std::vector<UString> kDateSuffixes = {U"年", U"月", U"日", U"号"};
const std::vector<UString> kTimePointSuffixes = {U"点钟",   U"点整", U"点半",
                                                 U"点过",   U"点前", U"点后",
                                                 U"点左右", U"点多", U"点"};
const std::vector<UString> kDigitSequencePrefixes = {
    U"手机号", U"手机号码", U"电话号码", U"电话",     U"验证码", U"校验码",
    U"编号",   U"号码",     U"账号",     U"帐号",     U"账户",   U"工号",
    U"单号",   U"订单号",   U"订单",     U"快递单号", U"快递",   U"邮编",
    U"端口号", U"端口",     U"进程号",   U"状态码",   U"房号",   U"房间号",
    U"尾号",   U"频道号",   U"工位编号", U"车牌",     U"ID",     U"id"};
const std::vector<UString> kFullDisplayPrefixes = [] {
  std::vector<UString> result = kDigitSequencePrefixes;
  result.push_back(U"最大连接数");
  result.push_back(U"连接数");
  return result;
}();
const std::vector<UString> kDigitSequenceSuffixes = {U"端口", U"错误"};
const std::vector<UString> kPlaceSuffixes = {U"号会议室", U"号机房", U"号门",
                                             U"号位"};
const std::vector<UString> kNumericPrefixes = {
    U"等于", U"等於", U"设置成", U"设置为", U"设为",  U"改成",
    U"改为", U"调成", U"调到",   U"调整成", U"调整为"};
const std::vector<UString> kMathPrefixes = {U"乘以", U"除以", U"等于",
                                            U"等於", U"加",   U"减"};
const std::vector<UString> kMathSuffixes = {U"乘以", U"除以", U"等于", U"等於",
                                            U"次方", U"加",   U"减"};
const std::vector<UString> kTimeEventSuffixes = {
    U"开会", U"更新", U"上线", U"发布", U"提醒", U"重试", U"执行", U"开服"};
const std::vector<UString> kDurationHalfSuffixes = {U"分半"};
const UString kContextPrefixChars = U"到至和或比乘除加减约近超共用隔差";
const UString kContextSuffixChars = U"到至和或比乘除加减多余前后";
const UString kContextSeparators = U" \t\r\n:：#-—_,，是为";
const UString kStandalonePrefix = U" \t\r\n([{\"'“‘（【《「『";
const UString kStandaloneSuffix =
    U" \t\r\n.,!?;:，。！？；：、)}\"'”’）】》」』…";

UView rstrip_chars(UView text, UView characters) {
  std::size_t end = text.size();
  while (end > 0 && characters.find(text[end - 1]) != UView::npos) {
    --end;
  }
  return text.substr(0, end);
}

bool all_chars_in(UView text, UView characters) {
  return std::all_of(text.begin(), text.end(), [&](char32_t value) {
    return characters.find(value) != UView::npos;
  });
}

bool approximate_pattern(UView body, UView next) {
  if (body.empty() || body.find(U'点') != UView::npos) {
    return false;
  }
  const bool has_unit = std::any_of(body.begin(), body.end(), [](char32_t ch) {
    return small_unit(ch).has_value() || large_unit(ch).has_value();
  });
  const auto nonzero = [](char32_t ch) {
    const auto digit = value_digit(ch);
    return digit.has_value() && *digit != '0';
  };
  if (has_unit) {
    for (std::size_t index = 0; index < body.size(); ++index) {
      if (!(small_unit(body[index]) || large_unit(body[index]))) {
        continue;
      }
      std::size_t before = 0;
      for (std::size_t cursor = index; cursor > 0 && nonzero(body[cursor - 1]);
           --cursor) {
        ++before;
      }
      std::size_t after = 0;
      for (std::size_t cursor = index + 1U;
           cursor < body.size() && nonzero(body[cursor]); ++cursor) {
        ++after;
      }
      if (before >= 2U || after >= 2U) {
        return true;
      }
    }
    return false;
  }
  if (body.size() != 2U && body.size() != 3U) {
    return false;
  }
  if (std::any_of(body.begin(), body.end(), zero_digit)) {
    return false;
  }
  if (std::all_of(body.begin(), body.end(),
                  [&](char32_t value) { return value == body.front(); })) {
    return false;
  }
  return starts_with_any(next, kApproxMeasureTokens);
}

bool has_digit_sequence_context(UView previous) {
  return ends_with_any(rstrip_chars(previous, kContextSeparators),
                       kDigitSequencePrefixes);
}

bool has_full_display_context(UView previous) {
  return ends_with_any(rstrip_chars(previous, kContextSeparators),
                       kFullDisplayPrefixes);
}

bool has_numeric_prefix_context(UView previous) {
  const UView stripped = rstrip_chars(previous, kContextSeparators);
  if (ends_with_any(stripped, kNumericPrefixes)) {
    return true;
  }
  return !previous.empty() &&
         kContextPrefixChars.find(previous.back()) != UString::npos;
}

bool has_semantic_prefix(UView previous) {
  return ends_with_any(rstrip_chars(previous, kContextSeparators),
                       kSemanticPrefixes);
}

bool has_operator_context(UView previous, UView next) {
  return (!previous.empty() &&
          kContextPrefixChars.find(previous.back()) != UString::npos) ||
         (!next.empty() &&
          kContextSuffixChars.find(next.front()) != UString::npos);
}

bool has_math_context(UView previous, UView next) {
  const UView raw = rstrip_chars(previous, U" \t\r\n:：#-—_,，");
  const UView stripped = rstrip_chars(previous, kContextSeparators);
  if (ends_with_any(stripped, kMathPrefixes) ||
      starts_with_any(next, kMathSuffixes)) {
    return true;
  }
  if (starts_with(next, U"的") &&
      next.substr(0, std::min<std::size_t>(5, next.size())).find(U"次方") !=
          UView::npos) {
    return true;
  }
  const UView tail = raw.size() <= 8U ? raw : raw.substr(raw.size() - 8U);
  return ends_with(raw, U"是") && tail.find(U"次方") != UView::npos;
}

bool previous_date_suffix(UView previous) {
  const UView stripped = rstrip_chars(previous, kContextSeparators);
  return !stripped.empty() &&
         (stripped.back() == U'年' || stripped.back() == U'月');
}

bool date_context(UView body, UView previous, UView next) {
  UView suffix;
  for (const UString &candidate : kDateSuffixes) {
    if (starts_with(next, candidate)) {
      suffix = candidate;
      break;
    }
  }
  if (suffix.empty()) {
    return false;
  }
  if (suffix == U"月" && starts_with(next, U"月天")) {
    return false;
  }
  if (suffix == U"年") {
    return body.size() >= 2U || contains_unit(body);
  }
  if (suffix == U"月") {
    return true;
  }
  return body.size() >= 2U || previous_date_suffix(previous);
}

bool time_prefix_context(UView previous) {
  static const std::vector<UString> prefixes = {
      U"凌晨", U"早上", U"上午", U"中午", U"下午", U"晚上",
      U"夜里", U"今天", U"明天", U"后天", U"昨天"};
  return ends_with_any(rstrip_chars(previous, kContextSeparators), prefixes);
}

bool time_context(UView next) {
  if (!starts_with_any(next, kTimePointSuffixes)) {
    return false;
  }
  const UView tail = next.size() > 0 ? next.substr(1) : UView();
  if (tail.empty()) {
    return true;
  }
  static const std::vector<UString> direct = {U"钟", U"整", U"半",   U"过",
                                              U"前", U"后", U"左右", U"多"};
  if (starts_with_any(tail, direct) ||
      starts_with_any(tail, kTimeEventSuffixes)) {
    return true;
  }
  return value_digit(tail.front()).has_value();
}

bool positive_context(UView previous, UView next) {
  const UView stripped = rstrip_chars(previous, kContextSeparators);
  return ends_with(stripped, U"到") || ends_with(stripped, U"至") ||
         has_math_context(previous, next);
}

bool special_fixed_phrase(UView body, UView previous, UView next) {
  if (body == U"一" && ends_with(previous, U"更上") &&
      starts_with(next, U"层楼")) {
    return true;
  }
  if (body == U"十" && previous.empty() && starts_with(next, U"年生死")) {
    return true;
  }
  if (ends_with(previous, U"波") && starts_with(next, U"折")) {
    return true;
  }
  if (ends_with(previous, U"番") && starts_with(next, U"次")) {
    return true;
  }
  if (!starts_with(next, U"斤")) {
    return false;
  }
  const UView tail = next.substr(1);
  return tail.size() >= 2U && tail.back() == U'两' &&
         std::all_of(tail.begin(), tail.end() - 1, [](char32_t value) {
           return value_digit(value).has_value();
         });
}

bool standalone_context(UView previous, UView next) {
  return all_chars_in(previous, kStandalonePrefix) &&
         all_chars_in(next, kStandaloneSuffix);
}

bool should_convert_digit_sequence(UView body, UView previous, UView next) {
  if (has_digit_sequence_context(previous)) {
    return true;
  }
  if (body.size() >= 3U && starts_with_any(next, kDigitSequenceSuffixes)) {
    return true;
  }
  if (date_context(body, previous, next) ||
      starts_with_any(next, kPlaceSuffixes) || time_context(next) ||
      starts_with_any(next, kDurationHalfSuffixes) ||
      has_math_context(previous, next) ||
      has_operator_context(previous, next) ||
      starts_with_any(next, kCountClassifiers) ||
      starts_with_any(next, kNumericSuffixes) ||
      has_semantic_prefix(previous) || starts_with_any(next, kQuantityUnits) ||
      has_numeric_prefix_context(previous)) {
    return true;
  }
  if (body.size() >= 3U && standalone_context(previous, next)) {
    return true;
  }
  return body.size() >= 3U &&
         (starts_with(next, U"共") || starts_with(next, U"一共"));
}

bool valid_decimal(UView body) {
  const std::size_t point = body.find(U'点');
  return point != UView::npos && point > 0 && point + 1U < body.size() &&
         is_structured_body(body.substr(0, point)) &&
         std::all_of(
             body.begin() + static_cast<std::ptrdiff_t>(point + 1U), body.end(),
             [](char32_t value) { return value_digit(value).has_value(); });
}

bool should_convert_general(UView body, UView previous, UView next,
                            bool fixed_protected) {
  if (body.empty()) {
    return false;
  }
  if (body.find(U'点') != UView::npos) {
    return valid_decimal(body);
  }
  if (fixed_protected || special_fixed_phrase(body, previous, next)) {
    return false;
  }
  if (body == U"一" && next == U"点" && !time_prefix_context(previous)) {
    return false;
  }
  if (starts_with(next, U"两") || approximate_pattern(body, next)) {
    return false;
  }
  if (is_digit_sequence(body)) {
    return should_convert_digit_sequence(body, previous, next);
  }
  if (!is_structured_body(body)) {
    return false;
  }
  const bool spoken_large =
      std::any_of(body.begin(), body.end(),
                  [](char32_t value) { return large_unit(value).has_value(); });
  if (spoken_large) {
    const auto first =
        std::find_if(body.begin(), body.end(), [](char32_t value) {
          return large_unit(value).has_value();
        });
    if (first != body.begin() && first + 1 != body.end()) {
      return true;
    }
  }
  if (has_full_display_context(previous) ||
      (standalone_context(previous, next) && contains_unit(body) &&
       !approximate_pattern(body, next)) ||
      date_context(body, previous, next) ||
      starts_with_any(next, kPlaceSuffixes) || time_context(next) ||
      starts_with_any(next, kDurationHalfSuffixes) ||
      has_math_context(previous, next) ||
      has_operator_context(previous, next) ||
      starts_with_any(next, kCountClassifiers) ||
      starts_with_any(next, kNumericSuffixes) ||
      has_semantic_prefix(previous) || starts_with_any(next, kQuantityUnits) ||
      has_numeric_prefix_context(previous)) {
    return true;
  }
  return false;
}

struct Candidate {
  enum class Type {
    money,
    negative_percent,
    percent,
    permille,
    below_zero,
    time,
    ordinal,
    positive,
    negative,
    general,
  };

  Candidate(Type candidate_type, std::size_t candidate_start,
            std::size_t candidate_end, UString candidate_first,
            UString candidate_second = {}, char32_t candidate_unit = 0,
            bool candidate_sign = false)
      : type(candidate_type), start(candidate_start), end(candidate_end),
        first(std::move(candidate_first)), second(std::move(candidate_second)),
        unit(candidate_unit), sign(candidate_sign) {}

  Type type = Type::general;
  std::size_t start = 0;
  std::size_t end = 0;
  UString first;
  UString second;
  char32_t unit = 0;
  bool sign = false;
};

std::size_t consume_integer(UView text, std::size_t offset) {
  std::size_t end = offset;
  while (end < text.size() && integer_char(text[end])) {
    ++end;
  }
  return end;
}

std::size_t consume_general(UView text, std::size_t offset) {
  std::size_t end = offset;
  while (end < text.size() && general_char(text[end])) {
    ++end;
  }
  if (end < text.size() && text[end] == U'点') {
    std::size_t fraction_end = end + 1U;
    while (fraction_end < text.size() &&
           value_digit(text[fraction_end]).has_value()) {
      ++fraction_end;
    }
    if (fraction_end > end + 1U &&
        (fraction_end >= text.size() || (!small_unit(text[fraction_end]) &&
                                         !large_unit(text[fraction_end])))) {
      end = fraction_end;
    }
  }
  return end;
}

std::optional<Candidate> match_candidate(UView text, std::size_t offset) {
  const auto match_prefixed =
      [&](UView prefix, Candidate::Type type) -> std::optional<Candidate> {
    if (!starts_with(text.substr(offset), prefix)) {
      return std::nullopt;
    }
    const std::size_t body_start = offset + prefix.size();
    const std::size_t body_end = consume_general(text, body_start);
    if (body_end == body_start) {
      return std::nullopt;
    }
    return Candidate{type, offset, body_end,
                     UString(text.substr(body_start, body_end - body_start))};
  };

  std::size_t money_body = offset;
  bool money_negative = false;
  if (money_body < text.size() && text[money_body] == U'负') {
    money_negative = true;
    ++money_body;
  }
  const std::size_t money_end = consume_integer(text, money_body);
  if (money_end > money_body && money_end + 1U < text.size() &&
      (text[money_end] == U'块' || text[money_end] == U'元') &&
      value_digit(text[money_end + 1U]).has_value()) {
    return Candidate{Candidate::Type::money,
                     offset,
                     money_end + 2U,
                     UString(text.substr(money_body, money_end - money_body)),
                     UString(1, text[money_end + 1U]),
                     text[money_end],
                     money_negative};
  }
  if (const auto match =
          match_prefixed(U"负百分之", Candidate::Type::negative_percent)) {
    return match;
  }
  if (const auto match = match_prefixed(U"百分之", Candidate::Type::percent)) {
    return match;
  }
  if (const auto match = match_prefixed(U"千分之", Candidate::Type::permille)) {
    return match;
  }
  if (const auto match = match_prefixed(U"零下", Candidate::Type::below_zero)) {
    return match;
  }

  const std::size_t hour_end = consume_integer(text, offset);
  if (hour_end > offset && hour_end < text.size() && text[hour_end] == U'点') {
    const std::size_t minute_start = hour_end + 1U;
    const std::size_t minute_end = consume_integer(text, minute_start);
    if (minute_end > minute_start && minute_end < text.size() &&
        text[minute_end] == U'分') {
      return Candidate{
          Candidate::Type::time, offset, minute_end + 1U,
          UString(text.substr(offset, hour_end - offset)),
          UString(text.substr(minute_start, minute_end - minute_start))};
    }
  }
  if (offset < text.size() && text[offset] == U'第') {
    const std::size_t end = consume_integer(text, offset + 1U);
    if (end > offset + 1U) {
      return Candidate{Candidate::Type::ordinal, offset, end,
                       UString(text.substr(offset + 1U, end - offset - 1U))};
    }
  }
  if (offset < text.size() && text[offset] == U'正') {
    const std::size_t end = consume_general(text, offset + 1U);
    if (end > offset + 1U) {
      return Candidate{Candidate::Type::positive, offset, end,
                       UString(text.substr(offset + 1U, end - offset - 1U))};
    }
  }
  if (offset < text.size() && text[offset] == U'负') {
    const std::size_t end = consume_general(text, offset + 1U);
    if (end > offset + 1U) {
      return Candidate{Candidate::Type::negative, offset, end,
                       UString(text.substr(offset + 1U, end - offset - 1U))};
    }
  }
  const std::size_t end = consume_general(text, offset);
  if (end > offset) {
    return Candidate{Candidate::Type::general, offset, end,
                     UString(text.substr(offset, end - offset))};
  }
  return std::nullopt;
}

std::optional<UString> replacement_for(const Candidate &candidate, UView source,
                                       const std::vector<Span> &protected_spans,
                                       const std::vector<Span> &fixed_spans) {
  const UString original(
      source.substr(candidate.start, candidate.end - candidate.start));
  if (overlaps(candidate.start, candidate.end, protected_spans)) {
    return original;
  }
  const UView previous = source.substr(0, candidate.start);
  const UView next = source.substr(candidate.end);
  const auto encoded =
      [](const std::optional<std::string> &value) -> std::optional<UString> {
    return value ? std::optional<UString>(decode_utf8(*value)) : std::nullopt;
  };
  switch (candidate.type) {
  case Candidate::Type::money: {
    const auto whole = encoded(convert_structured(candidate.first));
    const auto fraction = value_digit(candidate.second.front());
    if (!whole || !fraction) {
      return original;
    }
    UString result = candidate.sign ? U"-" : U"";
    result += *whole;
    result += U'.';
    result.push_back(static_cast<char32_t>(*fraction));
    result.push_back(candidate.unit);
    return result;
  }
  case Candidate::Type::negative_percent:
  case Candidate::Type::percent:
  case Candidate::Type::permille:
  case Candidate::Type::below_zero: {
    const auto converted = encoded(convert_structured(candidate.first));
    if (!converted) {
      return original;
    }
    if (candidate.type == Candidate::Type::negative_percent) {
      return UString(U"-") + *converted + U"%";
    }
    if (candidate.type == Candidate::Type::percent) {
      return *converted + U"%";
    }
    if (candidate.type == Candidate::Type::permille) {
      return *converted + U"‰";
    }
    return UString(U"零下") + *converted;
  }
  case Candidate::Type::time: {
    const auto hour = encoded(convert_structured(candidate.first));
    const auto minute = encoded(convert_time_minute(candidate.second));
    if (!hour || !minute) {
      return original;
    }
    return *hour + U"点" + *minute + U"分";
  }
  case Candidate::Type::ordinal: {
    const auto converted = encoded(convert_structured(candidate.first));
    return converted ? std::optional<UString>(UString(U"第") + *converted)
                     : std::optional<UString>(original);
  }
  case Candidate::Type::positive: {
    if (!positive_context(previous, next)) {
      return original;
    }
    const auto converted = encoded(convert_structured(candidate.first));
    return converted ? std::optional<UString>(UString(U"正") + *converted)
                     : std::optional<UString>(original);
  }
  case Candidate::Type::negative: {
    const auto converted = encoded(convert_structured(candidate.first));
    if (!converted) {
      return original;
    }
    if (starts_with(next, U"楼") || starts_with(next, U"层")) {
      return UString(U"负") + *converted;
    }
    return UString(U"-") + *converted;
  }
  case Candidate::Type::general:
    break;
  }

  UView body = candidate.first;
  UString preserved;
  if (starts_with(next, U"共") && body.size() > 1U && body.back() == U'一' &&
      is_digit_sequence(body.substr(0, body.size() - 1U))) {
    body = body.substr(0, body.size() - 1U);
    preserved = U"一";
  }
  const bool fixed = contained_by(candidate.start, candidate.end, fixed_spans);
  if (!should_convert_general(body, previous,
                              UString(preserved) + UString(next), fixed)) {
    return original;
  }
  const auto converted =
      encoded(convert_general(body, has_full_display_context(previous)));
  return converted ? std::optional<UString>(*converted + preserved)
                   : std::optional<UString>(original);
}

UString normalize_chinese_numbers(UView source,
                                  const std::vector<Span> &protected_spans) {
  if (source.empty()) {
    return {};
  }
  const std::vector<Span> fixed_spans = fixed_phrase_spans(source);
  UString output;
  output.reserve(source.size());
  for (std::size_t offset = 0; offset < source.size();) {
    const auto candidate = match_candidate(source, offset);
    if (!candidate) {
      output.push_back(source[offset]);
      ++offset;
      continue;
    }
    output +=
        *replacement_for(*candidate, source, protected_spans, fixed_spans);
    offset = candidate->end;
  }
  return output;
}

bool ascii_digit(char32_t value) { return value >= U'0' && value <= U'9'; }

std::optional<long long> parse_ascii_integer(UView text) {
  if (text.empty() || !std::all_of(text.begin(), text.end(), ascii_digit)) {
    return std::nullopt;
  }
  long long value = 0;
  for (char32_t ch : text) {
    if (value > (std::numeric_limits<long long>::max() - (ch - U'0')) / 10) {
      return std::nullopt;
    }
    value = value * 10 + (ch - U'0');
  }
  return value;
}

UString two_digits(long long value) {
  const std::string encoded =
      value < 10 ? "0" + std::to_string(value) : std::to_string(value);
  return decode_utf8(encoded);
}

struct StyleMatch {
  std::size_t start = 0;
  std::size_t end = 0;
  UString replacement;
};

UString replace_matches(UView source, std::vector<StyleMatch> matches) {
  std::sort(matches.begin(), matches.end(),
            [](const StyleMatch &left, const StyleMatch &right) {
              return left.start < right.start;
            });
  UString output;
  std::size_t cursor = 0;
  for (const StyleMatch &match : matches) {
    if (match.start < cursor) {
      continue;
    }
    output.append(source.substr(cursor, match.start - cursor));
    output.append(match.replacement);
    cursor = match.end;
  }
  output.append(source.substr(cursor));
  return output;
}

UString compact_dates(UView source, const std::vector<Span> &protected_spans) {
  std::vector<StyleMatch> matches;
  for (std::size_t index = 0; index < source.size(); ++index) {
    if (index > 0 && ascii_digit(source[index - 1])) {
      continue;
    }
    std::size_t cursor = index;
    while (cursor < source.size() && ascii_digit(source[cursor]) &&
           cursor - index < 4U) {
      ++cursor;
    }
    if (cursor - index != 4U || cursor >= source.size() ||
        source[cursor] != U'年') {
      continue;
    }
    const UView year_text = source.substr(index, 4U);
    const std::size_t month_start = ++cursor;
    while (cursor < source.size() && ascii_digit(source[cursor]) &&
           cursor - month_start < 2U) {
      ++cursor;
    }
    if (cursor == month_start || cursor >= source.size() ||
        source[cursor] != U'月') {
      continue;
    }
    const UView month_text = source.substr(month_start, cursor - month_start);
    const std::size_t day_start = ++cursor;
    while (cursor < source.size() && ascii_digit(source[cursor]) &&
           cursor - day_start < 2U) {
      ++cursor;
    }
    if (cursor == day_start || cursor >= source.size() ||
        (source[cursor] != U'日' && source[cursor] != U'号')) {
      continue;
    }
    ++cursor;
    if (overlaps(index, cursor, protected_spans)) {
      continue;
    }
    const auto year = parse_ascii_integer(year_text);
    const auto month = parse_ascii_integer(month_text);
    const auto day =
        parse_ascii_integer(source.substr(day_start, cursor - day_start - 1U));
    if (!year || !month || !day) {
      continue;
    }
    UString replacement = decode_utf8(std::to_string(*year));
    if (replacement.size() < 4U) {
      replacement.insert(replacement.begin(), 4U - replacement.size(), U'0');
    }
    replacement += U"/" + two_digits(*month) + U"/" + two_digits(*day);
    const UView tail = source.substr(cursor);
    static const std::vector<UString> periods = {
        U"凌晨", U"早上", U"上午", U"中午", U"下午", U"傍晚", U"晚上"};
    if (starts_with_any(tail, periods) ||
        (!tail.empty() && ascii_digit(tail.front()))) {
      replacement.push_back(U' ');
    }
    matches.push_back({index, cursor, std::move(replacement)});
    index = cursor - 1U;
  }
  return replace_matches(source, std::move(matches));
}

UString compact_times(UView source, const std::vector<Span> &protected_spans) {
  const std::vector<UString> periods = {U"凌晨", U"早上", U"上午", U"中午",
                                        U"下午", U"傍晚", U"晚上"};
  std::vector<StyleMatch> matches;
  for (std::size_t index = 0; index < source.size(); ++index) {
    std::size_t cursor = index;
    UString period;
    for (const UString &candidate : periods) {
      if (starts_with(source.substr(cursor), candidate)) {
        period = candidate;
        cursor += candidate.size();
        break;
      }
    }
    if (cursor > 0 && cursor == index && index > 0 &&
        ascii_digit(source[index - 1])) {
      continue;
    }
    const std::size_t hour_start = cursor;
    while (cursor < source.size() && ascii_digit(source[cursor]) &&
           cursor - hour_start < 2U) {
      ++cursor;
    }
    if (cursor == hour_start || cursor >= source.size() ||
        source[cursor] != U'点') {
      continue;
    }
    const auto hour_value =
        parse_ascii_integer(source.substr(hour_start, cursor - hour_start));
    ++cursor;
    bool half = false;
    bool has_minutes = false;
    long long minute = 0;
    if (cursor < source.size() && source[cursor] == U'半') {
      half = true;
      minute = 30;
      ++cursor;
    } else {
      const std::size_t minute_start = cursor;
      while (cursor < source.size() && ascii_digit(source[cursor]) &&
             cursor - minute_start < 2U) {
        ++cursor;
      }
      if (cursor > minute_start && cursor < source.size() &&
          source[cursor] == U'分') {
        const auto parsed = parse_ascii_integer(
            source.substr(minute_start, cursor - minute_start));
        if (!parsed) {
          continue;
        }
        minute = *parsed;
        has_minutes = true;
        ++cursor;
      } else {
        cursor = minute_start;
      }
    }
    if (period.empty() && !half && !has_minutes) {
      continue;
    }
    if (!hour_value || *hour_value > 23 || minute > 59 ||
        overlaps(index, cursor, protected_spans)) {
      continue;
    }
    long long hour = *hour_value;
    if ((period == U"下午" || period == U"傍晚") && hour < 12) {
      hour += 12;
    } else if (period == U"晚上") {
      if (hour == 12) {
        hour = 0;
      } else if (hour < 12) {
        hour += 12;
      }
    } else if (period == U"中午" && hour >= 1 && hour < 11) {
      hour += 12;
    } else if (period == U"凌晨" && hour == 12) {
      hour = 0;
    } else if ((period == U"早上" || period == U"上午") && hour == 12) {
      hour = 0;
    }
    matches.push_back(
        {index, cursor, two_digits(hour) + U":" + two_digits(minute)});
    index = cursor - 1U;
  }
  return replace_matches(source, std::move(matches));
}

struct AsciiNumber {
  std::size_t start = 0;
  std::size_t end = 0;
  UString text;
};

std::optional<AsciiNumber> ascii_number_at(UView source, std::size_t index) {
  std::size_t cursor = index;
  if (cursor < source.size() && source[cursor] == U'-') {
    ++cursor;
  }
  const std::size_t digits = cursor;
  while (cursor < source.size() && ascii_digit(source[cursor])) {
    ++cursor;
  }
  if (cursor == digits) {
    return std::nullopt;
  }
  if (cursor < source.size() && source[cursor] == U'.') {
    const std::size_t fraction = ++cursor;
    while (cursor < source.size() && ascii_digit(source[cursor])) {
      ++cursor;
    }
    if (cursor == fraction) {
      --cursor;
    }
  }
  return AsciiNumber{index, cursor,
                     UString(source.substr(index, cursor - index))};
}

UString compact_units(UView source, const std::vector<Span> &protected_spans,
                      bool distances) {
  const std::vector<std::pair<UString, UString>> units =
      distances ? std::vector<std::pair<UString, UString>>{{U"公里", U"km"},
                                                           {U"千米", U"km"},
                                                           {U"厘米", U"cm"},
                                                           {U"毫米", U"mm"},
                                                           {U"米", U"m"}}
                : std::vector<std::pair<UString, UString>>{{U"元", U"¥"},
                                                           {U"块", U"¥"}};
  std::vector<StyleMatch> matches;
  for (std::size_t index = 0; index < source.size(); ++index) {
    if (index > 0 &&
        ((source[index - 1] >= U'A' && source[index - 1] <= U'Z') ||
         (source[index - 1] >= U'a' && source[index - 1] <= U'z') ||
         ascii_digit(source[index - 1]) || source[index - 1] == U'_' ||
         source[index - 1] == U'.' ||
         (!distances &&
          (source[index - 1] == U'¥' || source[index - 1] == U'￥')))) {
      continue;
    }
    const auto number = ascii_number_at(source, index);
    if (!number) {
      continue;
    }
    std::size_t cursor = number->end;
    while (cursor < source.size() &&
           (source[cursor] == U' ' || source[cursor] == U'\t')) {
      ++cursor;
    }
    for (const auto &[unit, symbol] : units) {
      if (!starts_with(source.substr(cursor), unit)) {
        continue;
      }
      const std::size_t end = cursor + unit.size();
      if (end < source.size() &&
          ((source[end] >= U'A' && source[end] <= U'Z') ||
           (source[end] >= U'a' && source[end] <= U'z'))) {
        continue;
      }
      if (overlaps(index, end, protected_spans)) {
        break;
      }
      UString replacement;
      if (!distances && !number->text.empty() && number->text.front() == U'-') {
        replacement = U"-¥" + number->text.substr(1);
      } else if (!distances) {
        replacement = U"¥" + number->text;
      } else {
        replacement = number->text + symbol;
      }
      matches.push_back({index, end, std::move(replacement)});
      index = end - 1U;
      break;
    }
  }
  return replace_matches(source, std::move(matches));
}

UString apply_written_style(UString source, const NormalizationConfig &config,
                            const std::vector<Span> &protected_spans) {
  if (config.compact_dates) {
    source = compact_dates(source, protected_spans);
  }
  if (config.compact_times) {
    source = compact_times(source, protected_spans);
  }
  if (config.compact_distances) {
    source = compact_units(source, protected_spans, true);
  }
  if (config.currency_symbols) {
    source = compact_units(source, protected_spans, false);
  }
  return source;
}

} // namespace

class TextNormalizer::Impl {
public:
  explicit Impl(NormalizationConfig config) : config_(config) {}

  std::string normalize(const std::string &text) {
    const Lexicon lexicon = load_lexicon();
    const TermRewriteResult terms = rewrite_terms(lexicon, decode_utf8(text));
    if (terms.text.empty()) {
      return {};
    }
    if (!config_.enabled) {
      return encode_utf8(terms.text);
    }
    const UString numeric =
        normalize_chinese_numbers(terms.text, terms.protected_spans);
    const TermRewriteResult restyled = rewrite_terms(lexicon, numeric);
    return encode_utf8(
        apply_written_style(restyled.text, config_, restyled.protected_spans));
  }

  std::string build_native_hotwords(const std::string &extra) {
    const Lexicon lexicon = load_lexicon();
    std::vector<UString> candidates;
    for (const TermEntry &entry : lexicon.entries) {
      candidates.insert(candidates.end(), entry.hotwords.begin(),
                        entry.hotwords.end());
    }
    std::istringstream stream(extra);
    std::string item;
    while (stream >> item) {
      candidates.push_back(decode_utf8(item));
    }
    std::vector<UString> result;
    std::unordered_set<UString> seen;
    for (const UString &candidate_raw : candidates) {
      const UString candidate = trim(candidate_raw);
      if (candidate.empty() || has_whitespace(candidate) ||
          candidate.size() > 10U) {
        continue;
      }
      const UString key = ascii_casefold(candidate);
      if (!seen.insert(key).second) {
        continue;
      }
      result.push_back(candidate);
      if (result.size() >= 1000U) {
        break;
      }
    }
    UString joined;
    for (std::size_t index = 0; index < result.size(); ++index) {
      if (index > 0) {
        joined.push_back(U' ');
      }
      joined += result[index];
    }
    return encode_utf8(joined);
  }

private:
  Lexicon load_lexicon() {
    std::lock_guard lock(mutex_);
    const std::filesystem::path path = terms_path();
    const auto signature = file_signature(path);
    if (path == cached_path_ && signature == cached_signature_) {
      return cached_lexicon_;
    }
    if (!signature) {
      cached_path_ = path;
      cached_signature_.reset();
      cached_lexicon_ = Lexicon();
      return cached_lexicon_;
    }
    try {
      Lexicon parsed = parse_terms_yaml(path);
      cached_path_ = path;
      cached_signature_ = signature;
      cached_lexicon_ = std::move(parsed);
    } catch (...) {
      if (cached_path_ != path) {
        cached_path_ = path;
        cached_signature_ = signature;
        cached_lexicon_ = Lexicon();
      } else {
        cached_signature_ = signature;
      }
    }
    return cached_lexicon_;
  }

  NormalizationConfig config_;
  std::mutex mutex_;
  std::filesystem::path cached_path_;
  std::optional<FileSignature> cached_signature_;
  Lexicon cached_lexicon_;
};

TextNormalizer::TextNormalizer(NormalizationConfig config)
    : impl_(std::make_unique<Impl>(config)) {}
TextNormalizer::TextNormalizer(TextNormalizer &&) noexcept = default;
TextNormalizer &TextNormalizer::operator=(TextNormalizer &&) noexcept = default;
TextNormalizer::~TextNormalizer() = default;

std::string TextNormalizer::normalize(const std::string &text) {
  return impl_->normalize(text);
}

std::string
TextNormalizer::build_native_hotwords(const std::string &extra_hotwords) {
  return impl_->build_native_hotwords(extra_hotwords);
}

} // namespace vocotype::core
