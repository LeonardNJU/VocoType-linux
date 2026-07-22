#include "vocotype/core/dispatcher.hpp"

#include <utility>

namespace vocotype::core {

CoreDispatcher::CoreDispatcher(AppConfig config)
    : config_(std::move(config)), slm_(config_.slm),
      offline_asr_(config_.offline_asr, config_.normalization),
      streaming_asr_(config_.streaming_asr),
      transcription_tasks_(offline_asr_, slm_), voice_edit_planner_(slm_),
      voice_edit_tasks_(offline_asr_, voice_edit_planner_) {
  if (offline_asr_.enabled()) {
    (void)offline_asr_.initialize();
  }
}

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
             {"slm_streaming", true},
             {"slm_remote_stream", slm_.remote_stream()},
             {"slm_enabled", slm_.enabled()},
             {"final_asr", offline_asr_.enabled()},
             {"final_asr_ready", offline_asr_.ready()},
             {"streaming_asr", streaming_asr_.enabled()},
             {"streaming_asr_ready", streaming_asr_.ready()},
             {"async_transcription", true},
             {"voice_edit", slm_.edit_enabled()},
         }},
    };
  }
  if (type == "asr_preview_start") {
    return streaming_asr_.start_session();
  }
  if (type == "asr_preview_feed") {
    return streaming_asr_.feed(request);
  }
  if (type == "asr_preview_close") {
    return streaming_asr_.close_session(request);
  }
  if (type == "normalize_text") {
    const std::string text = request.value("text", "");
    return {{"success", true},
            {"text", offline_asr_.normalize_text(text)},
            {"hotwords", offline_asr_.build_native_hotwords(
                             request.value("hotwords", std::string()))}};
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
  if (type == "transcribe") {
    Json result = offline_asr_.transcribe(request);
    if (result.value("success", false) && request.value("long_mode", false)) {
      const std::string original = result.value("text", "");
      const PolishResult polished = slm_.polish(original);
      result["original_text"] = original;
      result["slm_reason"] = polished.reason;
      result["slm_latency_ms"] = polished.latency_ms;
      if (polished.success) {
        result["text"] = polished.text;
      } else {
        result["success"] = false;
        result["error"] = polished.error;
      }
    }
    result["backend"] = "cpp";
    return result;
  }
  if (type == "transcribe_start") {
    return transcription_tasks_.start(request);
  }
  if (type == "polish_poll") {
    return transcription_tasks_.poll(request);
  }
  if (type == "polish_cancel") {
    return transcription_tasks_.cancel(request);
  }
  if (type == "edit_audio") {
    return voice_edit_tasks_.run_sync(request);
  }
  if (type == "edit_start") {
    return voice_edit_tasks_.start(request);
  }
  if (type == "edit_poll") {
    return voice_edit_tasks_.poll(request);
  }
  if (type == "edit_cancel") {
    return voice_edit_tasks_.cancel(request);
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
