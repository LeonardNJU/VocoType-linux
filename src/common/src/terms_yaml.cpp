#include "vocotype/common/terms_yaml.hpp"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace vocotype::common {
namespace {

std::string trim_ascii(std::string value) {
  const auto is_space = [](unsigned char character) {
    return character == ' ' || character == '\t' || character == '\r' ||
           character == '\n';
  };
  const auto first = std::find_if_not(value.begin(), value.end(), [&](char ch) {
    return is_space(static_cast<unsigned char>(ch));
  });
  const auto last =
      std::find_if_not(value.rbegin(), value.rend(), [&](char ch) {
        return is_space(static_cast<unsigned char>(ch));
      }).base();
  if (first >= last) {
    return {};
  }
  return std::string(first, last);
}

std::string strip_yaml_comment(const std::string &line) {
  bool single_quoted = false;
  bool double_quoted = false;
  int brackets = 0;
  for (std::size_t index = 0; index < line.size(); ++index) {
    const char character = line[index];
    if (character == '\'' && !double_quoted) {
      single_quoted = !single_quoted;
    } else if (character == '"' && !single_quoted &&
               (index == 0 || line[index - 1] != '\\')) {
      double_quoted = !double_quoted;
    } else if (!single_quoted && !double_quoted) {
      if (character == '[' || character == '{') {
        ++brackets;
      } else if (character == ']' || character == '}') {
        --brackets;
      } else if (character == '#' && brackets == 0 &&
                 (index == 0 || std::isspace(static_cast<unsigned char>(
                                    line[index - 1])) != 0)) {
        return line.substr(0, index);
      }
    }
  }
  if (single_quoted || double_quoted || brackets != 0) {
    throw std::runtime_error("unterminated YAML scalar");
  }
  return line;
}

std::size_t find_mapping_colon(const std::string &value) {
  bool single_quoted = false;
  bool double_quoted = false;
  int brackets = 0;
  for (std::size_t index = 0; index < value.size(); ++index) {
    const char character = value[index];
    if (character == '\'' && !double_quoted) {
      single_quoted = !single_quoted;
    } else if (character == '"' && !single_quoted &&
               (index == 0 || value[index - 1] != '\\')) {
      double_quoted = !double_quoted;
    } else if (!single_quoted && !double_quoted) {
      if (character == '[' || character == '{') {
        ++brackets;
      } else if (character == ']' || character == '}') {
        --brackets;
      } else if (character == ':' && brackets == 0) {
        return index;
      }
    }
  }
  return std::string::npos;
}

std::pair<std::string, std::string>
parse_mapping_entry(const std::string &content) {
  const std::size_t colon = find_mapping_colon(content);
  if (colon == std::string::npos) {
    throw std::runtime_error("invalid YAML mapping entry");
  }
  return {trim_ascii(content.substr(0, colon)),
          trim_ascii(content.substr(colon + 1))};
}

std::string unquote(std::string value) {
  value = trim_ascii(std::move(value));
  if (value.empty()) {
    return {};
  }
  if (value.front() == '\'' || value.front() == '"') {
    if (value.size() < 2U || value.back() != value.front()) {
      throw std::runtime_error("unterminated YAML string");
    }
    value = value.substr(1, value.size() - 2U);
  }
  return trim_ascii(std::move(value));
}

std::vector<std::string> parse_inline_list(std::string value) {
  value = trim_ascii(std::move(value));
  if (value == "[]") {
    return {};
  }
  if (value.size() < 2U || value.front() != '[' || value.back() != ']') {
    throw std::runtime_error("expected an inline YAML list");
  }
  value = value.substr(1, value.size() - 2U);
  std::vector<std::string> result;
  std::string current;
  bool single_quoted = false;
  bool double_quoted = false;
  for (std::size_t index = 0; index < value.size(); ++index) {
    const char character = value[index];
    if (character == '\'' && !double_quoted) {
      single_quoted = !single_quoted;
      current.push_back(character);
      continue;
    }
    if (character == '"' && !single_quoted &&
        (index == 0 || value[index - 1] != '\\')) {
      double_quoted = !double_quoted;
      current.push_back(character);
      continue;
    }
    if (character == ',' && !single_quoted && !double_quoted) {
      const std::string item = unquote(current);
      if (!item.empty()) {
        result.push_back(item);
      }
      current.clear();
      continue;
    }
    current.push_back(character);
  }
  if (single_quoted || double_quoted) {
    throw std::runtime_error("unterminated YAML string");
  }
  const std::string item = unquote(current);
  if (!item.empty()) {
    result.push_back(item);
  }
  return result;
}

std::optional<bool> parse_bool_like(std::string value) {
  value = trim_ascii(std::move(value));
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char character) {
                   return static_cast<char>(std::tolower(character));
                 });
  if (value == "true" || value == "yes" || value == "on" || value == "1") {
    return true;
  }
  if (value.empty() || value == "false" || value == "no" || value == "off" ||
      value == "0") {
    return false;
  }
  return std::nullopt;
}

std::string parse_sequence_item(const std::string &content) {
  if (content.size() < 2U || content[0] != '-' || content[1] != ' ') {
    throw std::runtime_error("expected a YAML sequence item");
  }
  const std::string value = unquote(content.substr(2));
  if (value.empty()) {
    throw std::runtime_error("YAML sequence item cannot be empty");
  }
  return value;
}

enum class Section { none, terms, replace, protect };
enum class PendingList { none, aliases, hotwords, replace_aliases };

void append_pending(TermsDocument &document, TermDefinition *current,
                    PendingList pending, const std::string &value) {
  if (pending == PendingList::aliases) {
    if (current == nullptr) {
      throw std::runtime_error("aliases list has no term");
    }
    current->aliases.push_back(value);
  } else if (pending == PendingList::hotwords) {
    if (current == nullptr) {
      throw std::runtime_error("hotwords list has no term");
    }
    current->hotwords.push_back(value);
  } else if (pending == PendingList::replace_aliases) {
    if (document.terms.empty()) {
      throw std::runtime_error("replace list has no canonical term");
    }
    document.terms.back().aliases.push_back(value);
  } else {
    throw std::runtime_error("unexpected nested YAML sequence");
  }
}

} // namespace

TermsDocument parse_terms_yaml(std::istream &input) {
  TermsDocument document;
  Section section = Section::none;
  PendingList pending = PendingList::none;
  TermDefinition *current = nullptr;
  bool saw_mapping = false;
  std::string line;
  while (std::getline(input, line)) {
    line = strip_yaml_comment(line);
    if (trim_ascii(line).empty()) {
      continue;
    }
    const std::size_t indent = line.find_first_not_of(' ');
    if (indent == std::string::npos) {
      continue;
    }
    const std::size_t tab = line.find('\t');
    if (tab != std::string::npos && tab <= indent) {
      throw std::runtime_error("tabs are not allowed for YAML indentation");
    }
    const std::string content = trim_ascii(line.substr(indent));
    if (indent == 0) {
      const auto [key, value] = parse_mapping_entry(content);
      if (key.empty()) {
        throw std::runtime_error("YAML mapping key cannot be empty");
      }
      saw_mapping = true;
      current = nullptr;
      pending = PendingList::none;
      if (key == "terms") {
        if (!value.empty() && value != "[]") {
          throw std::runtime_error("terms must be a YAML sequence");
        }
        section = Section::terms;
      } else if (key == "replace") {
        if (!value.empty() && value != "{}") {
          throw std::runtime_error("replace must be a YAML mapping");
        }
        section = Section::replace;
      } else if (key == "protect") {
        if (!value.empty() && value != "[]") {
          throw std::runtime_error("protect must be a YAML sequence");
        }
        section = Section::protect;
      } else {
        section = Section::none;
      }
      continue;
    }

    if (section == Section::terms) {
      if (indent == 2 && content.starts_with("- ")) {
        const auto [key, value] = parse_mapping_entry(content.substr(2));
        if (key != "canonical") {
          throw std::runtime_error("term entry must start with canonical");
        }
        const std::string canonical = unquote(value);
        if (canonical.empty()) {
          throw std::runtime_error("canonical term cannot be empty");
        }
        TermDefinition definition;
        definition.canonical = canonical;
        document.terms.push_back(std::move(definition));
        current = &document.terms.back();
        pending = PendingList::none;
        continue;
      }
      if (current == nullptr) {
        throw std::runtime_error("term field has no canonical entry");
      }
      if (indent >= 6 && content.starts_with("- ")) {
        append_pending(document, current, pending,
                       parse_sequence_item(content));
        continue;
      }
      if (indent != 4) {
        throw std::runtime_error("invalid term entry indentation");
      }
      pending = PendingList::none;
      const auto [key, value] = parse_mapping_entry(content);
      if (key == "aliases" || key == "hotwords") {
        std::vector<std::string> *target =
            key == "aliases" ? &current->aliases : &current->hotwords;
        target->clear();
        if (value.empty()) {
          pending =
              key == "aliases" ? PendingList::aliases : PendingList::hotwords;
        } else {
          *target = parse_inline_list(value);
        }
      } else if (key == "protect") {
        const auto parsed = parse_bool_like(value);
        if (!parsed.has_value()) {
          throw std::runtime_error("protect must be boolean");
        }
        current->protect = *parsed;
      } else if (key == "hotword") {
        const auto parsed = parse_bool_like(value);
        if (!parsed.has_value()) {
          throw std::runtime_error("hotword must be boolean");
        }
        current->hotwords = *parsed
                                ? std::vector<std::string>{current->canonical}
                                : std::vector<std::string>{};
      }
      continue;
    }

    if (section == Section::replace) {
      if (indent == 2) {
        const auto [canonical_raw, value] = parse_mapping_entry(content);
        const std::string canonical = unquote(canonical_raw);
        if (canonical.empty()) {
          throw std::runtime_error("replace canonical cannot be empty");
        }
        TermDefinition definition;
        definition.canonical = canonical;
        document.terms.push_back(std::move(definition));
        current = &document.terms.back();
        current->aliases.clear();
        pending = PendingList::none;
        if (value.empty()) {
          pending = PendingList::replace_aliases;
        } else {
          current->aliases = parse_inline_list(value);
        }
        continue;
      }
      if (indent >= 4 && content.starts_with("- ")) {
        append_pending(document, current, pending,
                       parse_sequence_item(content));
        continue;
      }
      throw std::runtime_error("invalid replace indentation");
    }

    if (section == Section::protect) {
      if (indent != 2) {
        throw std::runtime_error("invalid protect indentation");
      }
      document.protected_phrases.push_back(parse_sequence_item(content));
      continue;
    }
  }
  if (!saw_mapping) {
    throw std::runtime_error("YAML top level must be a mapping");
  }
  return document;
}

TermsDocument parse_terms_yaml_content(std::string_view content) {
  std::istringstream input{std::string(content)};
  return parse_terms_yaml(input);
}

TermsDocument parse_terms_yaml(const std::filesystem::path &path) {
  std::ifstream input(path);
  if (!input) {
    throw std::runtime_error("cannot open terms file");
  }
  return parse_terms_yaml(input);
}

RimeSchemaMetadata parse_rime_schema_metadata(const std::filesystem::path &path,
                                              std::string fallback_id) {
  RimeSchemaMetadata metadata{fallback_id, fallback_id};
  std::ifstream input(path);
  if (!input) {
    return metadata;
  }
  bool in_schema = false;
  std::string line;
  while (std::getline(input, line)) {
    try {
      line = strip_yaml_comment(line);
    } catch (const std::exception &) {
      return metadata;
    }
    if (trim_ascii(line).empty()) {
      continue;
    }
    const std::size_t indent = line.find_first_not_of(' ');
    if (indent == std::string::npos) {
      continue;
    }
    const std::string content = trim_ascii(line.substr(indent));
    if (indent == 0) {
      in_schema = false;
      try {
        const auto [key, value] = parse_mapping_entry(content);
        if (key == "schema" && (value.empty() || value == "{}")) {
          in_schema = true;
        }
      } catch (const std::exception &) {
      }
      continue;
    }
    if (!in_schema) {
      continue;
    }
    try {
      const auto [key, value] = parse_mapping_entry(content);
      const std::string scalar = unquote(value);
      if (key == "schema_id" && !scalar.empty()) {
        metadata.schema_id = scalar;
      } else if (key == "name" && !scalar.empty()) {
        metadata.name = scalar;
      }
    } catch (const std::exception &) {
    }
  }
  if (metadata.schema_id.empty()) {
    metadata.schema_id = fallback_id;
  }
  if (metadata.name.empty()) {
    metadata.name = metadata.schema_id;
  }
  return metadata;
}

} // namespace vocotype::common
