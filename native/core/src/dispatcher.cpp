#include "vocotype/core/dispatcher.hpp"

#include <utility>

namespace vocotype::core {

CoreDispatcher::CoreDispatcher(AppConfig config)
    : config_(std::move(config)), slm_(config_.slm) {}

Json CoreDispatcher::dispatch(const Json &request) const {
  if (!request.is_object()) {
    return {{"success", false}, {"error", "request_must_be_object"}};
  }
  const std::string type = request.value("type", "");
  if (type == "ping") {
    return {
        {"pong", true},
        {"success", true},
        {"backend", "cpp"},
        {"protocol_version", 1},
    };
  }
  if (type == "capabilities") {
    return {
        {"success", true},
        {"backend", "cpp"},
        {"protocol_version", 1},
        {"features",
         {
             {"ipc", true},
             {"slm_non_streaming", true},
             {"slm_enabled", slm_.enabled()},
             {"final_asr", false},
             {"streaming_asr", false},
             {"voice_edit", false},
         }},
    };
  }
  if (type == "polish_text") {
    const std::string text = request.value("text", "");
    const PolishResult result = slm_.polish(text);
    return {
        {"success", result.success},
        {"text", result.text},
        {"original_text", result.original_text},
        {"reason", result.reason},
        {"error", result.error},
        {"latency_ms", result.latency_ms},
    };
  }
  if (type == "transcribe" || type == "transcribe_start") {
    return {
        {"success", false},
        {"error", "native_final_asr_not_connected"},
        {"reason", "not_implemented"},
        {"backend", "cpp"},
    };
  }
  if (type == "edit_applied") {
    return {{"success", true}};
  }
  return {
      {"success", false},
      {"error", "unknown_request_type"},
      {"request_type", type},
  };
}

} // namespace vocotype::core
