#include "vocotype/desktop/fcitx_profile.hpp"

#include <algorithm>
#include <fstream>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <system_error>
#include <unistd.h>

namespace vocotype::desktop {
namespace {

constexpr const char *kLegacyInputMethod = "vocotype";
const std::regex kSectionPattern(R"(^\s*\[([^\]]+)\]\s*$)");
const std::regex kItemSectionPattern(R"(^Groups/([^/]+)/Items/([0-9]+)$)");
const std::regex kGroupSectionPattern(R"(^Groups/([^/]+)$)");

struct ProfileBlock {
  std::optional<std::string> section;
  std::vector<std::string> lines;
};

std::vector<std::string> split_lines_keep_ends(const std::string &text) {
  std::vector<std::string> lines;
  std::size_t begin = 0;
  while (begin < text.size()) {
    const std::size_t newline = text.find('\n', begin);
    if (newline == std::string::npos) {
      lines.push_back(text.substr(begin));
      break;
    }
    lines.push_back(text.substr(begin, newline - begin + 1));
    begin = newline + 1;
  }
  if (text.empty())
    return {};
  return lines;
}

std::string strip_line_ending(std::string line) {
  while (!line.empty() && (line.back() == '\n' || line.back() == '\r'))
    line.pop_back();
  return line;
}

std::string trim(const std::string &value) {
  const std::size_t first = value.find_first_not_of(" \t\r\n");
  if (first == std::string::npos)
    return {};
  const std::size_t last = value.find_last_not_of(" \t\r\n");
  return value.substr(first, last - first + 1);
}

std::vector<ProfileBlock> profile_blocks(const std::string &text) {
  std::vector<ProfileBlock> blocks;
  std::optional<std::string> section;
  std::vector<std::string> lines;
  for (const auto &line : split_lines_keep_ends(text)) {
    std::smatch match;
    const std::string content = strip_line_ending(line);
    if (std::regex_match(content, match, kSectionPattern)) {
      if (!lines.empty())
        blocks.push_back({section, std::move(lines)});
      section = match[1].str();
      lines = {line};
    } else {
      lines.push_back(line);
    }
  }
  if (!lines.empty())
    blocks.push_back({section, std::move(lines)});
  return blocks;
}

std::optional<std::string> profile_value(const ProfileBlock &block,
                                         const std::string &key) {
  for (std::size_t index = block.section ? 1U : 0U; index < block.lines.size();
       ++index) {
    const std::string stripped = trim(block.lines[index]);
    if (stripped.empty() || stripped.front() == '#' || stripped.front() == ';')
      continue;
    const std::size_t equals = block.lines[index].find('=');
    if (equals == std::string::npos)
      continue;
    if (trim(block.lines[index].substr(0, equals)) == key)
      return trim(block.lines[index].substr(equals + 1));
  }
  return std::nullopt;
}

void replace_profile_value(ProfileBlock &block, const std::string &key,
                           const std::string &value) {
  for (std::size_t index = block.section ? 1U : 0U; index < block.lines.size();
       ++index) {
    const std::size_t equals = block.lines[index].find('=');
    if (equals == std::string::npos ||
        trim(block.lines[index].substr(0, equals)) != key)
      continue;
    const std::string line = block.lines[index];
    std::size_t value_begin = equals + 1;
    while (value_begin < line.size() &&
           (line[value_begin] == ' ' || line[value_begin] == '\t'))
      ++value_begin;
    const std::string prefix = line.substr(0, value_begin);
    const std::string ending =
        line.ends_with("\r\n") ? "\r\n" : (line.ends_with("\n") ? "\n" : "");
    block.lines[index] = prefix + value + ending;
    return;
  }
}

std::string block_newline(const ProfileBlock &block) {
  for (const auto &line : block.lines) {
    if (line.ends_with("\r\n"))
      return "\r\n";
    if (line.ends_with("\n"))
      return "\n";
  }
  return "\n";
}

std::string read_text(const std::filesystem::path &path) {
  std::ifstream input(path, std::ios::binary);
  if (!input)
    throw std::runtime_error("无法读取 Fcitx profile: " + path.string());
  return std::string(std::istreambuf_iterator<char>(input),
                     std::istreambuf_iterator<char>());
}

void write_atomic_preserving_permissions(const std::filesystem::path &path,
                                         const std::string &text) {
  const auto status = std::filesystem::status(path);
  const auto temporary =
      path.parent_path() / ("." + path.filename().string() + ".vocotype-tmp-" +
                            std::to_string(getpid()));
  try {
    {
      std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
      if (!output)
        throw std::runtime_error("无法写入临时 Fcitx profile: " +
                                 temporary.string());
      output << text;
      if (!output)
        throw std::runtime_error("写入临时 Fcitx profile 失败: " +
                                 temporary.string());
    }
    std::filesystem::permissions(temporary, status.permissions());
    std::filesystem::rename(temporary, path);
  } catch (...) {
    std::error_code ignored;
    std::filesystem::remove(temporary, ignored);
    throw;
  }
}

} // namespace

std::optional<std::map<std::string, bool>>
parse_fcitx_addon_states(const Json &payload) {
  if (!payload.is_object() || !payload.contains("data") ||
      !payload["data"].is_array() || payload["data"].empty() ||
      !payload["data"][0].is_array())
    return std::nullopt;
  std::map<std::string, bool> states;
  for (const auto &row : payload["data"][0]) {
    if (!row.is_array() || row.size() < 6U || !row[0].is_string() ||
        row[0].get<std::string>().empty() || !row[5].is_boolean())
      continue;
    states[row[0].get<std::string>()] = row[5].get<bool>();
  }
  return states;
}

std::vector<std::string>
legacy_fcitx_profile_references(const std::filesystem::path &profile) {
  std::error_code error;
  if (!std::filesystem::is_regular_file(profile, error))
    return {};
  const auto blocks = profile_blocks(read_text(profile));
  std::vector<std::string> references;
  for (const auto &block : blocks) {
    if (!block.section)
      continue;
    std::smatch match;
    if (std::regex_match(*block.section, match, kItemSectionPattern) &&
        profile_value(block, "Name") == kLegacyInputMethod) {
      references.push_back(*block.section);
    } else if (std::regex_match(*block.section, match, kGroupSectionPattern) &&
               profile_value(block, "DefaultIM") == kLegacyInputMethod) {
      references.push_back(*block.section + ":DefaultIM");
    }
  }
  return references;
}

FcitxProfileMigrationResult
migrate_legacy_fcitx_profile(const std::filesystem::path &profile) {
  FcitxProfileMigrationResult result;
  result.profile = profile;
  std::error_code error;
  if (!std::filesystem::exists(profile, error))
    return result;
  if (error || !std::filesystem::is_regular_file(profile))
    throw std::runtime_error("Fcitx profile 不是普通文件: " + profile.string());

  const std::string original = read_text(profile);
  auto blocks = profile_blocks(original);
  std::vector<ProfileBlock> retained;
  std::map<std::string, std::vector<std::string>> group_items;

  for (auto &block : blocks) {
    std::smatch match;
    if (block.section &&
        std::regex_match(*block.section, match, kItemSectionPattern)) {
      const std::string group = match[1].str();
      const auto name = profile_value(block, "Name");
      if (name == kLegacyInputMethod) {
        ++result.removed_entries;
        continue;
      }
      if (name && !name->empty())
        group_items[group].push_back(*name);
    }
    retained.push_back(std::move(block));
  }

  std::vector<ProfileBlock> rewritten;
  std::map<std::string, std::size_t> item_indexes;
  std::set<std::string> groups_needing_item;

  for (auto &block : retained) {
    std::smatch group_match;
    const bool group_section =
        block.section &&
        std::regex_match(*block.section, group_match, kGroupSectionPattern);
    if (group_section &&
        profile_value(block, "DefaultIM") == kLegacyInputMethod) {
      const std::string group = group_match[1].str();
      const auto &items = group_items[group];
      std::string fallback = "keyboard-us";
      if (std::find(items.begin(), items.end(), "rime") != items.end()) {
        fallback = "rime";
      } else {
        const auto non_keyboard = std::find_if(
            items.begin(), items.end(), [](const std::string &name) {
              return !name.starts_with("keyboard-");
            });
        if (non_keyboard != items.end())
          fallback = *non_keyboard;
        else if (!items.empty())
          fallback = items.front();
      }
      replace_profile_value(block, "DefaultIM", fallback);
      result.restored_defaults.emplace_back(group, fallback);
      if (items.empty())
        groups_needing_item.insert(group);
    }

    std::smatch item_match;
    if (block.section &&
        std::regex_match(*block.section, item_match, kItemSectionPattern)) {
      const std::string group = item_match[1].str();
      const std::size_t index = item_indexes[group]++;
      const std::string newline = block_newline(block);
      block.section = "Groups/" + group + "/Items/" + std::to_string(index);
      if (!block.lines.empty())
        block.lines[0] = "[" + *block.section + "]" + newline;
    }

    rewritten.push_back(std::move(block));
    if (group_section) {
      const std::string group = group_match[1].str();
      if (groups_needing_item.erase(group) > 0U) {
        const std::string newline = block_newline(rewritten.back());
        ProfileBlock item;
        item.section = "Groups/" + group + "/Items/0";
        item.lines = {newline,
                      "[" + *item.section + "]" + newline,
                      "# Name" + newline,
                      "Name=keyboard-us" + newline,
                      "# Layout" + newline,
                      "Layout=" + newline};
        rewritten.push_back(std::move(item));
        group_items[group] = {"keyboard-us"};
        item_indexes[group] = 1U;
      }
    }
  }

  result.changed =
      result.removed_entries > 0U || !result.restored_defaults.empty();
  if (!result.changed)
    return result;

  std::ostringstream migrated;
  for (const auto &block : rewritten)
    for (const auto &line : block.lines)
      migrated << line;

  result.backup = profile.parent_path() /
                  (profile.filename().string() + ".vocotype-backup");
  if (!std::filesystem::exists(result.backup))
    std::filesystem::copy_file(profile, result.backup,
                               std::filesystem::copy_options::none);
  write_atomic_preserving_permissions(profile, migrated.str());
  return result;
}

const char *fcitx_addon_state_name(FcitxAddonState state) {
  switch (state) {
  case FcitxAddonState::enabled:
    return "enabled";
  case FcitxAddonState::disabled:
    return "disabled";
  case FcitxAddonState::missing:
    return "missing";
  case FcitxAddonState::unavailable:
    return "unavailable";
  }
  return "unavailable";
}

} // namespace vocotype::desktop
