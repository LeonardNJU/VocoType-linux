#pragma once

#include "vocotype/core/config.hpp"
#include "vocotype/core/offline_asr.hpp"
#include "vocotype/core/slm_client.hpp"
#include "vocotype/core/streaming_asr.hpp"
#include "vocotype/core/transcription_tasks.hpp"
#include "vocotype/core/voice_edit.hpp"
#include "vocotype/core/voice_edit_tasks.hpp"

namespace vocotype::core {

class CoreDispatcher {
public:
  explicit CoreDispatcher(AppConfig config);
  [[nodiscard]] Json dispatch(const Json &request) const;

private:
  AppConfig config_;
  SlmClient slm_;
  mutable OfflineAsrProcess offline_asr_;
  mutable StreamingAsrProcess streaming_asr_;
  mutable TranscriptionTaskManager transcription_tasks_;
  VoiceEditPlanner voice_edit_planner_;
  mutable VoiceEditTaskManager voice_edit_tasks_;
};

} // namespace vocotype::core
