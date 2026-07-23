#include <iostream>
#include <string>

#include <nlohmann/json.hpp>

int main() {
  using Json = nlohmann::json;
  std::cout << Json({{"type", "ready"},
                     {"success", true},
                     {"contextual_hotword", true},
                     {"vad", true},
                     {"punctuation", true}})
                   .dump()
            << '\n'
            << std::flush;

  std::string line;
  while (std::getline(std::cin, line)) {
    Json response;
    try {
      const Json request = Json::parse(line);
      const std::string type = request.value("type", "");
      if (type == "transcribe") {
        response = {{"success", true},
                    {"text", "原生最终转写"},
                    {"raw_text", "原生最终转写"},
                    {"hotwords", request.value("hotwords", "")},
                    {"latency_ms", 12.5},
                    {"snippet_time", 1.0},
                    {"result_count", 1}};
      } else if (type == "stop") {
        std::cout << Json({{"success", true}}).dump() << '\n' << std::flush;
        return 0;
      } else if (type == "ping") {
        response = {{"success", true}};
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
