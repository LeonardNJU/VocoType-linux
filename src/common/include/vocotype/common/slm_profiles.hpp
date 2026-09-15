#pragma once

#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>

#include <nlohmann/json.hpp>

#if defined(_WIN32)
#include <process.h>
#else
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace vocotype::common {

using Json = nlohmann::json;

namespace detail {

// 这是旧版 Python 后处理器使用的完整默认提示词。修改时必须保持兼容。
inline constexpr std::string_view kDefaultSlmSystemPrompt =
    R"PROMPT(你是中文语音转写文本的后处理器。

目标：在不改变原意、不新增事实的前提下，做最小必要修正，让文本通顺、自然、易读。

仅允许：
1. 补充/修改/删除标点
2. 调整断句与分句
3. 删除明显口头禅、重复词、无意义语气词
4. 修正明显同音/近音错词、漏字、多字
5. 原句明显不通顺时，做最小限度顺句

核心约束：
- 最小编辑：能不改就不改，能少改就少改
- 含义守恒：不新增事实、细节、观点、结论；不扩写、不解释、不总结
- 技术字符串保真：英文、缩写、模型名、版本号、路径、命令、参数、代码片段按原样优先保留
- 形式保真：技术标识中的大小写、数字、连字符(-)、斜杠(/)、下划线(_)、小数点(.)尽量不改写
- 技术词纠偏：若技术词存在明显转写偏差（同音/近形/单字符误差）且上下文可确定，可做最小字符级修正
- 混排保真：字母数字混合标识保持字母/数字角色，不把字母读音替换成数字或汉字
- 术语优先：若有多个近似写法，优先更常见的技术术语拼写
- 数字规范：默认保留阿拉伯数字，非固定汉语表达不要改成汉字
- 不确定时保留原样，避免误改

输出要求：只输出最终文本，不要任何说明。)PROMPT";

inline constexpr std::string_view kWriterPromptSuffix =
    R"PROMPT(

写作与纠错模板：在上述最小编辑约束下，优先修复语病、搭配和句子衔接；只有原文明确需要时才调整段落和语气。保留作者的事实、立场、语气和专有名词，不主动扩写或改成宣传文案。)PROMPT";

inline bool non_blank(std::string_view value) {
  return value.find_first_not_of(" \t\r\n") != std::string_view::npos;
}

inline std::string require_string(const Json &object, const char *key,
                                  const std::string &where,
                                  bool allow_blank = false) {
  const auto found = object.find(key);
  if (found == object.end() || !found->is_string()) {
    throw std::invalid_argument(where + " 缺少字符串字段 " + key);
  }
  const std::string value = found->get<std::string>();
  if (!allow_blank && !non_blank(value)) {
    throw std::invalid_argument(where + " 字段 " + key + " 不能为空");
  }
  return value;
}

inline std::filesystem::path expand_tilde(std::filesystem::path path) {
  const std::string text = path.string();
  if (text != "~" && !text.starts_with("~/")) {
    return path;
  }
  const char *home = std::getenv("HOME");
  if (home == nullptr || *home == '\0') {
    throw std::runtime_error("HOME 未设置，无法展开 profile 配置路径");
  }
  if (text == "~") {
    return std::filesystem::path(home);
  }
  return std::filesystem::path(home) / text.substr(2);
}

inline long long process_id() noexcept {
#if defined(_WIN32)
  return static_cast<long long>(_getpid());
#else
  return static_cast<long long>(::getpid());
#endif
}

inline void remove_quietly(const std::filesystem::path &path) noexcept {
  std::error_code error;
  std::filesystem::remove(path, error);
}

} // namespace detail

// 返回内置 profile 文档。每次调用返回独立 JSON，调用方可安全修改。
[[nodiscard]] inline Json default_profile_document() {
  const std::string default_prompt(detail::kDefaultSlmSystemPrompt);
  return Json{
      {"version", 1},
      {"active", "default"},
      {"profiles",
       {{"default", {{"name", "默认"}, {"system_prompt", default_prompt}}},
        {"writer",
         {{"name", "写作与纠错"},
          {"system_prompt",
           default_prompt + std::string(detail::kWriterPromptSuffix)}}}}},
      {"vocabulary", Json::array()},
  };
}

// 校验 profile 文档的结构。未知字段有意保留，便于 UI 保存用户扩展数据。
inline void validate_profile_document(const Json &document) {
  if (!document.is_object()) {
    throw std::invalid_argument("profile 文档顶层必须是对象");
  }

  const auto version = document.find("version");
  if (version == document.end() ||
      !(version->is_number_integer() || version->is_number_unsigned()) ||
      version->get<int>() != 1) {
    throw std::invalid_argument("profile 文档 version 必须是 1");
  }

  const std::string active =
      detail::require_string(document, "active", "profile 文档");
  const auto profiles = document.find("profiles");
  if (profiles == document.end() || !profiles->is_object() ||
      profiles->empty()) {
    throw std::invalid_argument("profile 文档 profiles 必须是非空对象");
  }
  const auto active_profile = profiles->find(active);
  if (active_profile == profiles->end()) {
    throw std::invalid_argument("profile 文档 active 未指向已有 profile");
  }
  for (const auto &[profile_id, profile] : profiles->items()) {
    if (!detail::non_blank(profile_id) || !profile.is_object()) {
      throw std::invalid_argument("profile 定义必须是对象且 id 不能为空");
    }
    const std::string where = "profile " + profile_id;
    (void)detail::require_string(profile, "name", where);
    (void)detail::require_string(profile, "system_prompt", where);
  }

  const auto vocabulary = document.find("vocabulary");
  if (vocabulary == document.end() || !vocabulary->is_array()) {
    throw std::invalid_argument("profile 文档 vocabulary 必须是数组");
  }
  std::size_t index = 0;
  for (const auto &entry : *vocabulary) {
    const std::string where = "vocabulary[" + std::to_string(index) + "]";
    if (!entry.is_object()) {
      throw std::invalid_argument(where + " 必须是对象");
    }
    (void)detail::require_string(entry, "canonical", where);
    (void)detail::require_string(entry, "context", where);
    const auto aliases = entry.find("aliases");
    if (aliases == entry.end() || !aliases->is_array()) {
      throw std::invalid_argument(where + " aliases 必须是数组");
    }
    for (const auto &alias : *aliases) {
      if (!alias.is_string() ||
          !detail::non_blank(alias.get<std::string>())) {
        throw std::invalid_argument(where + " aliases 必须是非空字符串数组");
      }
    }
    ++index;
  }
}

// 返回 profile 文件路径。环境变量优先级高于 XDG_CONFIG_HOME。
[[nodiscard]] inline std::filesystem::path slm_profiles_path() {
  if (const char *override_path = std::getenv("VOCOTYPE_PROFILE_CONFIG");
      override_path != nullptr && *override_path != '\0') {
    return detail::expand_tilde(std::filesystem::path(override_path));
  }

  std::filesystem::path config_home;
  if (const char *xdg = std::getenv("XDG_CONFIG_HOME"); xdg != nullptr &&
      *xdg != '\0') {
    config_home = detail::expand_tilde(std::filesystem::path(xdg));
  } else {
    const char *home = std::getenv("HOME");
    if (home == nullptr || *home == '\0') {
      throw std::runtime_error("HOME 未设置，无法确定 profile 配置路径");
    }
    config_home = std::filesystem::path(home) / ".config";
  }
  return config_home / "vocotype" / "slm-profiles.json";
}

// 严格读取一个完整快照。文件缺失时使用内置默认值，其他读取、解析或
// 校验错误直接抛出，供设置界面区分“坏文件”与“尚未创建”。
[[nodiscard]] inline Json load_profile_document_strict() {
  const std::filesystem::path path = slm_profiles_path();
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    std::error_code error;
    const bool exists = std::filesystem::exists(path, error);
    if (!exists && !error) {
      return default_profile_document();
    }
    throw std::runtime_error("无法读取 SLM profile 文件 " + path.string());
  }

  std::ostringstream content;
  content << input.rdbuf();
  if (input.bad()) {
    throw std::runtime_error("读取 SLM profile 文件失败");
  }
  Json document = Json::parse(content.str());
  validate_profile_document(document);
  return document;
}

// 每次调用都读取一个完整快照；坏文件回退默认并只向 stderr 发出警告。
[[nodiscard]] inline Json load_profile_document() {
  std::filesystem::path path;
  try {
    path = slm_profiles_path();
    return load_profile_document_strict();
  } catch (const std::exception &error) {
    std::cerr << "VoCoType: SLM profile 文件不可用"
              << (path.empty() ? std::string() : " " + path.string()) << "（"
              << error.what() << "），回退内置默认配置" << '\n';
    return default_profile_document();
  }
}

// 以临时文件加 rename 写入完整文档，保留未知字段并将文件权限设为 0600。
inline void save_profile_document(const Json &document) {
  validate_profile_document(document);
  const std::filesystem::path path = slm_profiles_path();
  const std::filesystem::path parent = path.parent_path();
  if (!parent.empty()) {
    std::filesystem::create_directories(parent);
  }

  const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
  const std::filesystem::path temporary =
      path.parent_path() /
      (path.filename().string() + ".tmp-" +
       std::to_string(detail::process_id()) + "-" + std::to_string(stamp));
  bool temporary_created = false;
  try {
#if !defined(_WIN32)
    // 临时文件创建时即限定为 0600，避免默认 umask 产生短暂的可读窗口。
    const std::string serialized = document.dump(2) + '\n';
    const int descriptor =
        ::open(temporary.c_str(), O_WRONLY | O_CREAT | O_EXCL,
               static_cast<mode_t>(0600));
    if (descriptor < 0) {
      throw std::system_error(errno, std::generic_category(),
                              "创建临时 profile 文件失败");
    }
    temporary_created = true;
    std::size_t offset = 0;
    try {
      while (offset < serialized.size()) {
        const ssize_t written =
            ::write(descriptor, serialized.data() + offset,
                    serialized.size() - offset);
        if (written > 0) {
          offset += static_cast<std::size_t>(written);
          continue;
        }
        if (written < 0 && errno == EINTR)
          continue;
        if (written == 0)
          throw std::runtime_error("写入临时 profile 文件返回 0");
        throw std::system_error(errno, std::generic_category(),
                                "写入临时 profile 文件失败");
      }
    } catch (...) {
      (void)::close(descriptor);
      throw;
    }
    if (::close(descriptor) != 0) {
      throw std::system_error(errno, std::generic_category(),
                              "关闭临时 profile 文件失败");
    }
#else
    {
      std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
      if (!output.is_open())
        throw std::runtime_error("创建临时 profile 文件失败");
      temporary_created = true;
      output.exceptions(std::ios::badbit | std::ios::failbit);
      output << document.dump(2) << '\n';
      output.flush();
    }
    std::error_code permission_error;
    std::filesystem::permissions(
        temporary,
        std::filesystem::perms::owner_read |
            std::filesystem::perms::owner_write,
        std::filesystem::perm_options::replace, permission_error);
    if (permission_error) {
      throw std::system_error(permission_error,
                               "设置 profile 文件权限失败");
    }
#endif
    std::error_code rename_error;
    std::filesystem::rename(temporary, path, rename_error);
    if (rename_error) {
      throw std::system_error(rename_error, "原子替换 profile 文件失败");
    }
  } catch (...) {
    if (temporary_created)
      detail::remove_quietly(temporary);
    throw;
  }
}

// 追加一条待编辑的空白词汇。调用方在完成填写后再做完整校验和保存。
inline void append_vocabulary_draft(Json &document) {
  if (!document.is_object())
    throw std::invalid_argument("profile 文档顶层必须是对象");
  auto vocabulary = document.find("vocabulary");
  if (vocabulary == document.end() || !vocabulary->is_array())
    throw std::invalid_argument("vocabulary 结构无效");
  vocabulary->push_back(Json{{"canonical", ""},
                             {"aliases", Json::array()},
                             {"context", ""}});
}

// 将当前 profile 与全局语义词汇表拼成一次模型请求的 system prompt。
// 词汇表只提供上下文判断提示，绝不在本地修改用户文本。
[[nodiscard]] inline std::string compose_profile_prompt(const Json &document) {
  validate_profile_document(document);
  const std::string active = document.at("active").get<std::string>();
  const Json &profile = document.at("profiles").at(active);
  std::string prompt = profile.at("system_prompt").get<std::string>();
  const Json &vocabulary = document.at("vocabulary");
  if (vocabulary.empty()) {
    return prompt;
  }

  prompt +=
      "\n\n语义词汇参考（仅供结合上下文判断，不是本地字符串替换规则）：\n";
  prompt +=
      "每条 aliases 只是 canonical 的可能 ASR 误识别候选；仅当上下文明确符合该条适用语境时，才可规范为 canonical；原文语义正确或不确定时必须保留原文。\n";
  for (const auto &entry : vocabulary) {
    prompt += "- canonical: " + entry.at("canonical").get<std::string>();
    prompt += "；aliases: ";
    bool first_alias = true;
    for (const auto &alias : entry.at("aliases")) {
      if (!first_alias) {
        prompt += "、";
      }
      first_alias = false;
      prompt += alias.get<std::string>();
    }
    prompt += "；context: " + entry.at("context").get<std::string>() + "\n";
  }
  prompt += "词汇表只附加上述模型提示上下文，不执行本地字符串替换。";
  return prompt;
}

} // namespace vocotype::common
