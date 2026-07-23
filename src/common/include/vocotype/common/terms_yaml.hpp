#pragma once

#include <filesystem>
#include <istream>
#include <string>
#include <string_view>
#include <vector>

namespace vocotype::common {

struct TermDefinition {
  std::string canonical;
  std::vector<std::string> aliases;
  std::vector<std::string> hotwords;
  bool protect = true;
};

struct TermsDocument {
  std::vector<TermDefinition> terms;
  std::vector<std::string> protected_phrases;
};

struct RimeSchemaMetadata {
  std::string schema_id;
  std::string name;
};

[[nodiscard]] TermsDocument parse_terms_yaml(std::istream &input);
[[nodiscard]] TermsDocument parse_terms_yaml_content(std::string_view content);
[[nodiscard]] TermsDocument parse_terms_yaml(const std::filesystem::path &path);

[[nodiscard]] RimeSchemaMetadata
parse_rime_schema_metadata(const std::filesystem::path &path,
                           std::string fallback_id);

} // namespace vocotype::common
