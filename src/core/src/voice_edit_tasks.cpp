#include "vocotype/core/voice_edit_tasks.hpp"

#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <utility>

namespace vocotype::core {
namespace {

Json error_response(const std::string &error, const std::string &reason = "") {
  Json result{{"success", false}, {"error", error}};
  if (!reason.empty()) {
    result["reason"] = reason;
  }
  return result;
}

constexpr auto kTaskTtl = std::chrono::minutes(5);

std::string trim(std::string value) {
  const auto first = value.find_first_not_of(" \t\r\n");
  if (first == std::string::npos) {
    return {};
  }
  const auto last = value.find_last_not_of(" \t\r\n");
  return value.substr(first, last - first + 1);
}

} // namespace

struct VoiceEditTaskManager::Task {
  explicit Task(std::string value) : task_id(std::move(value)) {}

  void set_instruction(const std::string &value) {
    std::lock_guard lock(mutex);
    if (cancelled || status != "running") {
      return;
    }
    instruction = value;
    phase = "editing";
  }

  void mark_final(Json value) {
    std::lock_guard lock(mutex);
    if (cancelled || status != "running") {
      return;
    }
    status = "final";
    phase = "done";
    instruction = value.value("instruction", instruction);
    reason = value.value("reason", "ok");
    result = std::move(value);
    done_at = std::chrono::steady_clock::now();
  }

  void mark_error(const std::string &message, const std::string &error_reason,
                  const std::string &recognized_instruction = "") {
    std::lock_guard lock(mutex);
    if (cancelled || status != "running") {
      return;
    }
    status = "error";
    phase = "done";
    error = message;
    reason = error_reason;
    if (!recognized_instruction.empty()) {
      instruction = recognized_instruction;
    }
    done_at = std::chrono::steady_clock::now();
  }

  void cancel() {
    std::lock_guard lock(mutex);
    if (status != "running") {
      return;
    }
    cancelled = true;
    status = "cancelled";
    phase = "done";
    error = "已取消语音编辑";
    reason = "cancelled";
    done_at = std::chrono::steady_clock::now();
  }

  [[nodiscard]] bool is_cancelled() const {
    std::lock_guard lock(mutex);
    return cancelled;
  }

  [[nodiscard]] bool expired(std::chrono::steady_clock::time_point now) const {
    std::lock_guard lock(mutex);
    return status != "running" &&
           done_at != std::chrono::steady_clock::time_point{} &&
           now - done_at > kTaskTtl;
  }

  [[nodiscard]] Json snapshot() const {
    std::lock_guard lock(mutex);
    return {{"success", true},
            {"task_id", task_id},
            {"status", status},
            {"phase", phase},
            {"instruction", instruction},
            {"result", result},
            {"error", error},
            {"reason", reason}};
  }

  std::string task_id;
  mutable std::mutex mutex;
  std::string status = "running";
  std::string phase = "asr";
  std::string instruction;
  Json result = Json::object();
  std::string error;
  std::string reason;
  bool cancelled = false;
  std::chrono::steady_clock::time_point done_at{};
};

VoiceEditTaskManager::VoiceEditTaskManager(OfflineAsrProcess &asr,
                                           const VoiceEditPlanner &planner)
    : asr_(asr), planner_(planner) {}

VoiceEditTaskManager::~VoiceEditTaskManager() {
  std::lock_guard lock(workers_mutex_);
  for (auto &worker : workers_) {
    if (worker.thread.joinable()) {
      worker.thread.join();
    }
  }
  workers_.clear();
}

void VoiceEditTaskManager::cleanup_finished_workers() {
  std::lock_guard lock(workers_mutex_);
  for (auto iterator = workers_.begin(); iterator != workers_.end();) {
    if (!iterator->finished->load(std::memory_order_acquire)) {
      ++iterator;
      continue;
    }
    if (iterator->thread.joinable()) {
      iterator->thread.join();
    }
    iterator = workers_.erase(iterator);
  }
}

void VoiceEditTaskManager::cleanup_expired_tasks() {
  const auto now = std::chrono::steady_clock::now();
  std::lock_guard lock(tasks_mutex_);
  for (auto iterator = tasks_.begin(); iterator != tasks_.end();) {
    if (iterator->second->expired(now)) {
      iterator = tasks_.erase(iterator);
    } else {
      ++iterator;
    }
  }
}

std::string VoiceEditTaskManager::next_task_id() {
  return "edit-" + std::to_string(::getpid()) + "-" +
         std::to_string(++next_id_);
}

std::shared_ptr<VoiceEditTaskManager::Task>
VoiceEditTaskManager::find_task(const std::string &task_id) const {
  std::lock_guard lock(tasks_mutex_);
  const auto found = tasks_.find(task_id);
  return found == tasks_.end() ? nullptr : found->second;
}

Json VoiceEditTaskManager::perform(const Json &request,
                                   const std::shared_ptr<Task> &task) {
  const std::string audio_path = request.value("audio_path", "");
  if (audio_path.empty()) {
    return error_response("缺少 audio_path 参数", "missing_audio_path");
  }

  Json asr_request{{"audio_path", audio_path},
                   {"sampling_rate", request.value("sampling_rate", 16000)},
                   {"itn", request.value("itn", true)}};
  const auto hotwords = request.find("hotwords");
  if (hotwords != request.end() && hotwords->is_string() &&
      !hotwords->get_ref<const std::string &>().empty()) {
    asr_request["hotwords"] = *hotwords;
  }
  Json transcribed = asr_.transcribe(asr_request);
  if (!transcribed.value("success", false)) {
    return error_response(transcribed.value("error", "编辑指令识别失败"),
                          transcribed.value("reason", "asr_error"));
  }

  const std::string instruction = trim(transcribed.value("text", ""));
  if (instruction.empty()) {
    return {{"success", false},
            {"error", "未识别到编辑指令，请靠近麦克风后重试"},
            {"reason", "empty_instruction"}};
  }
  if (task) {
    if (task->is_cancelled()) {
      return error_response("已取消语音编辑", "cancelled");
    }
    task->set_instruction(instruction);
  }

  Json planned = planner_.plan(request, instruction);
  if (!planned.contains("instruction")) {
    planned["instruction"] = instruction;
  }
  return planned;
}

Json VoiceEditTaskManager::run_sync(const Json &request) {
  return perform(request);
}

Json VoiceEditTaskManager::start(const Json &request) {
  cleanup_finished_workers();
  cleanup_expired_tasks();
  const std::string audio_path = request.value("audio_path", "");
  if (audio_path.empty()) {
    return error_response("缺少 audio_path 参数", "missing_audio_path");
  }
  const auto expanded = expand_user_path(audio_path);
  if (!std::filesystem::is_regular_file(expanded)) {
    return error_response("录音文件不存在", "audio_file_not_found");
  }

  Json owned_request = request;
  owned_request["audio_path"] = std::filesystem::canonical(expanded).string();
  auto task = std::make_shared<Task>(next_task_id());
  {
    std::lock_guard lock(tasks_mutex_);
    tasks_[task->task_id] = task;
  }
  {
    auto finished = std::make_shared<std::atomic<bool>>(false);
    WorkerSlot slot;
    slot.finished = finished;
    slot.thread = std::thread(
        [this, task, owned_request, finished]() mutable {
          struct FinishGuard {
            std::shared_ptr<std::atomic<bool>> flag;
            ~FinishGuard() { flag->store(true, std::memory_order_release); }
          } guard{finished};
          run_task(task, std::move(owned_request));
        });
    std::lock_guard lock(workers_mutex_);
    workers_.push_back(std::move(slot));
  }
  return {{"success", true}, {"task_id", task->task_id}, {"status", "running"}};
}

Json VoiceEditTaskManager::poll(const Json &request) const {
  const std::string task_id = request.value("task_id", "");
  if (task_id.empty()) {
    return error_response("缺少 task_id 参数", "missing_task_id");
  }
  const auto task = find_task(task_id);
  if (!task) {
    return error_response("编辑任务不存在或已过期", "task_not_found");
  }
  return task->snapshot();
}

Json VoiceEditTaskManager::cancel(const Json &request) {
  const std::string task_id = request.value("task_id", "");
  if (task_id.empty()) {
    return error_response("缺少 task_id 参数", "missing_task_id");
  }
  const auto task = find_task(task_id);
  if (task) {
    task->cancel();
  }
  return {{"success", true}};
}

void VoiceEditTaskManager::run_task(const std::shared_ptr<Task> &task,
                                    Json request) {
  const std::filesystem::path audio_path = request.value("audio_path", "");
  struct AudioCleanup {
    std::filesystem::path path;
    ~AudioCleanup() {
      std::error_code error;
      std::filesystem::remove(path, error);
    }
  } cleanup{audio_path};

  try {
    Json result = perform(request, task);
    if (task->is_cancelled()) {
      return;
    }
    if (result.value("success", false)) {
      task->mark_final(std::move(result));
      return;
    }
    task->mark_error(result.value("error", "语音编辑失败"),
                     result.value("reason", "edit_failed"),
                     result.value("instruction", ""));
  } catch (const std::exception &error) {
    task->mark_error(error.what(), "exception");
  }
}

} // namespace vocotype::core
