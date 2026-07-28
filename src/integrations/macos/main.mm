#import <AppKit/AppKit.h>
#import <InputMethodKit/InputMethodKit.h>

#import "VocoTypeInputController.h"
#import "VocoTypeRuntime.h"

#include "vocotype/desktop/config.hpp"
#include "vocotype/desktop/ipc.hpp"

#include <string>
#include <thread>

int main(int argc, const char *argv[]) {
  @autoreleasepool {
    VocoTypeConfigureEnvironment();
    [NSApplication sharedApplication];
    if (argc >= 2 && std::string(argv[1]) == "--pane-smoke") {
      NSDictionary<NSString *, id> *metrics =
          VocoTypeStatusPanelLongTextSmokeMetrics();
      if (argc >= 3) {
        NSData *data = [NSJSONSerialization dataWithJSONObject:metrics
                                                       options:NSJSONWritingPrettyPrinted
                                                         error:nil];
        [data writeToFile:[NSString stringWithUTF8String:argv[2]] atomically:YES];
      }
      return [metrics[@"success"] boolValue] ? 0 : 3;
    }
    if (argc >= 2 && std::string(argv[1]) == "--short-result-smoke") {
      NSDictionary<NSString *, id> *metrics =
          VocoTypeShortRecordingResultSmokeMetrics();
      if (argc >= 3) {
        NSData *data = [NSJSONSerialization dataWithJSONObject:metrics
                                                       options:NSJSONWritingPrettyPrinted
                                                         error:nil];
        [data writeToFile:[NSString stringWithUTF8String:argv[2]] atomically:YES];
      }
      return [metrics[@"success"] boolValue] ? 0 : 4;
    }
    if (argc >= 2 && std::string(argv[1]) == "--voice-lifecycle-smoke") {
      NSDictionary<NSString *, id> *metrics =
          VocoTypeVoiceLifecycleSmokeMetrics();
      if (argc >= 3) {
        NSData *data = [NSJSONSerialization dataWithJSONObject:metrics
                                                       options:NSJSONWritingPrettyPrinted
                                                         error:nil];
        [data writeToFile:[NSString stringWithUTF8String:argv[2]] atomically:YES];
      }
      return [metrics[@"success"] boolValue] ? 0 : 5;
    }
    std::thread([] {
      (void)vocotype::desktop::ensure_native_core(
          vocotype::desktop::backend_socket_path(),
          vocotype::desktop::runtime_config_path(), 45000);
    }).detach();
    NSBundle *bundle = [NSBundle mainBundle];
    NSString *connection =
        [bundle objectForInfoDictionaryKey:@"InputMethodConnectionName"];
    IMKServer *server = [[IMKServer alloc]
        initWithName:connection
     bundleIdentifier:bundle.bundleIdentifier];
    if (!server)
      return 2;
    VocoTypeInstallGlobalHotKeys();
    [NSApp run];
  }
  return 0;
}
