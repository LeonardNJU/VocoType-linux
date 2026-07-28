#import <AppKit/AppKit.h>

#import "VocoTypeApplicationController.h"
#import "VocoTypeRuntime.h"

#include <algorithm>
#include <fstream>
#include <string>

int main(int argc, const char *argv[]) {
  @autoreleasepool {
    std::string snapshot;
    std::string microphone_smoke;
    std::string paste_smoke;
    std::string menu_smoke;
    std::string tab_switch_smoke;
    std::string terms_page_smoke;
    std::string appearance;
    double scroll_fraction = 0.0;
    NSInteger page = 0;
    for (int index = 1; index < argc; ++index) {
      const std::string argument = argv[index];
      if (argument == "--settings-snapshot" && index + 1 < argc)
        snapshot = argv[++index];
      else if (argument == "--settings-page" && index + 1 < argc)
        page = std::clamp<NSInteger>(std::stol(argv[++index]), 0, 7);
      else if (argument == "--microphone-smoke" && index + 1 < argc) {
        microphone_smoke = argv[++index];
        page = 2;
      } else if (argument == "--paste-smoke" && index + 1 < argc) {
        paste_smoke = argv[++index];
        page = 4;
      } else if (argument == "--menu-smoke" && index + 1 < argc) {
        menu_smoke = argv[++index];
        page = 0;
      } else if (argument == "--tab-switch-smoke" && index + 1 < argc) {
        tab_switch_smoke = argv[++index];
        page = 1;
      } else if (argument == "--terms-page-smoke" && index + 1 < argc) {
        terms_page_smoke = argv[++index];
        page = 3;
      } else if (argument == "--appearance" && index + 1 < argc) {
        appearance = argv[++index];
      } else if (argument == "--settings-scroll" && index + 1 < argc) {
        scroll_fraction = std::clamp(std::stod(argv[++index]), 0.0, 1.0);
      }
    }
    NSString *installationError = nil;
    if (!VocoTypeEnsureEmbeddedInputMethod(&installationError)) {
      [NSApplication sharedApplication];
      NSAlert *alert = [[NSAlert alloc] init];
      alert.alertStyle = NSAlertStyleCritical;
      alert.messageText = @"VoCoType 安装未完成";
      alert.informativeText = installationError ? installationError : @"无法安装输入法组件。";
      [alert addButtonWithTitle:@"退出"];
      [alert runModal];
      return 2;
    }
    VocoTypeConfigureEnvironment();
    [NSApplication sharedApplication];
    if (appearance == "dark")
      NSApp.appearance = [NSAppearance appearanceNamed:NSAppearanceNameDarkAqua];
    else if (appearance == "light")
      NSApp.appearance = [NSAppearance appearanceNamed:NSAppearanceNameAqua];
    VocoTypeApplicationController *controller =
        [[VocoTypeApplicationController alloc] init];
    NSApp.delegate = controller;
    [controller install];
    dispatch_async(dispatch_get_main_queue(), ^{
      [controller showSettingsPage:page];
      [controller scrollCurrentPageToFraction:scroll_fraction];
      if (!terms_page_smoke.empty()) {
        const std::string output = terms_page_smoke;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                     static_cast<int64_t>(300 * NSEC_PER_MSEC)),
                       dispatch_get_main_queue(), ^{
          const BOOL success = [controller runTermsPageSmokeTestToPath:
              [NSString stringWithUTF8String:output.c_str()]];
          if (!success)
            std::exit(1);
          [NSApp terminate:nil];
        });
      } else if (!tab_switch_smoke.empty()) {
        const std::string output = tab_switch_smoke;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                     static_cast<int64_t>(300 * NSEC_PER_MSEC)),
                       dispatch_get_main_queue(), ^{
          const BOOL success = [controller runTabSwitchSmokeTestToPath:
              [NSString stringWithUTF8String:output.c_str()]];
          if (!success)
            std::exit(1);
          [NSApp terminate:nil];
        });
      } else if (!menu_smoke.empty()) {
        const std::string output = menu_smoke;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                     static_cast<int64_t>(300 * NSEC_PER_MSEC)),
                       dispatch_get_main_queue(), ^{
          const BOOL success = [controller runMenuSmokeTestToPath:
              [NSString stringWithUTF8String:output.c_str()]];
          if (!success)
            std::exit(1);
          [NSApp terminate:nil];
        });
      } else if (!microphone_smoke.empty()) {
        const std::string output = microphone_smoke;
        [controller runMicrophoneSmokeTestToPath:
                        [NSString stringWithUTF8String:output.c_str()]
                                         completion:^{
          [NSApp terminate:nil];
        }];
      } else if (!paste_smoke.empty()) {
        const std::string output = paste_smoke;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                     static_cast<int64_t>(300 * NSEC_PER_MSEC)),
                       dispatch_get_main_queue(), ^{
          const BOOL success =
              [controller runApiKeyPasteSmokeTestWithText:@"sk-test-paste-123"];
          std::ofstream stream(output, std::ios::trunc);
          stream << "{\"success\":" << (success ? "true" : "false")
                 << "}\n";
          stream.close();
          [NSApp terminate:nil];
        });
      } else if (!snapshot.empty()) {
        const std::string output = snapshot;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                     static_cast<int64_t>(500 * NSEC_PER_MSEC)),
                       dispatch_get_main_queue(), ^{
          (void)[controller captureSettingsWindowToPath:
                                [NSString stringWithUTF8String:output.c_str()]];
          [NSApp terminate:nil];
        });
      }
    });
    [NSApp run];
  }
  return 0;
}
