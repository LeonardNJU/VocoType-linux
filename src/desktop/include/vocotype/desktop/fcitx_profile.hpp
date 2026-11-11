#pragma once

#include "vocotype/desktop/config.hpp"

#include <filesystem>
#include <map>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace vocotype::desktop {

enum class FcitxAddonState {
  unavailable,
  missing,
  disabled,
  enabled,
};

struct FcitxProfileMigrationResult {
  bool changed = false;
  std::filesystem::path profile;
  std::filesystem::path backup;
  std::size_t removed_entries = 0;
  std::vector<std::pair<std::string, std::string>> restored_defaults;
};

std::optional<std::map<std::string, bool>>
parse_fcitx_addon_states(const Json &payload);

std::vector<std::string>
legacy_fcitx_profile_references(const std::filesystem::path &profile);

FcitxProfileMigrationResult
migrate_legacy_fcitx_profile(const std::filesystem::path &profile);

const char *fcitx_addon_state_name(FcitxAddonState state);

// Fcitx 会把等于内置默认值的配置项写成注释（如 "# EditKey=Control+F9"）。
// 返回键的有效值：明文行优先，其次解引用带默认值的注释行，
// 最后是没有注释文档的 "# Key=" 形式；只有键完全缺失时返回 nullopt。
std::optional<std::string>
fcitx_conf_value(const std::filesystem::path &path, const std::string &key);

} // namespace vocotype::desktop
