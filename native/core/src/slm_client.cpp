#include "vocotype/core/slm_client.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdlib>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string_view>
#include <utility>
#include <vector>

#include <curl/curl.h>

namespace vocotype::core {
namespace {

using Clock = std::chrono::steady_clock;

constexpr std::string_view kSystemPrompt =
    R"PROMPT(你是中文语音转写文本的后处理器。

目标：在不改变原意、不新增事实的前提下，做最小必要修正，让文本通顺、自然、易读。

仅允许修正标点、断句、明显口头禅、重复词、同音错词、漏字和多字。技术字符串、英文、缩写、模型名、版本号、路径、命令、参数和代码片段应优先保持原样。不确定时保留原样。

只输出最终文本，不要解释。)PROMPT";

class CurlGlobal final {
public:
  CurlGlobal() {
    const CURLcode result = curl_global_init(CURL_GLOBAL_DEFAULT);
    if (result != CURLE_OK) {
      throw std::runtime_error("curl_global_init failed");
    }
  }
  ~CurlGlobal() { curl_global_cleanup(); }
};

void ensure_curl_initialized() {
  static const CurlGlobal global;
  (void)global;
}

std::size_t append_body(char *data, std::size_t size, std::size_t count,
                        void *user) {
  const std::size_t bytes = size * count;
  auto *output = static_cast<std::string *>(user);
  output->append(data, bytes);
  return bytes;
}

std::string string_from_content(const Json &value) {
  if (value.is_string()) {
    return value.get<std::string>();
  }
  if (!value.is_array()) {
    return {};
  }
  std::string result;
  for (const auto &part : value) {
    if (part.is_string()) {
      result += part.get<std::string>();
    } else if (part.is_object()) {
      const auto text = part.find("text");
      if (text != part.end()) {
        if (text->is_string()) {
          result += text->get<std::string>();
        } else if (text->is_object()) {
          const auto text_value = text->find("value");
          if (text_value != text->end() && text_value->is_string()) {
            result += text_value->get<std::string>();
          }
        }
      }
    }
  }
  return result;
}

std::string trim(std::string value) {
  const auto first = value.find_first_not_of(" \t\r\n");
  if (first == std::string::npos) {
    return {};
  }
  const auto last = value.find_last_not_of(" \t\r\n");
  return value.substr(first, last - first + 1);
}

std::string ascii_lower(std::string value) {
  std::transform(
      value.begin(), value.end(), value.begin(),
      [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
  return value;
}

bool begins_with_label(const std::string &text,
                       const std::vector<std::string> &labels) {
  const std::string normalized = ascii_lower(trim(text));
  for (const std::string &label : labels) {
    if (normalized.starts_with(ascii_lower(label))) {
      return true;
    }
  }
  return false;
}

std::string remove_think_blocks(std::string text) {
  constexpr std::string_view open = "<think>";
  constexpr std::string_view close = "</think>";
  while (true) {
    const std::size_t start = text.find(open);
    if (start == std::string::npos) {
      break;
    }
    const std::size_t end = text.find(close, start + open.size());
    if (end == std::string::npos) {
      text.erase(start);
      break;
    }
    text.erase(start, end + close.size() - start);
  }
  return text;
}

std::optional<std::size_t> last_final_marker_end(const std::string &text) {
  static const std::vector<std::string> markers = {
      "final answer", "final response", "answer",   "最终答案",
      "最终输出",     "润色结果",       "输出结果", "输出"};
  const std::string folded = ascii_lower(text);
  std::optional<std::size_t> result;
  for (const std::string &marker : markers) {
    const std::string folded_marker = ascii_lower(marker);
    std::size_t offset = 0;
    while (offset < folded.size()) {
      const std::size_t found = folded.find(folded_marker, offset);
      if (found == std::string::npos) {
        break;
      }
      const std::size_t line = text.rfind('\n', found);
      const std::size_t line_start = line == std::string::npos ? 0 : line + 1U;
      const bool only_space =
          std::all_of(text.begin() + static_cast<std::ptrdiff_t>(line_start),
                      text.begin() + static_cast<std::ptrdiff_t>(found),
                      [](unsigned char ch) { return std::isspace(ch) != 0; });
      std::size_t end = found + marker.size();
      while (end < text.size() && (text[end] == ' ' || text[end] == '\t')) {
        ++end;
      }
      if (only_space && end < text.size() &&
          (text[end] == ':' ||
           text.compare(end, std::string("：").size(), "：") == 0)) {
        end += text[end] == ':' ? 1U : std::string("：").size();
        if (!result || end > *result) {
          result = end;
        }
      }
      offset = found + 1U;
    }
  }
  return result;
}

bool reasoning_line(const std::string &line) {
  const std::string value = trim(line);
  if (value.empty()) {
    return true;
  }
  static const std::vector<std::string> prefixes = {"thinking process",
                                                    "thought process",
                                                    "reasoning",
                                                    "analysis",
                                                    "chain of thought",
                                                    "let's think",
                                                    "lets think",
                                                    "step",
                                                    "思考过程",
                                                    "推理过程",
                                                    "分析过程",
                                                    "推理",
                                                    "分析",
                                                    "思路"};
  if (begins_with_label(value, prefixes)) {
    return true;
  }
  if (value.starts_with("-") || value.starts_with("*")) {
    return true;
  }
  std::size_t index = 0;
  while (index < value.size() &&
         std::isdigit(static_cast<unsigned char>(value[index]))) {
    ++index;
  }
  return index > 0 && index < value.size() &&
         (value[index] == '.' || value[index] == ')');
}

std::optional<std::pair<std::string, std::string>>
remote_error(const Json &payload) {
  const auto error = payload.find("error");
  if (error == payload.end()) {
    return std::nullopt;
  }
  if (error->is_object()) {
    const std::string code = error->value(
        "code", payload.value("error_type", std::string("unknown")));
    return std::pair{
        code, error->value("message", std::string("remote provider error"))};
  }
  if (error->is_string() && !trim(error->get<std::string>()).empty()) {
    return std::pair{std::string("unknown"), error->get<std::string>()};
  }
  return std::nullopt;
}

struct CurlCleanup {
  void operator()(CURL *handle) const noexcept { curl_easy_cleanup(handle); }
};

struct HeaderCleanup {
  void operator()(curl_slist *list) const noexcept {
    curl_slist_free_all(list);
  }
};

curl_slist *build_headers(const SlmConfig &config) {
  curl_slist *headers = nullptr;
  headers = curl_slist_append(headers, "Content-Type: application/json");
  std::string api_key = config.api_key;
  if (api_key.empty() && !config.api_key_env.empty()) {
    const char *value = std::getenv(config.api_key_env.c_str());
    if (value != nullptr) {
      api_key = value;
    }
  }
  if (!api_key.empty()) {
    headers = curl_slist_append(headers,
                                ("Authorization: Bearer " + api_key).c_str());
  }
  for (const auto &[key, value] : config.extra_headers.items()) {
    if (value.is_string()) {
      headers = curl_slist_append(
          headers, (key + ": " + value.get<std::string>()).c_str());
    }
  }
  return headers;
}

struct StreamContext {
  const SlmStreamCallback *callback = nullptr;
  std::string raw_body;
  std::string line_buffer;
  std::vector<std::string> data_lines;
  std::string full_content;
  std::string emitted_visible;
  std::string reason;
  std::string error;
  Clock::time_point last_event = Clock::now();
  bool saw_sse = false;
  bool saw_event = false;
  bool done = false;
  bool cancelled = false;
  bool parse_failed = false;
  int idle_timeout_ms = 20000;
};

bool emit_stream(StreamContext &context, SlmStreamEvent event) {
  if (context.callback == nullptr || !*context.callback) {
    return true;
  }
  if (!(*context.callback)(event)) {
    context.cancelled = true;
    context.reason = "cancelled";
    return false;
  }
  return true;
}

std::string extract_delta_payload(const Json &payload) {
  const auto choices = payload.find("choices");
  if (choices != payload.end() && choices->is_array() && !choices->empty()) {
    const Json &first = (*choices)[0];
    if (first.is_object()) {
      for (const char *container_key : {"delta", "message"}) {
        const auto container = first.find(container_key);
        if (container != first.end() && container->is_object()) {
          for (const char *key : {"content", "text"}) {
            const auto value = container->find(key);
            if (value != container->end()) {
              const std::string text = string_from_content(*value);
              if (!text.empty()) {
                return text;
              }
            }
          }
        }
      }
      for (const char *key : {"content", "text"}) {
        const auto value = first.find(key);
        if (value != first.end()) {
          const std::string text = string_from_content(*value);
          if (!text.empty()) {
            return text;
          }
        }
      }
    }
  }
  for (const char *key : {"content", "text"}) {
    const auto value = payload.find(key);
    if (value != payload.end()) {
      const std::string text = string_from_content(*value);
      if (!text.empty()) {
        return text;
      }
    }
  }
  return {};
}

std::string visible_content(const std::string &content) {
  std::string text = remove_think_blocks(content);
  if (const auto marker = last_final_marker_end(text)) {
    return trim(text.substr(*marker));
  }
  static const std::vector<std::string> thinking = {
      "thinking process:", "thought process:", "reasoning:", "analysis:",
      "chain of thought:", "思考过程：",       "思考过程:",  "推理过程：",
      "推理过程:",         "分析过程：",       "分析过程:"};
  if (begins_with_label(text, thinking)) {
    return {};
  }
  return trim(text);
}

bool process_sse_payload(StreamContext &context,
                         const std::string &payload_text) {
  const std::string payload = trim(payload_text);
  if (payload.empty()) {
    return true;
  }
  if (payload == "[DONE]") {
    context.done = true;
    return true;
  }
  try {
    const Json parsed = Json::parse(payload);
    context.saw_event = true;
    context.last_event = Clock::now();
    if (const auto error = remote_error(parsed)) {
      context.reason = "remote_error";
      context.error = error->second;
      return false;
    }
    const std::string delta = extract_delta_payload(parsed);
    if (delta.empty()) {
      return emit_stream(context, {"heartbeat"});
    }
    context.full_content += delta;
    const std::string visible = visible_content(context.full_content);
    if (visible.empty() || visible == context.emitted_visible) {
      return emit_stream(context, {"heartbeat"});
    }
    const std::string increment =
        visible.starts_with(context.emitted_visible)
            ? visible.substr(context.emitted_visible.size())
            : visible;
    context.emitted_visible = visible;
    return emit_stream(context, {"delta", increment, visible, "", "", 0.0});
  } catch (const Json::exception &error) {
    context.reason = "bad_json";
    context.error = error.what();
    context.parse_failed = true;
    return false;
  }
}

bool flush_sse(StreamContext &context) {
  if (context.data_lines.empty()) {
    return true;
  }
  std::string payload;
  for (std::size_t index = 0; index < context.data_lines.size(); ++index) {
    if (index > 0) {
      payload.push_back('\n');
    }
    payload += context.data_lines[index];
  }
  context.data_lines.clear();
  return process_sse_payload(context, payload);
}

bool process_stream_line(StreamContext &context, std::string line) {
  if (!line.empty() && line.back() == '\r') {
    line.pop_back();
  }
  if (line.empty()) {
    return flush_sse(context);
  }
  if (line.starts_with(":")) {
    context.saw_sse = true;
    context.saw_event = true;
    context.last_event = Clock::now();
    return emit_stream(context, {"heartbeat"});
  }
  if (line.starts_with("data:")) {
    context.saw_sse = true;
    std::string data = line.substr(5);
    if (!data.empty() && data.front() == ' ') {
      data.erase(data.begin());
    }
    context.data_lines.push_back(std::move(data));
  }
  return true;
}

std::size_t append_stream(char *data, std::size_t size, std::size_t count,
                          void *user) {
  const std::size_t bytes = size * count;
  auto &context = *static_cast<StreamContext *>(user);
  context.raw_body.append(data, bytes);
  context.line_buffer.append(data, bytes);
  while (true) {
    const std::size_t newline = context.line_buffer.find('\n');
    if (newline == std::string::npos) {
      break;
    }
    std::string line = context.line_buffer.substr(0, newline);
    context.line_buffer.erase(0, newline + 1U);
    if (!process_stream_line(context, std::move(line))) {
      return 0;
    }
  }
  return bytes;
}

int stream_progress(void *user, curl_off_t, curl_off_t, curl_off_t,
                    curl_off_t) {
  auto &context = *static_cast<StreamContext *>(user);
  if (context.cancelled || context.parse_failed ||
      context.reason == "remote_error") {
    return 1;
  }
  const auto idle = std::chrono::duration_cast<std::chrono::milliseconds>(
      Clock::now() - context.last_event);
  if (idle.count() > context.idle_timeout_ms) {
    context.reason = "idle_timeout";
    return 1;
  }
  return 0;
}

} // namespace

SlmClient::SlmClient(SlmConfig config) : config_(std::move(config)) {}

bool SlmClient::enabled() const noexcept { return config_.enabled; }

bool SlmClient::remote_stream() const noexcept { return config_.remote_stream; }

bool SlmClient::edit_enabled() const noexcept {
  return config_.enabled && config_.edit_enabled;
}

int SlmClient::edit_max_tokens() const noexcept {
  return config_.edit_max_tokens;
}

bool SlmClient::should_polish(const std::string &text,
                              int min_chars_override) const {
  const int threshold =
      min_chars_override >= 0 ? min_chars_override : config_.min_chars;
  return config_.enabled &&
         static_cast<int>(trim(text).size()) >= std::max(0, threshold);
}

Json SlmClient::build_payload(const std::string &system_prompt,
                              const std::string &user_text, int max_tokens,
                              bool stream,
                              std::optional<bool> enable_thinking) const {
  Json payload = {
      {"model", config_.model},
      {"messages", Json::array({
                       {{"role", "system"}, {"content", system_prompt}},
                       {{"role", "user"}, {"content", user_text}},
                   })},
      {"stream", stream},
      {"temperature", config_.temperature},
      {"top_p", config_.top_p},
      {"top_k", config_.top_k},
  };
  if (max_tokens > 0) {
    payload["max_tokens"] = max_tokens;
  }
  if (enable_thinking.value_or(config_.enable_thinking)) {
    payload["enable_thinking"] = true;
  }
  for (const auto &[key, value] : config_.extra_body.items()) {
    payload[key] = value;
  }
  return payload;
}

std::string SlmClient::extract_content(const Json &response) {
  const auto choices = response.find("choices");
  if (choices != response.end() && choices->is_array() && !choices->empty()) {
    const Json &first = (*choices)[0];
    if (first.is_object()) {
      const auto message = first.find("message");
      if (message != first.end() && message->is_object()) {
        const auto content = message->find("content");
        if (content != message->end()) {
          const std::string text = string_from_content(*content);
          if (!text.empty()) {
            return strip_thinking_content(text);
          }
        }
      }
      const auto text = first.find("text");
      if (text != first.end()) {
        const std::string parsed = string_from_content(*text);
        if (!parsed.empty()) {
          return strip_thinking_content(parsed);
        }
      }
    }
  }
  for (const char *key : {"output_text", "content", "text"}) {
    const auto found = response.find(key);
    if (found != response.end()) {
      const std::string parsed = string_from_content(*found);
      if (!parsed.empty()) {
        return strip_thinking_content(parsed);
      }
    }
  }
  return {};
}

std::string SlmClient::extract_stream_delta(const Json &response) {
  return extract_delta_payload(response);
}

std::string SlmClient::stream_visible_content(const std::string &content) {
  return visible_content(content);
}

std::string SlmClient::strip_thinking_content(const std::string &content) {
  std::string text = trim(remove_think_blocks(content));
  if (text.empty()) {
    return {};
  }
  if (const auto marker = last_final_marker_end(text)) {
    return trim(text.substr(*marker));
  }
  static const std::vector<std::string> thinking = {
      "thinking process:", "thought process:", "reasoning:", "analysis:",
      "chain of thought:", "思考过程：",       "思考过程:",  "推理过程：",
      "推理过程:",         "分析过程：",       "分析过程:"};
  if (!begins_with_label(text, thinking)) {
    return text;
  }
  std::vector<std::string> lines;
  std::istringstream stream(text);
  std::string line;
  while (std::getline(stream, line)) {
    line = trim(std::move(line));
    if (!line.empty()) {
      lines.push_back(std::move(line));
    }
  }
  for (auto iterator = lines.rbegin(); iterator != lines.rend(); ++iterator) {
    if (!reasoning_line(*iterator)) {
      return *iterator;
    }
  }
  return {};
}

std::string SlmClient::failure_message(const std::string &reason) {
  if (reason == "timeout") {
    return "SLM 调用失败：请求超时";
  }
  if (reason == "request_error") {
    return "SLM 调用失败：请求错误";
  }
  if (reason == "idle_timeout") {
    return "SLM 调用失败：长时间未收到模型输出";
  }
  if (reason == "bad_json") {
    return "SLM 调用失败：响应解析失败";
  }
  if (reason == "remote_error") {
    return "SLM 调用失败：远端服务返回错误（请查看日志）";
  }
  if (reason == "empty_content") {
    return "SLM 调用失败：返回内容为空";
  }
  if (reason == "blank_content") {
    return "SLM 调用失败：润色结果为空";
  }
  if (reason == "thinking_only") {
    return "SLM 调用失败：仅返回思考内容";
  }
  if (reason == "cancelled") {
    return "已取消";
  }
  return reason.empty() ? "SLM 调用失败" : "SLM 调用失败：" + reason;
}

CompletionResult
SlmClient::complete(const std::string &system_prompt,
                    const std::string &user_text, int max_tokens,
                    std::optional<bool> enable_thinking) const {
  CompletionResult result;
  if (!config_.enabled) {
    result.reason = "disabled";
    result.error = "SLM is disabled";
    return result;
  }

  const auto started = Clock::now();
  std::lock_guard request_lock(request_mutex_);
  try {
    ensure_curl_initialized();
    std::unique_ptr<CURL, CurlCleanup> handle(curl_easy_init());
    if (!handle) {
      throw std::runtime_error("curl_easy_init returned null");
    }
    const std::string payload =
        build_payload(system_prompt, user_text, std::max(1, max_tokens), false,
                      enable_thinking)
            .dump();
    std::string body;
    char error_buffer[CURL_ERROR_SIZE] = {};
    curl_easy_setopt(handle.get(), CURLOPT_URL, config_.endpoint.c_str());
    curl_easy_setopt(handle.get(), CURLOPT_POST, 1L);
    curl_easy_setopt(handle.get(), CURLOPT_POSTFIELDS, payload.data());
    curl_easy_setopt(handle.get(), CURLOPT_POSTFIELDSIZE,
                     static_cast<long>(payload.size()));
    curl_easy_setopt(handle.get(), CURLOPT_WRITEFUNCTION, append_body);
    curl_easy_setopt(handle.get(), CURLOPT_WRITEDATA, &body);
    curl_easy_setopt(handle.get(), CURLOPT_TIMEOUT_MS,
                     static_cast<long>(config_.timeout_ms));
    curl_easy_setopt(handle.get(), CURLOPT_CONNECTTIMEOUT_MS,
                     static_cast<long>(std::min(config_.timeout_ms, 5000)));
    curl_easy_setopt(handle.get(), CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(handle.get(), CURLOPT_ERRORBUFFER, error_buffer);
    std::unique_ptr<curl_slist, HeaderCleanup> headers(build_headers(config_));
    curl_easy_setopt(handle.get(), CURLOPT_HTTPHEADER, headers.get());

    const CURLcode curl_result = curl_easy_perform(handle.get());
    if (curl_result != CURLE_OK) {
      result.reason =
          curl_result == CURLE_OPERATION_TIMEDOUT ? "timeout" : "request_error";
      const std::string detail = error_buffer[0] != '\0'
                                     ? std::string(error_buffer)
                                     : curl_easy_strerror(curl_result);
      throw std::runtime_error("SLM transport failed: " + detail);
    }
    long status = 0;
    curl_easy_getinfo(handle.get(), CURLINFO_RESPONSE_CODE, &status);
    if (status < 200 || status >= 300) {
      result.reason = "remote_error";
      throw std::runtime_error("SLM HTTP " + std::to_string(status) + ": " +
                               body.substr(0, 500));
    }

    Json response;
    try {
      response = Json::parse(body);
    } catch (const Json::exception &error) {
      result.reason = "bad_json";
      throw std::runtime_error(std::string("invalid SLM JSON: ") +
                               error.what());
    }
    if (const auto error = remote_error(response)) {
      result.reason = "remote_error";
      throw std::runtime_error(error->second);
    }
    std::string output = trim(extract_content(response));
    if (output.empty()) {
      result.reason = "empty_content";
      throw std::runtime_error("SLM response did not contain text");
    }
    result.success = true;
    result.text = std::move(output);
    result.reason = "ok";
  } catch (const std::exception &error) {
    if (result.reason.empty()) {
      result.reason = "exception";
    }
    result.error = error.what();
  }
  result.latency_ms =
      std::chrono::duration<double, std::milli>(Clock::now() - started).count();
  return result;
}

PolishResult SlmClient::polish(const std::string &text,
                               std::optional<bool> enable_thinking) const {
  PolishResult result;
  result.original_text = text;
  result.text = text;
  if (!config_.enabled) {
    result.success = true;
    result.reason = "disabled";
    return result;
  }
  if (trim(text).empty()) {
    result.success = true;
    result.reason = "empty";
    return result;
  }
  if (config_.remote_stream) {
    return stream_polish(text, enable_thinking, {});
  }
  const CompletionResult completion = complete(
      std::string(kSystemPrompt), text, config_.max_tokens, enable_thinking);
  result.success = completion.success;
  result.reason = completion.reason;
  result.error = completion.error;
  result.latency_ms = completion.latency_ms;
  if (completion.success) {
    result.text = completion.text;
  }
  return result;
}

PolishResult SlmClient::stream_polish(const std::string &text,
                                      std::optional<bool> enable_thinking,
                                      const SlmStreamCallback &callback) const {
  PolishResult result;
  result.original_text = text;
  result.text = text;
  if (!config_.enabled) {
    result.success = true;
    result.reason = "disabled";
    if (callback) {
      (void)callback({"final", text, text, "disabled"});
    }
    return result;
  }
  if (!config_.remote_stream) {
    const PolishResult completed = [&] {
      const CompletionResult response =
          complete(std::string(kSystemPrompt), text, config_.max_tokens,
                   enable_thinking);
      PolishResult value;
      value.original_text = text;
      value.text = response.success ? response.text : text;
      value.success = response.success;
      value.reason = response.reason;
      value.error = response.error;
      value.latency_ms = response.latency_ms;
      return value;
    }();
    if (callback) {
      (void)callback(
          completed.success
              ? SlmStreamEvent{"final", completed.text, completed.text,
                               completed.reason, "", completed.latency_ms}
              : SlmStreamEvent{"error", "", "", completed.reason,
                               completed.error, completed.latency_ms});
    }
    return completed;
  }

  const auto started = Clock::now();
  if (callback && !callback({"status", "正在调用大模型..."})) {
    result.reason = "cancelled";
    result.error = failure_message(result.reason);
    return result;
  }
  std::lock_guard request_lock(request_mutex_);
  StreamContext context;
  context.callback = &callback;
  context.idle_timeout_ms = config_.stream_idle_timeout_ms;
  context.last_event = Clock::now();

  try {
    ensure_curl_initialized();
    std::unique_ptr<CURL, CurlCleanup> handle(curl_easy_init());
    if (!handle) {
      throw std::runtime_error("curl_easy_init returned null");
    }
    const std::string payload =
        build_payload(std::string(kSystemPrompt), trim(text),
                      config_.remote_max_tokens, true, enable_thinking)
            .dump();
    char error_buffer[CURL_ERROR_SIZE] = {};
    curl_easy_setopt(handle.get(), CURLOPT_URL, config_.endpoint.c_str());
    curl_easy_setopt(handle.get(), CURLOPT_POST, 1L);
    curl_easy_setopt(handle.get(), CURLOPT_POSTFIELDS, payload.data());
    curl_easy_setopt(handle.get(), CURLOPT_POSTFIELDSIZE,
                     static_cast<long>(payload.size()));
    curl_easy_setopt(handle.get(), CURLOPT_WRITEFUNCTION, append_stream);
    curl_easy_setopt(handle.get(), CURLOPT_WRITEDATA, &context);
    curl_easy_setopt(handle.get(), CURLOPT_XFERINFOFUNCTION, stream_progress);
    curl_easy_setopt(handle.get(), CURLOPT_XFERINFODATA, &context);
    curl_easy_setopt(handle.get(), CURLOPT_NOPROGRESS, 0L);
    if (config_.transport_timeout_ms > 0) {
      curl_easy_setopt(handle.get(), CURLOPT_TIMEOUT_MS,
                       static_cast<long>(config_.transport_timeout_ms));
    }
    curl_easy_setopt(
        handle.get(), CURLOPT_CONNECTTIMEOUT_MS,
        static_cast<long>(std::min(config_.stream_idle_timeout_ms, 5000)));
    curl_easy_setopt(handle.get(), CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(handle.get(), CURLOPT_ERRORBUFFER, error_buffer);
    std::unique_ptr<curl_slist, HeaderCleanup> headers(build_headers(config_));
    curl_easy_setopt(handle.get(), CURLOPT_HTTPHEADER, headers.get());

    const CURLcode curl_result = curl_easy_perform(handle.get());
    if (!context.line_buffer.empty() && context.saw_sse) {
      (void)process_stream_line(context, std::move(context.line_buffer));
    }
    if (context.saw_sse) {
      (void)flush_sse(context);
    }
    long status = 0;
    curl_easy_getinfo(handle.get(), CURLINFO_RESPONSE_CODE, &status);
    if (status < 200 || status >= 300) {
      context.reason = "remote_error";
      context.error = "SLM HTTP " + std::to_string(status);
    }
    if (curl_result != CURLE_OK && context.reason.empty()) {
      if (curl_result == CURLE_OPERATION_TIMEDOUT) {
        context.reason = "idle_timeout";
      } else if (context.cancelled) {
        context.reason = "cancelled";
      } else {
        context.reason = "request_error";
      }
      context.error = error_buffer[0] != '\0' ? std::string(error_buffer)
                                              : curl_easy_strerror(curl_result);
    }
    if (!context.reason.empty()) {
      throw std::runtime_error(context.error.empty()
                                   ? failure_message(context.reason)
                                   : context.error);
    }

    if (!context.saw_sse) {
      const Json response = Json::parse(context.raw_body);
      if (const auto error = remote_error(response)) {
        context.reason = "remote_error";
        throw std::runtime_error(error->second);
      }
      context.full_content = extract_content(response);
    }
    const std::string polished =
        trim(strip_thinking_content(context.full_content));
    if (polished.empty()) {
      context.reason = trim(context.full_content).empty() ? "empty_content"
                                                          : "thinking_only";
      throw std::runtime_error(failure_message(context.reason));
    }
    result.success = true;
    result.text = polished;
    result.reason = "ok";
  } catch (const Json::exception &error) {
    result.reason = context.reason.empty() ? "bad_json" : context.reason;
    result.error = error.what();
  } catch (const std::exception &error) {
    result.reason = context.reason.empty() ? "exception" : context.reason;
    result.error = error.what();
  }
  result.latency_ms =
      std::chrono::duration<double, std::milli>(Clock::now() - started).count();
  if (callback) {
    if (result.success) {
      (void)callback({"final", result.text, result.text, result.reason, "",
                      result.latency_ms});
    } else if (result.reason != "cancelled") {
      (void)callback({"error", "", "", result.reason,
                      failure_message(result.reason), result.latency_ms});
    }
  }
  return result;
}

} // namespace vocotype::core
