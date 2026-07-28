#pragma once

#import <InputMethodKit/InputMethodKit.h>

@interface VocoTypeInputController : IMKInputController
- (BOOL)handleGlobalHotKey:(NSUInteger)identifier pressed:(BOOL)pressed;
@end

FOUNDATION_EXPORT void VocoTypeInstallGlobalHotKeys(void);
FOUNDATION_EXPORT void VocoTypeReloadGlobalHotKeys(void);
FOUNDATION_EXPORT NSDictionary<NSString *, id> *VocoTypeStatusPanelLongTextSmokeMetrics(void);
FOUNDATION_EXPORT NSDictionary<NSString *, id> *VocoTypeShortRecordingResultSmokeMetrics(void);
FOUNDATION_EXPORT NSDictionary<NSString *, id> *VocoTypeVoiceLifecycleSmokeMetrics(void);
