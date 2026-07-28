#include "vocotype/core/voice_edit.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <limits>
#include <set>
#include <stdexcept>
#include <string_view>
#include <unordered_map>
#include <utility>

namespace vocotype::core {
namespace {

constexpr std::string_view kEditSystemPrompt =
    R"PROMPT(你是中文输入框的语音编辑规划器。

你会收到 ASR 识别出的用户指令、输入框全文、光标、锚点、选区和执行能力。ASR 指令可能包含同音词、近音词或错别字；必须结合输入框上下文推断用户真正指向的词和操作，不能机械做字面字符串匹配。

只允许输出一个严格 JSON 对象，不要 Markdown、解释或额外文本，也绝不能输出 null。

可用计划：
1. 修改正文：
{"mode":"replace","new_text":"编辑后的完整输入框全文","record_history":true,"hint":""}
2. 导航、选择、撤销、重做、复制、剪切、粘贴等按键动作：
{"mode":"key_actions","key_actions":[{"key":"left","modifiers":["ctrl"],"repeat":1}],"hint":""}
3. 无法安全执行或无需修改：
{"mode":"no_op","hint":"说明原因"}

key 只能是：left、right、up、down、home、end、pageup、pagedown、backspace、delete、enter、tab、escape、space、a、c、v、x、z。
modifiers 只能是：ctrl、shift、alt、super。repeat 必须是 1 到 100 的整数。

动作语义参考：
- 撤销通常是 ctrl+z，重做通常是 ctrl+shift+z；
- 上一个/下一个词通常是 ctrl+left / ctrl+right；加 shift 表示扩展选区；
- 行首/行尾通常是 home / end；若用户说的是“当前句首”而句首不等于行首，应根据全文和光标计算距离，用 left 的 repeat 精确移动，距离超过 100 时拆成多个动作；
- 全选、复制、剪切、粘贴通常是 ctrl+a / ctrl+c / ctrl+x / ctrl+v。
这些只是执行原语说明，必须由你结合用户自然语言、上下文与光标决定实际计划。

规则：
- 文本替换、删除、翻译、LaTeX 转换、生成评论等使用 replace，并返回完整全文。
- 光标移动、选区、撤销/重做等使用 key_actions；由你根据自然语言意图选择正确按键组合。
- 只做用户要求的最小修改，保留其余文本、格式、代码、路径和技术字符串。
- 如果 ASR 把目标词识别成同音词，优先依据上下文定位实际存在且语义合理的目标。
- 所有字符串字段缺省时写空字符串，不得写 null。)PROMPT";

const std::set<std::string> kAllowedKeys = {
    "left",     "right",     "up",     "down",  "home", "end",    "pageup",
    "pagedown", "backspace", "delete", "enter", "tab",  "escape", "space",
    "a",        "c",         "v",      "x",     "z",
};
const std::set<std::string> kAllowedModifiers = {"ctrl", "shift", "alt",
                                                 "super"};

std::string safe_string(const Json &object, const char *key,
                        const std::string &fallback = "") {
  if (!object.is_object()) {
    return fallback;
  }
  const auto found = object.find(key);
  return found != object.end() && found->is_string() ? found->get<std::string>()
                                                     : fallback;
}

int safe_int(const Json &object, const char *key, int fallback = 0) {
  if (!object.is_object()) {
    return fallback;
  }
  const auto found = object.find(key);
  if (found == object.end()) {
    return fallback;
  }
  try {
    return found->get<int>();
  } catch (const Json::exception &) {
    return fallback;
  }
}

bool safe_bool(const Json &object, const char *key, bool fallback) {
  if (!object.is_object()) {
    return fallback;
  }
  const auto found = object.find(key);
  if (found == object.end()) {
    return fallback;
  }
  if (found->is_boolean()) {
    return found->get<bool>();
  }
  if (found->is_string()) {
    std::string value = found->get<std::string>();
    std::transform(
        value.begin(), value.end(), value.begin(),
        [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    if (value == "1" || value == "true" || value == "yes" || value == "on") {
      return true;
    }
    if (value == "0" || value == "false" || value == "no" || value == "off") {
      return false;
    }
  }
  return fallback;
}

std::string normalized_name(std::string value) {
  std::transform(
      value.begin(), value.end(), value.begin(),
      [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  std::replace(value.begin(), value.end(), '-', '_');
  return value;
}

std::string normalized_key(std::string value) {
  std::transform(
      value.begin(), value.end(), value.begin(),
      [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  value.erase(std::remove(value.begin(), value.end(), '_'), value.end());
  static const std::unordered_map<std::string, std::string> aliases = {
      {"return", "enter"},   {"pgup", "pageup"},        {"pgdn", "pagedown"},
      {"page-up", "pageup"}, {"page-down", "pagedown"},
  };
  const auto found = aliases.find(value);
  return found == aliases.end() ? value : found->second;
}

std::string extract_json_object(std::string text) {
  const std::size_t fence = text.find("```");
  if (fence != std::string::npos) {
    const std::size_t first_line = text.find('\n', fence);
    const std::size_t closing = text.rfind("```");
    if (first_line != std::string::npos && closing != std::string::npos &&
        closing > first_line) {
      text = text.substr(first_line + 1, closing - first_line - 1);
    }
  }

  const std::size_t start = text.find('{');
  if (start == std::string::npos) {
    throw std::runtime_error("模型未返回 JSON 对象");
  }
  int depth = 0;
  bool in_string = false;
  bool escaped = false;
  for (std::size_t index = start; index < text.size(); ++index) {
    const char current = text[index];
    if (in_string) {
      if (escaped) {
        escaped = false;
      } else if (current == '\\') {
        escaped = true;
      } else if (current == '"') {
        in_string = false;
      }
      continue;
    }
    if (current == '"') {
      in_string = true;
    } else if (current == '{') {
      ++depth;
    } else if (current == '}') {
      --depth;
      if (depth == 0) {
        return text.substr(start, index - start + 1);
      }
    }
  }
  throw std::runtime_error("编辑计划 JSON 不完整");
}

} // namespace

VoiceEditPlanner::VoiceEditPlanner(const SlmClient &slm) : slm_(slm) {}

std::string VoiceEditPlanner::build_request_text(
    const std::string &context_text, const std::string &instruction,
    int cursor_pos, int anchor_pos, const std::string &selected_text,
    bool supports_surrounding, const std::string &replace_state) {
  return "ASR 用户指令：" + instruction + "\n" + "surrounding 可用：" +
         (supports_surrounding ? std::string("true") : std::string("false")) +
         "\n全文替换能力：" +
         (replace_state.empty() ? std::string("unknown") : replace_state) +
         "\n光标位置：" + std::to_string(cursor_pos) + "\n锚点位置：" +
         std::to_string(anchor_pos) + "\n选中文本：" + selected_text +
         "\n输入框全文：\n" + context_text +
         "\n请结合全文消解 ASR 同音/近音错误，并只返回严格 JSON 编辑计划。";
}

std::string VoiceEditPlanner::format_failure(const std::string &reason,
                                             const std::string &detail) {
  if (reason == "edit_disabled" || reason == "disabled") {
    return "SLM 编辑未启用";
  }
  if (reason == "timeout") {
    return "SLM 调用失败：请求超时";
  }
  if (reason == "request_error") {
    return "SLM 调用失败：请求错误";
  }
  if (reason == "bad_json") {
    return "SLM 调用失败：响应解析失败";
  }
  if (reason == "bad_edit_plan") {
    return "SLM 调用失败：模型返回的编辑计划格式无效";
  }
  if (reason == "remote_error") {
    return "SLM 调用失败：远端服务返回错误";
  }
  if (reason == "empty_content") {
    return "SLM 调用失败：返回内容为空";
  }
  return detail.empty() ? "SLM 调用失败" : detail;
}

Json VoiceEditPlanner::validate_model_output(const std::string &output,
                                             const std::string &original_text) {
  Json value;
  try {
    value = Json::parse(extract_json_object(output));
  } catch (const std::exception &error) {
    throw std::runtime_error(std::string("编辑计划 JSON 无效：") +
                             error.what());
  }
  if (!value.is_object()) {
    throw std::runtime_error("模型编辑计划顶层必须是对象");
  }

  std::string raw_mode = safe_string(value, "mode");
  if (raw_mode.empty()) {
    raw_mode = safe_string(value, "action");
  }
  std::string mode = normalized_name(raw_mode);
  static const std::unordered_map<std::string, std::string> aliases = {
      {"replace_text", "replace"},
      {"text", "replace"},
      {"keys", "key_actions"},
      {"navigate", "key_actions"},
      {"navigation", "key_actions"},
      {"noop", "no_op"},
      {"none", "no_op"},
  };
  const auto alias = aliases.find(mode);
  if (alias != aliases.end()) {
    mode = alias->second;
  }
  if (mode != "replace" && mode != "key_actions" && mode != "no_op") {
    throw std::runtime_error("未知编辑计划模式：" + (raw_mode.empty()
                                                         ? std::string("(空)")
                                                         : raw_mode));
  }

  const std::string hint = safe_string(value, "hint");
  Json result{{"handled", true},
              {"mode", mode},
              {"hint", hint},
              {"reason", safe_string(value, "reason")},
              {"key_actions", Json::array()}};
  if (mode == "replace") {
    const auto new_text = value.find("new_text");
    const auto legacy_text = value.find("text");
    if (new_text != value.end() && new_text->is_string()) {
      result["new_text"] = new_text->get<std::string>();
    } else if (legacy_text != value.end() && legacy_text->is_string()) {
      result["new_text"] = legacy_text->get<std::string>();
    } else {
      throw std::runtime_error("replace 计划必须返回字符串 new_text");
    }
    result["record_history"] = safe_bool(value, "record_history", true);
    return result;
  }

  if (mode == "key_actions") {
    const auto actions = value.contains("key_actions")
                             ? value.find("key_actions")
                             : value.find("actions");
    if (actions == value.end() || !actions->is_array() || actions->empty()) {
      throw std::runtime_error("key_actions 计划必须包含非空动作数组");
    }
    if (actions->size() > 32U) {
      throw std::runtime_error("key_actions 动作数量超过安全上限 32");
    }
    for (const auto &raw_action : *actions) {
      if (!raw_action.is_object()) {
        throw std::runtime_error("key_actions 中的每一项必须是对象");
      }
      const std::string key = normalized_key(safe_string(raw_action, "key"));
      if (!kAllowedKeys.contains(key)) {
        throw std::runtime_error("不允许的按键动作：" +
                                 (key.empty() ? std::string("(空)") : key));
      }
      Json modifiers = Json::array();
      std::set<std::string> seen;
      const auto raw_modifiers = raw_action.find("modifiers");
      if (raw_modifiers != raw_action.end() && !raw_modifiers->is_null()) {
        if (!raw_modifiers->is_array()) {
          throw std::runtime_error("modifiers 必须是字符串数组");
        }
        for (const auto &item : *raw_modifiers) {
          if (!item.is_string()) {
            throw std::runtime_error("modifiers 必须是字符串数组");
          }
          const std::string modifier = normalized_name(item.get<std::string>());
          if (!kAllowedModifiers.contains(modifier)) {
            throw std::runtime_error("不允许的修饰键：" + modifier);
          }
          if (seen.insert(modifier).second) {
            modifiers.push_back(modifier);
          }
        }
      }
      result["key_actions"].push_back(
          {{"key", key},
           {"modifiers", modifiers},
           {"repeat", std::clamp(safe_int(raw_action, "repeat", 1), 1, 100)}});
    }
    result["new_text"] = "";
    result["record_history"] = false;
    return result;
  }

  result["new_text"] = original_text;
  result["record_history"] = false;
  return result;
}

Json VoiceEditPlanner::plan(const Json &request,
                            const std::string &instruction) const {
  const Json snapshot = request.value("snapshot", Json::object());
  const std::string context = safe_string(snapshot, "text");
  const int maximum = static_cast<int>(std::min<std::size_t>(
      context.size(),
      static_cast<std::size_t>(std::numeric_limits<int>::max())));
  const int cursor =
      std::clamp(safe_int(snapshot, "cursor_pos", 0), 0, maximum);
  const int anchor =
      std::clamp(safe_int(snapshot, "anchor_pos", cursor), 0, maximum);
  std::string selected = safe_string(snapshot, "selected_text");
  if (selected.empty() && cursor != anchor) {
    const int start = std::min(cursor, anchor);
    const int length = std::max(cursor, anchor) - start;
    selected = context.substr(static_cast<std::size_t>(start),
                              static_cast<std::size_t>(length));
  }

  if (!slm_.enabled()) {
    return {{"success", false},
            {"error", "AI 功能尚未启用；普通转录只验证 ASR，不会调用 "
                      "LLM。请先在设置中心启用并测活 AI 端点"},
            {"instruction", instruction},
            {"reason", "slm_disabled"}};
  }
  if (!slm_.edit_enabled()) {
    return {{"success", false},
            {"error", "AI 功能已启用，但 Ctrl+F9 语音编辑开关仍处于关闭状态"},
            {"instruction", instruction},
            {"reason", "edit_disabled"}};
  }
  if (instruction.empty()) {
    return {{"success", false},
            {"error", "未识别到编辑指令，请靠近麦克风后重试"},
            {"reason", "empty_instruction"}};
  }

  const int token_budget =
      std::min(8192, std::max(slm_.edit_max_tokens(),
                              static_cast<int>(std::min<std::size_t>(
                                  context.size() * 2U + 256U, 8192U))));
  const CompletionResult completion =
      slm_.complete(std::string(kEditSystemPrompt),
                    build_request_text(
                        context, instruction, cursor, anchor, selected,
                        request.value("supports_surrounding", true),
                        request.value("replace_state", std::string("unknown"))),
                    token_budget, false);
  if (!completion.success) {
    return {{"success", false},
            {"error", format_failure(completion.reason, completion.error)},
            {"instruction", instruction},
            {"reason", completion.reason}};
  }

  try {
    Json result = validate_model_output(completion.text, context);
    result["success"] = true;
    result["instruction"] = instruction;
    result["expected_text"] = result.value("mode", "") == "replace"
                                  ? result.value("new_text", "")
                                  : context;
    result["reason"] = "ok";
    return result;
  } catch (const std::exception &error) {
    return {{"success", false},
            {"error", format_failure("bad_edit_plan", error.what())},
            {"instruction", instruction},
            {"reason", "bad_edit_plan"}};
  }
}

} // namespace vocotype::core
