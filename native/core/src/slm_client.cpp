#include "vocotype/core/slm_client.hpp"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <memory>
#include <stdexcept>
#include <string_view>
#include <utility>

#include <curl/curl.h>

namespace vocotype::core {
namespace {

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

} // namespace

SlmClient::SlmClient(SlmConfig config) : config_(std::move(config)) {}

bool SlmClient::enabled() const noexcept { return config_.enabled; }

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
                              std::optional<bool> enable_thinking) const {
  Json payload = {
      {"model", config_.model},
      {"messages", Json::array({
                       {{"role", "system"}, {"content", system_prompt}},
                       {{"role", "user"}, {"content", user_text}},
                   })},
      {"stream", false},
      {"max_tokens", std::max(1, max_tokens)},
      {"temperature", config_.temperature},
      {"top_p", config_.top_p},
      {"top_k", config_.top_k},
  };
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
            return text;
          }
        }
      }
      const auto text = first.find("text");
      if (text != first.end()) {
        const std::string parsed = string_from_content(*text);
        if (!parsed.empty()) {
          return parsed;
        }
      }
    }
  }
  for (const char *key : {"output_text", "content", "text"}) {
    const auto found = response.find(key);
    if (found != response.end()) {
      const std::string parsed = string_from_content(*found);
      if (!parsed.empty()) {
        return parsed;
      }
    }
  }
  return {};
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

  const auto started = std::chrono::steady_clock::now();
  std::lock_guard request_lock(request_mutex_);
  try {
    ensure_curl_initialized();
    CURL *raw = curl_easy_init();
    if (raw == nullptr) {
      throw std::runtime_error("curl_easy_init returned null");
    }
    struct CurlCleanup {
      void operator()(CURL *handle) const noexcept {
        curl_easy_cleanup(handle);
      }
    };
    std::unique_ptr<CURL, CurlCleanup> handle(raw);

    const std::string payload =
        build_payload(system_prompt, user_text, max_tokens, enable_thinking)
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

    curl_slist *headers = nullptr;
    headers = curl_slist_append(headers, "Content-Type: application/json");
    std::string api_key = config_.api_key;
    if (api_key.empty() && !config_.api_key_env.empty()) {
      const char *value = std::getenv(config_.api_key_env.c_str());
      if (value != nullptr) {
        api_key = value;
      }
    }
    if (!api_key.empty()) {
      headers = curl_slist_append(headers,
                                  ("Authorization: Bearer " + api_key).c_str());
    }
    for (const auto &[key, value] : config_.extra_headers.items()) {
      if (value.is_string()) {
        headers = curl_slist_append(
            headers, (key + ": " + value.get<std::string>()).c_str());
      }
    }
    struct HeaderCleanup {
      void operator()(curl_slist *list) const noexcept {
        curl_slist_free_all(list);
      }
    };
    std::unique_ptr<curl_slist, HeaderCleanup> header_guard(headers);
    curl_easy_setopt(handle.get(), CURLOPT_HTTPHEADER, headers);

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
  result.latency_ms = std::chrono::duration<double, std::milli>(
                          std::chrono::steady_clock::now() - started)
                          .count();
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

} // namespace vocotype::core
