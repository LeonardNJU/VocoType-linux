#include "vocotype/desktop/streaming_preview.hpp"
#include "vocotype/desktop/task_status.hpp"

#include <cstdlib>
#include <iostream>
#include <string>

int main() {
  using namespace vocotype::desktop;
  const auto require = [](bool condition, const char *message) {
    if (!condition) {
      std::cerr << "FAIL: " << message << '\n';
      std::exit(1);
    }
  };

  StreamingPreviewTranscript preview;
  const auto first = preview.update_session_text("你好世界");
  require(first && *first == "你好世界", "first preview was rejected");
  require(!preview.update_session_text("  \n\t").has_value(),
          "blank preview was accepted");
  require(preview.display_text() == "你好世界",
          "blank preview erased valid text");

  preview.begin_recovery();
  const auto overlap = preview.update_session_text("世界继续说话");
  require(overlap && *overlap == "你好世界继续说话",
          "UTF-8 overlap was not deduplicated after recovery");

  preview.begin_recovery();
  const auto continued = preview.update_session_text("说话然后补充第二句");
  require(continued && *continued == "你好世界继续说话然后补充第二句",
          "committed prefix was not preserved across a second recovery");
  require(!preview.update_session_text("\r\n ").has_value(),
          "post-recovery blank preview was accepted");
  require(preview.display_text() == "你好世界继续说话然后补充第二句",
          "post-recovery blank preview erased accumulated text");

  require(merge_preview_text("第一段第二段", "第二段第三段") ==
              "第一段第二段第三段",
          "overlap merge failed");
  require(merge_preview_text("完整前缀", "完整前缀继续") ==
              "完整前缀继续",
          "cumulative session text duplicated its prefix");

  preview.reset();
  require(!preview.has_text() && preview.display_text().empty(),
          "reset retained stale text");

  require(task_status_is_terminal("final"), "final must be terminal");
  require(task_status_is_terminal("error"), "error must be terminal");
  require(task_status_is_terminal("cancelled"), "cancelled must be terminal");
  require(task_status_is_terminal("completed"),
          "legacy completed must remain terminal");
  require(task_status_is_terminal("failed"),
          "legacy failed must remain terminal");
  require(!task_status_is_terminal("running"),
          "running must not be terminal");
  std::cout << "streaming preview tests passed\n";
}
