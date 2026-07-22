#include <iostream>
#include <string>
#include <unordered_map>

#include <nlohmann/json.hpp>

int main() {
  using Json = nlohmann::json;
  std::cout << Json({{"type", "ready"},
                     {"success", true},
                     {"sample_rate", 16000},
                     {"chunk_samples", 9600}})
                   .dump()
            << '\n'
            << std::flush;

  std::unordered_map<std::string, std::string> sessions;
  unsigned long long next_id = 0;
  std::string line;
  while (std::getline(std::cin, line)) {
    Json response;
    try {
      const Json request = Json::parse(line);
      const std::string type = request.value("type", "");
      if (type == "start") {
        const std::string id = std::to_string(++next_id);
        sessions[id] = "";
        response = {{"success", true},
                    {"session_id", id},
                    {"sample_rate", 16000},
                    {"chunk_samples", 9600}};
      } else if (type == "feed") {
        const std::string id = request.value("session_id", "");
        const auto found = sessions.find(id);
        if (found == sessions.end()) {
          response = {{"success", false},
                      {"error", "streaming_session_not_found"}};
        } else {
          found->second += "预览";
          response = {{"success", true},
                      {"text", found->second},
                      {"final", request.value("is_final", false)}};
        }
      } else if (type == "close") {
        const std::string id = request.value("session_id", "");
        const auto found = sessions.find(id);
        if (found == sessions.end()) {
          response = {{"success", false},
                      {"error", "streaming_session_not_found"}};
        } else {
          response = {
              {"success", true}, {"text", found->second}, {"final", true}};
          sessions.erase(found);
        }
      } else if (type == "stop") {
        std::cout << Json({{"success", true}}).dump() << '\n' << std::flush;
        return 0;
      } else {
        response = {{"success", false}, {"error", "unknown_request"}};
      }
    } catch (const std::exception &error) {
      response = {{"success", false}, {"error", error.what()}};
    }
    std::cout << response.dump() << '\n' << std::flush;
  }
  return 0;
}
