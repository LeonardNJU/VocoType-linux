#pragma once

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include "vocotype/core/config.hpp"
#include "vocotype/core/offline_asr.hpp"
#include "vocotype/core/slm_client.hpp"

namespace vocotype::core {

class TranscriptionTaskManager {
public:
  TranscriptionTaskManager(OfflineAsrProcess &asr, const SlmClient &slm);
  TranscriptionTaskManager(const TranscriptionTaskManager &) = delete;
  TranscriptionTaskManager &
  operator=(const TranscriptionTaskManager &) = delete;
  ~TranscriptionTaskManager();

  [[nodiscard]] Json start(const Json &request);
  [[nodiscard]] Json poll(const Json &request) const;
  [[nodiscard]] Json cancel(const Json &request);

private:
  struct Task;
  struct WorkerSlot {
    std::jthread thread;
    std::shared_ptr<std::atomic<bool>> finished;
  };

  [[nodiscard]] std::shared_ptr<Task>
  find_task(const std::string &task_id) const;
  void run_task(const std::shared_ptr<Task> &task, Json request);
  [[nodiscard]] std::string next_task_id();
  void cleanup_finished_workers();
  void cleanup_expired_tasks();

  OfflineAsrProcess &asr_;
  const SlmClient &slm_;
  mutable std::mutex tasks_mutex_;
  std::unordered_map<std::string, std::shared_ptr<Task>> tasks_;
  std::mutex workers_mutex_;
  std::vector<WorkerSlot> workers_;
  std::atomic<unsigned long long> next_id_{0};
};

} // namespace vocotype::core
