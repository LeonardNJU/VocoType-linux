/*
 * VoCoType Fcitx5 Addon - IPC Client Implementation
 */

#include "ipc_client.h"
#include <algorithm>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/time.h>
#include <unistd.h>
#include <cstring>
#include <cerrno>
#include <stdexcept>
#include <utility>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace vocotype {

namespace {

std::string jsonStringOr(const json &value,
                         const char *key,
                         const std::string &fallback = "") {
    const auto it = value.find(key);
    if (it == value.end() || !it->is_string()) {
        return fallback;
    }
    return it->get<std::string>();
}

bool jsonBoolOr(const json &value, const char *key, bool fallback) {
    const auto it = value.find(key);
    return it != value.end() && it->is_boolean() ? it->get<bool>() : fallback;
}

int jsonIntOr(const json &value, const char *key, int fallback) {
    const auto it = value.find(key);
    return it != value.end() && it->is_number_integer()
               ? it->get<int>()
               : fallback;
}

} // namespace

IPCClient::IPCClient(const std::string& socket_path)
    : socket_path_(socket_path) {
}

IPCClient::~IPCClient() {
}

std::string IPCClient::sendRequest(const std::string& request,
                                   int receive_timeout_ms) {
    // 创建 Unix Socket
    int sock = socket(AF_UNIX, SOCK_STREAM, 0);
    if (sock < 0) {
        throw std::runtime_error("Failed to create socket");
    }

    const struct timeval send_timeout = {2, 0};
    const int bounded_receive_timeout_ms =
        std::max(1, receive_timeout_ms);
    const struct timeval receive_timeout = {
        bounded_receive_timeout_ms / 1000,
        (bounded_receive_timeout_ms % 1000) * 1000,
    };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO,
               &receive_timeout, sizeof(receive_timeout));
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO,
               &send_timeout, sizeof(send_timeout));

    // 连接到服务器
    struct sockaddr_un addr;
    std::memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    std::strncpy(addr.sun_path, socket_path_.c_str(), sizeof(addr.sun_path) - 1);

    if (connect(sock, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        close(sock);
        throw std::runtime_error("Failed to connect to backend: " + socket_path_);
    }

    // 发送请求（确保全部写入）
    size_t total_sent = 0;
    while (total_sent < request.size()) {
        ssize_t sent = send(sock, request.data() + total_sent,
                            request.size() - total_sent, 0);
        if (sent < 0) {
            if (errno == EINTR) {
                continue;
            }
            close(sock);
            throw std::runtime_error("Failed to send request");
        }
        total_sent += static_cast<size_t>(sent);
    }

    shutdown(sock, SHUT_WR);

    // 接收响应（读到 EOF）
    std::string response;
    char buffer[4096];
    while (true) {
        ssize_t len = recv(sock, buffer, sizeof(buffer), 0);
        if (len < 0) {
            if (errno == EINTR) {
                continue;
            }
            close(sock);
            throw std::runtime_error("Failed to receive response");
        }
        if (len == 0) {
            break;
        }
        response.append(buffer, static_cast<size_t>(len));
    }

    close(sock);
    return response;
}

TranscribeResult IPCClient::transcribeAudio(const std::string& audio_path, bool long_mode) {
    TranscribeResult result;

    try {
        // 构建请求
        json request = {
            {"type", "transcribe"},
            {"audio_path", audio_path},
            {"long_mode", long_mode}
        };

        // 发送请求
        std::string response_str = sendRequest(request.dump());

        // 解析响应
        json response = json::parse(response_str);

        result.success = response.value("success", false);
        result.original_text = response.value("original_text", "");
        if (result.success) {
            result.text = response.value("text", "");
        } else {
            result.error = response.value("error", "Unknown error");
        }

    } catch (const std::exception& e) {
        result.success = false;
        result.error = e.what();
    }

    return result;
}

VoiceEditResult IPCClient::editAudio(
    const std::string& audio_path,
    const std::string& context_id,
    const std::string& surrounding_text,
    unsigned int cursor_pos,
    unsigned int anchor_pos,
    const std::string& selected_text,
    const std::string& replace_state,
    bool supports_surrounding) {
    VoiceEditResult result;
    try {
        json request = {
            {"type", "edit_audio"},
            {"audio_path", audio_path},
            {"context_id", context_id},
            {"replace_state", replace_state},
            {"supports_surrounding", supports_surrounding},
            {"snapshot", {
                {"text", surrounding_text},
                {"cursor_pos", cursor_pos},
                {"anchor_pos", anchor_pos},
                {"selected_text", selected_text}
            }}
        };
        // ASR plus a remote editing model routinely exceeds the 2-second
        // control-request timeout. This method is called from a worker thread,
        // so waiting here does not block Fcitx's event loop.
        const std::string response_str = sendRequest(request.dump(), 30000);
        const json response = json::parse(response_str);
        result.success = jsonBoolOr(response, "success", false);
        result.handled = jsonBoolOr(response, "handled", false);
        result.record_history = jsonBoolOr(response, "record_history", true);
        result.mode = jsonStringOr(response, "mode");
        result.new_text = jsonStringOr(response, "new_text");
        result.expected_text = jsonStringOr(response, "expected_text");
        result.hint = jsonStringOr(response, "hint");
        result.error = jsonStringOr(response, "error");
        result.instruction = jsonStringOr(response, "instruction");
        result.reason = jsonStringOr(response, "reason");
        if (response.contains("key_actions") && response["key_actions"].is_array()) {
            for (const auto& item : response["key_actions"]) {
                if (!item.is_object()) {
                    continue;
                }
                EditKeyAction action;
                action.key = jsonStringOr(item, "key");
                action.repeat = std::clamp(jsonIntOr(item, "repeat", 1), 1, 100);
                if (item.contains("modifiers") && item["modifiers"].is_array()) {
                    for (const auto& modifier : item["modifiers"]) {
                        if (modifier.is_string()) {
                            action.modifiers.push_back(modifier.get<std::string>());
                        }
                    }
                }
                if (!action.key.empty()) {
                    result.key_actions.push_back(std::move(action));
                }
            }
        }
        if (!result.success && result.error.empty()) {
            result.error = "Unknown edit error";
        }
    } catch (const std::exception& e) {
        result.success = false;
        result.error = e.what();
    }
    return result;
}


VoiceEditStartResult IPCClient::startVoiceEdit(
    const std::string& audio_path,
    const std::string& context_id,
    const std::string& surrounding_text,
    unsigned int cursor_pos,
    unsigned int anchor_pos,
    const std::string& selected_text,
    const std::string& replace_state,
    bool supports_surrounding) {
    VoiceEditStartResult result;
    try {
        json request = {
            {"type", "edit_start"},
            {"audio_path", audio_path},
            {"context_id", context_id},
            {"replace_state", replace_state},
            {"supports_surrounding", supports_surrounding},
            {"snapshot", {
                {"text", surrounding_text},
                {"cursor_pos", cursor_pos},
                {"anchor_pos", anchor_pos},
                {"selected_text", selected_text}
            }}
        };
        const json response = json::parse(sendRequest(request.dump()));
        result.success = jsonBoolOr(response, "success", false);
        result.task_id = jsonStringOr(response, "task_id");
        result.status = jsonStringOr(response, "status");
        result.error = jsonStringOr(response, "error");
        if (!result.success && result.error.empty()) {
            result.error = "Unknown edit start error";
        }
    } catch (const std::exception& e) {
        result.success = false;
        result.error = e.what();
    }
    return result;
}

VoiceEditPollResult IPCClient::pollVoiceEditTask(const std::string& task_id) {
    VoiceEditPollResult result;
    try {
        const json request = {
            {"type", "edit_poll"},
            {"task_id", task_id}
        };
        const json response = json::parse(sendRequest(request.dump()));
        result.success = jsonBoolOr(response, "success", false);
        result.task_id = jsonStringOr(response, "task_id", task_id);
        result.status = jsonStringOr(response, "status");
        result.phase = jsonStringOr(response, "phase");
        result.instruction = jsonStringOr(response, "instruction");
        result.error = jsonStringOr(response, "error");
        result.reason = jsonStringOr(response, "reason");
        if (response.contains("result") && response["result"].is_object()) {
            const auto& value = response["result"];
            auto& edit = result.result;
            edit.success = jsonBoolOr(value, "success", false);
            edit.handled = jsonBoolOr(value, "handled", false);
            edit.record_history = jsonBoolOr(value, "record_history", true);
            edit.mode = jsonStringOr(value, "mode");
            edit.new_text = jsonStringOr(value, "new_text");
            edit.expected_text = jsonStringOr(value, "expected_text");
            edit.hint = jsonStringOr(value, "hint");
            edit.error = jsonStringOr(value, "error");
            edit.instruction = jsonStringOr(
                value, "instruction", result.instruction);
            edit.reason = jsonStringOr(value, "reason", result.reason);
            if (value.contains("key_actions") && value["key_actions"].is_array()) {
                for (const auto& item : value["key_actions"]) {
                    if (!item.is_object()) {
                        continue;
                    }
                    EditKeyAction action;
                    action.key = jsonStringOr(item, "key");
                    action.repeat = std::clamp(
                        jsonIntOr(item, "repeat", 1), 1, 100);
                    if (item.contains("modifiers") && item["modifiers"].is_array()) {
                        for (const auto& modifier : item["modifiers"]) {
                            if (modifier.is_string()) {
                                action.modifiers.push_back(modifier.get<std::string>());
                            }
                        }
                    }
                    if (!action.key.empty()) {
                        edit.key_actions.push_back(std::move(action));
                    }
                }
            }
        }
        if (!result.success && result.error.empty()) {
            result.error = "Unknown edit poll error";
        }
    } catch (const std::exception& e) {
        result.success = false;
        result.error = e.what();
    }
    return result;
}

bool IPCClient::cancelVoiceEditTask(const std::string& task_id) {
    try {
        const json request = {
            {"type", "edit_cancel"},
            {"task_id", task_id}
        };
        const json response = json::parse(sendRequest(request.dump()));
        return response.value("success", false);
    } catch (const std::exception&) {
        return false;
    }
}

bool IPCClient::confirmEditApplied(const std::string& context_id,
                                   const std::string& original_text,
                                   const std::string& new_text,
                                   bool record_history) {
    try {
        json request = {
            {"type", "edit_applied"},
            {"context_id", context_id},
            {"original_text", original_text},
            {"new_text", new_text},
            {"record_history", record_history}
        };
        const json response = json::parse(sendRequest(request.dump()));
        return response.value("success", false);
    } catch (const std::exception&) {
        return false;
    }
}

bool IPCClient::cancelPolishTask(const std::string& task_id) {
    try {
        json request = {
            {"type", "polish_cancel"},
            {"task_id", task_id}
        };
        std::string response_str = sendRequest(request.dump());
        json response = json::parse(response_str);
        return response.value("success", false);
    } catch (const std::exception& e) {
        return false;
    }
}

RimeUIState IPCClient::processKey(int keyval, int mask) {
    RimeUIState state;

    try {
        // 构建请求
        json request = {
            {"type", "key_event"},
            {"keyval", keyval},
            {"mask", mask}
        };

        // 发送请求
        std::string response_str = sendRequest(request.dump());

        // 解析响应
        json response = json::parse(response_str);

        state.handled = response.value("handled", false);

        // 提交文本
        if (response.contains("commit")) {
            state.commit_text = response["commit"];
        }

        // 预编辑
        if (response.contains("preedit")) {
            state.preedit_text = response["preedit"]["text"];
            state.cursor_pos = response["preedit"]["cursor_pos"];
        }

        // 候选词
        if (response.contains("candidates")) {
            for (const auto& candidate : response["candidates"]) {
                std::string text = candidate["text"];
                std::string comment = candidate["comment"];
                state.candidates.push_back({text, comment});
            }
            state.highlighted_index = response.value("highlighted_index", 0);
            state.page_size = response.value("page_size", 5);
        }

    } catch (const std::exception& e) {
        // 错误时返回未处理状态
        state.handled = false;
    }

    return state;
}

void IPCClient::reset() {
    try {
        json request = {{"type", "reset"}};
        sendRequest(request.dump());
    } catch (const std::exception& e) {
        // 忽略错误
    }
}

bool IPCClient::ping() {
    try {
        json request = {{"type", "ping"}};
        std::string response_str = sendRequest(request.dump());
        json response = json::parse(response_str);
        return response.value("pong", false);
    } catch (const std::exception& e) {
        return false;
    }
}

} // namespace vocotype
