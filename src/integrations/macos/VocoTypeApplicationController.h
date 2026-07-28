#pragma once

#import <AppKit/AppKit.h>

@interface VocoTypeApplicationController : NSObject <NSApplicationDelegate>
- (void)install;
- (void)showSettingsWindow;
- (void)showSettingsPage:(NSInteger)index;
- (void)scrollCurrentPageToFraction:(double)fraction;
- (BOOL)captureSettingsWindowToPath:(NSString *)path;
- (void)runMicrophoneSmokeTestToPath:(NSString *)path
                          completion:(void (^)(void))completion;
- (BOOL)runApiKeyPasteSmokeTestWithText:(NSString *)text;
- (BOOL)runMenuSmokeTestToPath:(NSString *)path;
- (BOOL)runTabSwitchSmokeTestToPath:(NSString *)path;
- (BOOL)runTermsPageSmokeTestToPath:(NSString *)path;
@end
