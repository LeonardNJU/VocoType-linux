#include "vocotype/core/json_line_worker.hpp"
#include "win_support.hpp"
#include <algorithm>
#include <chrono>
namespace vocotype::core {
JsonLineWorker::JsonLineWorker() : child_(std::make_unique<vocotype::windows::ChildProcess>()) {}
JsonLineWorker::~JsonLineWorker() { stop(); }
bool JsonLineWorker::alive_locked() noexcept { return child_ && child_->alive(); }
bool JsonLineWorker::ready() noexcept { std::lock_guard lock(mutex_); return ready_ && alive_locked(); }
Json JsonLineWorker::start(const std::filesystem::path& exe,const std::vector<std::string>& args,int timeout) {
  std::lock_guard lock(mutex_);
  if(ready_ && alive_locked()) return ready_response_;
  stop_locked();
  try {
    std::vector<std::wstring> values; for(const auto& arg:args) values.push_back(vocotype::windows::wide(arg));
    child_->start(exe,values);
    auto response=Json::parse(child_->read_line(timeout));
    if(response.value("type","")!="ready" || !response.value("success",false)) {
      stop_locked(); return {{"success",false},{"error",response.value("error","worker_failed")}};
    }
    ready_=true; ready_response_=response; return response;
  } catch(const std::exception& e) { stop_locked(); return {{"success",false},{"error",e.what()}}; }
}
Json JsonLineWorker::request(const Json& request,int timeout) {
  std::lock_guard lock(mutex_);
  if(!ready_ || !alive_locked()) return {{"success",false},{"error","worker_not_ready"}};
  try {
    const auto started=std::chrono::steady_clock::now();
    write_line_locked(request.dump(),timeout);
    const auto elapsed=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-started).count();
    return Json::parse(read_line_locked(std::max(1,timeout-static_cast<int>(elapsed))));
  } catch(const std::exception& e) { stop_locked(); return {{"success",false},{"error",e.what()}}; }
}
std::string JsonLineWorker::read_line_locked(int timeout) { return child_->read_line(timeout); }
void JsonLineWorker::write_line_locked(const std::string& line,int timeout) { child_->write_line(line,timeout); }
void JsonLineWorker::reset_locked() noexcept { ready_=false; ready_response_=Json::object(); read_buffer_.clear(); }
void JsonLineWorker::stop_locked() noexcept { if(child_) child_->stop(); reset_locked(); }
void JsonLineWorker::stop() noexcept { std::lock_guard lock(mutex_); stop_locked(); }
}
