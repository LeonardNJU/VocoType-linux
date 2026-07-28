#import <AppKit/AppKit.h>

#import "VocoTypeRuntime.h"

#include "vocotype/desktop/config.hpp"

#include <cstdlib>
#include <algorithm>
#include <filesystem>
#include <unistd.h>

#include <nlohmann/json.hpp>

namespace {
using Json = nlohmann::json;

std::filesystem::path model_root() {
  const auto preferred =
      vocotype::desktop::cache_dir() / "modelscope/hub/models";
  const auto legacy =
      vocotype::desktop::home_path() / ".cache/modelscope/hub/models";
  if (!std::filesystem::exists(preferred) &&
      std::filesystem::is_directory(legacy))
    return legacy;
  return preferred;
}

void ensure_runtime_configuration() {
  const auto resources = vocotype::desktop::runtime_root();
  const auto models = model_root();
  Json config = vocotype::desktop::read_shared_config(true);
  if (!config.is_object())
    config = Json::object();
  config["core"]["socket_path"] = vocotype::desktop::backend_socket_path();
  config["audio"]["min_recording_ms"] =
      config.value("audio", Json::object()).value("min_recording_ms", 500);

  const auto offline_worker = resources / "bin/vocotype-offline-worker";
  config["asr"]["native_enabled"] =
      std::filesystem::is_regular_file(offline_worker);
  config["asr"]["worker_path"] = offline_worker.string();
  config["asr"]["model_dir"] =
      (models / "iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx").string();
  config["asr"]["vad_model_dir"] =
      (models / "iic/speech_fsmn_vad_zh-cn-16k-common-onnx").string();
  config["asr"]["punc_model_dir"] =
      (models / "iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx").string();
  config["asr"]["use_vad"] = false;
  config["asr"]["use_punc"] = true;

  const auto streaming_worker = resources / "bin/vocotype-streaming-worker";
  config["asr_streaming"]["enabled"] =
      std::filesystem::is_regular_file(streaming_worker);
  config["asr_streaming"]["worker_path"] = streaming_worker.string();
  config["asr_streaming"]["model_dir"] =
      (models / "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online-onnx").string();
  const double streaming_request_timeout =
      config["asr_streaming"].value("request_timeout_s", 8.0);
  config["asr_streaming"]["request_timeout_s"] =
      std::max(8.0, streaming_request_timeout);
  vocotype::desktop::write_shared_config(std::move(config));

  if (!std::filesystem::is_regular_file(vocotype::desktop::macos_config_path()))
    vocotype::desktop::write_macos_hotkeys(
        {{"transcribe", "F9"}, {"polish", "Shift+F9"}, {"edit", "Ctrl+F9"}});
}


NSString *bundle_version_at_path(NSString *path) {
  NSBundle *bundle = [NSBundle bundleWithPath:path];
  NSString *version =
      [bundle objectForInfoDictionaryKey:@"VoCoTypeFullVersion"];
  if (version.length == 0)
    version = [bundle objectForInfoDictionaryKey:@"CFBundleVersion"];
  return version ? version : @"";
}

BOOL run_task(NSString *executable, NSArray<NSString *> *arguments,
              NSString **output) {
  if (![NSFileManager.defaultManager isExecutableFileAtPath:executable]) {
    if (output)
      *output = [NSString stringWithFormat:@"缺少可执行文件：%@", executable];
    return NO;
  }
  NSPipe *pipe = [NSPipe pipe];
  NSTask *task = [[NSTask alloc] init];
  task.executableURL = [NSURL fileURLWithPath:executable];
  task.arguments = arguments;
  task.standardOutput = pipe;
  task.standardError = pipe;
  @try {
    [task launch];
    [task waitUntilExit];
  } @catch (NSException *exception) {
    if (output)
      *output = exception.reason ? exception.reason : @"无法启动安装工具";
    return NO;
  }
  NSData *data = [pipe.fileHandleForReading readDataToEndOfFile];
  NSString *message = [[NSString alloc] initWithData:data
                                            encoding:NSUTF8StringEncoding];
  if (output)
    *output = message ? message : @"";
  return task.terminationStatus == 0;
}

void stop_installed_runtime(NSString *destination) {
  for (NSRunningApplication *application in
       [NSRunningApplication runningApplicationsWithBundleIdentifier:
           @"io.github.LeonardNJU.VoCoTypeLinux.InputMethod"]) {
    [application terminate];
  }
  NSArray<NSString *> *runtimes = @[
    @"vocotype-core", @"vocotype-audio-recorder",
    @"vocotype-streaming-worker", @"vocotype-offline-worker"
  ];
  for (NSString *runtime in runtimes) {
    NSString *pattern = [destination stringByAppendingPathComponent:
        [@"Contents/Resources/bin" stringByAppendingPathComponent:runtime]];
    (void)run_task(@"/usr/bin/pkill", @[ @"-f", pattern ], nil);
  }
  NSString *socket = [NSString stringWithFormat:@"/tmp/vocotype-%u.sock",
                                                getuid()];
  [NSFileManager.defaultManager removeItemAtPath:socket error:nil];
  [NSFileManager.defaultManager
      removeItemAtPath:[socket stringByAppendingString:@".lock"] error:nil];
}

} // namespace

BOOL VocoTypeEnsureEmbeddedInputMethod(NSString **errorMessage) {
  NSBundle *host = NSBundle.mainBundle;
  NSString *source = [host.resourcePath
      stringByAppendingPathComponent:@"InputMethod/VoCoType-linux.app"];
  BOOL isDirectory = NO;
  if (![NSFileManager.defaultManager fileExistsAtPath:source
                                           isDirectory:&isDirectory] ||
      !isDirectory) {
    return YES; // Build-tree settings app: no embedded release payload.
  }

  NSString *destination = [NSHomeDirectory() stringByAppendingPathComponent:
      @"Library/Input Methods/VoCoType-linux.app"];
  NSString *sourceVersion = bundle_version_at_path(source);
  NSString *destinationVersion = bundle_version_at_path(destination);
  BOOL destinationExists =
      [NSFileManager.defaultManager fileExistsAtPath:destination];
  BOOL needsInstall = !destinationExists || sourceVersion.length == 0 ||
                      ![sourceVersion isEqualToString:destinationVersion];
  if (!needsInstall)
    return YES;

  NSFileManager *manager = NSFileManager.defaultManager;
  NSString *parent = [destination stringByDeletingLastPathComponent];
  NSError *error = nil;
  if (![manager createDirectoryAtPath:parent
          withIntermediateDirectories:YES attributes:nil error:&error]) {
    if (errorMessage)
      *errorMessage = [NSString stringWithFormat:
          @"无法创建输入法目录：%@", error.localizedDescription];
    return NO;
  }

  if (destinationExists) {
    NSString *oldTool = [destination stringByAppendingPathComponent:
        @"Contents/Resources/bin/vocotype-input-source-tool"];
    (void)run_task(oldTool,
                   @[ @"--disable",
                      @"io.github.LeonardNJU.VoCoTypeLinux.InputMethod" ],
                   nil);
  }
  stop_installed_runtime(destination);

  NSString *lsregister =
      @"/System/Library/Frameworks/CoreServices.framework/Versions/A/"
       "Frameworks/LaunchServices.framework/Versions/A/Support/lsregister";
  if (destinationExists)
    (void)run_task(lsregister, @[ @"-u", destination ], nil);

  NSString *temporary = [parent stringByAppendingPathComponent:
      [NSString stringWithFormat:@".VoCoType-linux-%@.installing",
                                 NSUUID.UUID.UUIDString]];
  [manager removeItemAtPath:temporary error:nil];
  if (![manager copyItemAtPath:source toPath:temporary error:&error]) {
    if (errorMessage)
      *errorMessage = [NSString stringWithFormat:
          @"无法复制输入法组件：%@", error.localizedDescription];
    return NO;
  }

  NSString *adhocMarker =
      [host.resourcePath stringByAppendingPathComponent:@".adhoc-test"];
  if ([manager fileExistsAtPath:adhocMarker])
    (void)run_task(@"/usr/bin/xattr",
                   @[ @"-dr", @"com.apple.quarantine", temporary ], nil);

  if (destinationExists &&
      ![manager removeItemAtPath:destination error:&error]) {
    [manager removeItemAtPath:temporary error:nil];
    if (errorMessage)
      *errorMessage = [NSString stringWithFormat:
          @"无法替换旧输入法组件：%@", error.localizedDescription];
    return NO;
  }
  if (![manager moveItemAtPath:temporary toPath:destination error:&error]) {
    [manager removeItemAtPath:temporary error:nil];
    if (errorMessage)
      *errorMessage = [NSString stringWithFormat:
          @"无法完成输入法安装：%@", error.localizedDescription];
    return NO;
  }

  (void)run_task(lsregister, @[ @"-f", @"-R", destination ], nil);
  NSString *tool = [destination stringByAppendingPathComponent:
      @"Contents/Resources/bin/vocotype-input-source-tool"];
  NSString *toolOutput = nil;
  BOOL activated = run_task(
      tool,
      @[ @"--install", destination,
         @"io.github.LeonardNJU.VoCoTypeLinux.InputMethod" ],
      &toolOutput);
  // On a fresh registration macOS can report selected=false for several
  // seconds even though TISRegister/TISEnable/TISSelect all returned noErr.
  // A subsequent activation after LaunchServices has propagated succeeds.
  for (NSInteger attempt = 0; !activated && attempt < 3; ++attempt) {
    [NSThread sleepForTimeInterval:0.5 * (attempt + 1)];
    activated = run_task(
        tool,
        @[ @"--activate",
           @"io.github.LeonardNJU.VoCoTypeLinux.InputMethod" ],
        &toolOutput);
  }
  if (!activated) {
    if (errorMessage)
      *errorMessage = [NSString stringWithFormat:
          @"输入法组件已复制，但系统激活失败。\n\n%@",
          toolOutput.length > 0 ? toolOutput : @"未知错误"];
    return NO;
  }
  return YES;
}

void VocoTypeConfigureEnvironment(void) {
  NSBundle *bundle = [NSBundle mainBundle];
  NSString *resource_path = bundle.resourcePath;
  if ([bundle.bundleIdentifier isEqualToString:
          @"io.github.LeonardNJU.VoCoTypeLinux.Settings"]) {
    NSString *nested = [bundle.bundlePath stringByDeletingLastPathComponent];
    NSString *nestedCore = [nested stringByAppendingPathComponent:
        @"bin/vocotype-core"];
    if ([NSFileManager.defaultManager isExecutableFileAtPath:nestedCore]) {
      resource_path = nested;
    } else {
      NSArray<NSString *> *candidates = @[
        [NSHomeDirectory() stringByAppendingPathComponent:
            @"Library/Input Methods/VoCoType-linux.app/Contents/Resources"],
        @"/Library/Input Methods/VoCoType-linux.app/Contents/Resources",
      ];
      resource_path = nil;
      for (NSString *candidate in candidates) {
        NSString *core = [candidate stringByAppendingPathComponent:
            @"bin/vocotype-core"];
        if ([NSFileManager.defaultManager isExecutableFileAtPath:core]) {
          resource_path = candidate;
          break;
        }
      }
    }
  }
  if (resource_path.length > 0)
    setenv("VOCOTYPE_RUNTIME_DIR", resource_path.fileSystemRepresentation, 1);

  const auto config = vocotype::desktop::shared_config_path();
  const auto socket = vocotype::desktop::backend_socket_path();
  const auto cache = vocotype::desktop::cache_dir();
  std::filesystem::create_directories(config.parent_path());
  std::filesystem::create_directories(cache);
  setenv("VOCOTYPE_CONFIG", config.c_str(), 1);
  setenv("VOCOTYPE_SOCKET", socket.c_str(), 1);
  setenv("VOCOTYPE_CACHE_DIR", cache.c_str(), 1);

  const auto terms = vocotype::desktop::terms_path();
  if (!std::filesystem::exists(terms)) {
    const auto bundled_terms =
        vocotype::desktop::runtime_root() / "share/terms.yaml";
    if (std::filesystem::is_regular_file(bundled_terms)) {
      std::filesystem::create_directories(terms.parent_path());
      std::error_code copy_error;
      std::filesystem::copy_file(
          bundled_terms, terms,
          std::filesystem::copy_options::skip_existing, copy_error);
    }
  }
  setenv("VOCOTYPE_TERMS_FILE", terms.c_str(), 1);
  ensure_runtime_configuration();
}
