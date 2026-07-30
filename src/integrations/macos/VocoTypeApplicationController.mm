#import "VocoTypeApplicationController.h"
#import <AVFAudio/AVAudioApplication.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>

#include "vocotype/desktop/audio.hpp"
#include "vocotype/desktop/config.hpp"
#include "vocotype/desktop/ipc.hpp"
#include "vocotype/desktop/settings_backend.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cctype>
#include <filesystem>
#include <functional>
#include <fstream>
#include <optional>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

@interface VocoTypeNumberBadgeView : NSView
- (instancetype)initWithNumber:(NSInteger)number;
@end

namespace {
using Json = nlohmann::json;
namespace settings = vocotype::desktop::settings;

constexpr NSInteger kOverviewPage = 0;
constexpr NSInteger kGeneralPage = 1;
constexpr NSInteger kPlaygroundPage = 2;
constexpr NSInteger kTermsPage = 3;
constexpr NSInteger kDoctorPage = 5;

NSString *to_ns(const std::string &text) {
  NSString *value = [[NSString alloc] initWithBytes:text.data()
                                              length:text.size()
                                            encoding:NSUTF8StringEncoding];
  return value ? value : @"";
}

std::string to_utf8(NSString *text) {
  if (!text)
    return {};
  const char *value = text.UTF8String;
  return value ? value : "";
}

NSString *trimmed(NSString *value) {
  return [value stringByTrimmingCharactersInSet:
                    NSCharacterSet.whitespaceAndNewlineCharacterSet];
}

std::vector<std::string> token_field_values(NSTokenField *field) {
  std::vector<std::string> values;
  if (!field)
    return values;
  id object = field.objectValue;
  NSArray *tokens = [object isKindOfClass:NSArray.class] ? object : nil;
  if (!tokens) {
    tokens = [field.stringValue componentsSeparatedByCharactersInSet:
        [NSCharacterSet characterSetWithCharactersInString:@",，\n"]];
  }
  for (id token in tokens) {
    NSString *text = nil;
    if ([token isKindOfClass:NSString.class])
      text = token;
    else
      text = [token description];
    text = trimmed(text ? text : @"");
    if (text.length == 0)
      continue;
    const std::string value = to_utf8(text);
    if (std::find(values.begin(), values.end(), value) == values.end())
      values.push_back(value);
  }
  return values;
}

NSString *app_version(void) {
  NSBundle *bundle = NSBundle.mainBundle;
  NSString *full = [bundle objectForInfoDictionaryKey:@"VoCoTypeFullVersion"];
  if (full.length > 0)
    return full;
  NSString *short_version =
      [bundle objectForInfoDictionaryKey:@"CFBundleShortVersionString"];
  return short_version.length > 0 ? short_version : @"unknown";
}

NSColor *page_background_color(void) {
  return [NSColor colorWithName:@"VoCoTypePageBackground"
                 dynamicProvider:^NSColor *(NSAppearance *appearance) {
    NSString *best = [appearance bestMatchFromAppearancesWithNames:@[
      NSAppearanceNameAqua, NSAppearanceNameDarkAqua
    ]];
    return [best isEqualToString:NSAppearanceNameDarkAqua]
        ? [NSColor colorWithWhite:0.105 alpha:1.0]
        : [NSColor colorWithWhite:0.965 alpha:1.0];
  }];
}

NSColor *card_background_color(void) {
  return [NSColor colorWithName:@"VoCoTypeCardBackground"
                 dynamicProvider:^NSColor *(NSAppearance *appearance) {
    NSString *best = [appearance bestMatchFromAppearancesWithNames:@[
      NSAppearanceNameAqua, NSAppearanceNameDarkAqua
    ]];
    return [best isEqualToString:NSAppearanceNameDarkAqua]
        ? [NSColor colorWithWhite:0.155 alpha:1.0]
        : NSColor.whiteColor;
  }];
}

NSColor *card_border_color(void) {
  return [NSColor colorWithName:@"VoCoTypeCardBorder"
                 dynamicProvider:^NSColor *(NSAppearance *appearance) {
    NSString *best = [appearance bestMatchFromAppearancesWithNames:@[
      NSAppearanceNameAqua, NSAppearanceNameDarkAqua
    ]];
    return [best isEqualToString:NSAppearanceNameDarkAqua]
        ? [NSColor colorWithWhite:0.30 alpha:1.0]
        : [NSColor colorWithWhite:0.82 alpha:1.0];
  }];
}

NSTextField *plain_label(NSString *text) {
  NSTextField *view = [NSTextField labelWithString:(text ? text : @"")];
  view.selectable = YES;
  view.alignment = NSTextAlignmentLeft;
  view.lineBreakMode = NSLineBreakByWordWrapping;
  view.maximumNumberOfLines = 0;
  return view;
}

NSTextField *status_label(NSString *text) {
  NSTextField *view = plain_label(text);
  view.font = [NSFont systemFontOfSize:11.5 weight:NSFontWeightRegular];
  view.textColor = NSColor.secondaryLabelColor;
  return view;
}

void collect_terms_page_views(NSView *view,
                              NSMutableSet<NSString *> *button_titles,
                              NSInteger *text_view_count) {
  if ([view isKindOfClass:NSButton.class]) {
    NSString *title = ((NSButton *)view).title;
    if (title.length > 0)
      [button_titles addObject:title];
  }
  if ([view isKindOfClass:NSTextView.class] && text_view_count)
    ++(*text_view_count);
  for (NSView *subview in view.subviews)
    collect_terms_page_views(subview, button_titles, text_view_count);
}

NSTextField *title_label(NSString *text, CGFloat size) {
  NSTextField *view = plain_label(text);
  view.font = [NSFont systemFontOfSize:size weight:NSFontWeightSemibold];
  view.textColor = NSColor.labelColor;
  return view;
}

NSTextField *text_field(NSString *placeholder) {
  NSTextField *field = [[NSTextField alloc] initWithFrame:NSZeroRect];
  field.placeholderString = placeholder;
  field.font = [NSFont systemFontOfSize:13.0];
  field.bezelStyle = NSTextFieldRoundedBezel;
  field.controlSize = NSControlSizeRegular;
  [field.heightAnchor constraintEqualToConstant:28.0].active = YES;
  [field.widthAnchor constraintEqualToConstant:340.0].active = YES;
  return field;
}

NSSecureTextField *secure_field(NSString *placeholder) {
  NSSecureTextField *field = [[NSSecureTextField alloc] initWithFrame:NSZeroRect];
  field.placeholderString = placeholder;
  field.font = [NSFont systemFontOfSize:13.0];
  field.bezelStyle = NSTextFieldRoundedBezel;
  field.controlSize = NSControlSizeRegular;
  [field.heightAnchor constraintEqualToConstant:28.0].active = YES;
  [field.widthAnchor constraintEqualToConstant:340.0].active = YES;
  return field;
}

NSButton *switch_button(void) {
  NSButton *button = [[NSButton alloc] initWithFrame:NSZeroRect];
  button.buttonType = NSButtonTypeSwitch;
  button.title = @"";
  button.controlSize = NSControlSizeSmall;
  return button;
}

NSButton *action_button(NSString *title, id target, SEL action) {
  NSButton *button = [NSButton buttonWithTitle:title target:target action:action];
  button.bezelStyle = NSBezelStyleRounded;
  button.controlSize = NSControlSizeRegular;
  button.font = [NSFont systemFontOfSize:12.5 weight:NSFontWeightMedium];
  return button;
}

NSButton *primary_button(NSString *title, id target, SEL action) {
  NSButton *button = action_button(title, target, action);
  button.keyEquivalent = @"\r";
  button.bezelColor = NSColor.controlAccentColor;
  return button;
}

NSStackView *vertical_stack(CGFloat spacing) {
  NSStackView *stack = [[NSStackView alloc] initWithFrame:NSZeroRect];
  stack.orientation = NSUserInterfaceLayoutOrientationVertical;
  stack.alignment = NSLayoutAttributeLeft;
  stack.spacing = spacing;
  stack.translatesAutoresizingMaskIntoConstraints = NO;
  return stack;
}

NSStackView *horizontal_stack(CGFloat spacing) {
  NSStackView *stack = [[NSStackView alloc] initWithFrame:NSZeroRect];
  stack.orientation = NSUserInterfaceLayoutOrientationHorizontal;
  stack.alignment = NSLayoutAttributeCenterY;
  stack.spacing = spacing;
  stack.translatesAutoresizingMaskIntoConstraints = NO;
  return stack;
}

NSView *flexible_spacer(void) {
  NSView *spacer = [[NSView alloc] initWithFrame:NSZeroRect];
  [spacer setContentHuggingPriority:NSLayoutPriorityDefaultLow
                    forOrientation:NSLayoutConstraintOrientationHorizontal];
  [spacer setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow
                                   forOrientation:NSLayoutConstraintOrientationHorizontal];
  return spacer;
}

NSView *separator_line(void) {
  NSView *separator = [[NSView alloc] initWithFrame:NSZeroRect];
  separator.translatesAutoresizingMaskIntoConstraints = NO;
  separator.wantsLayer = YES;
  separator.layer.backgroundColor =
      [NSColor.separatorColor colorWithAlphaComponent:0.50].CGColor;
  [separator.heightAnchor constraintEqualToConstant:1.0].active = YES;
  return separator;
}

NSView *equal_columns(NSView *left, NSView *right, CGFloat spacing) {
  NSView *container = [[NSView alloc] initWithFrame:NSZeroRect];
  container.translatesAutoresizingMaskIntoConstraints = NO;
  [container setContentHuggingPriority:NSLayoutPriorityDefaultLow
                        forOrientation:NSLayoutConstraintOrientationHorizontal];
  [container setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow
                                       forOrientation:NSLayoutConstraintOrientationHorizontal];
  [container.widthAnchor constraintGreaterThanOrEqualToConstant:680.0].active = YES;
  [left setContentHuggingPriority:NSLayoutPriorityDefaultLow
                  forOrientation:NSLayoutConstraintOrientationHorizontal];
  [right setContentHuggingPriority:NSLayoutPriorityDefaultLow
                   forOrientation:NSLayoutConstraintOrientationHorizontal];
  left.translatesAutoresizingMaskIntoConstraints = NO;
  right.translatesAutoresizingMaskIntoConstraints = NO;
  [container addSubview:left];
  [container addSubview:right];
  [NSLayoutConstraint activateConstraints:@[
    [left.leadingAnchor constraintEqualToAnchor:container.leadingAnchor],
    [left.topAnchor constraintEqualToAnchor:container.topAnchor],
    [left.bottomAnchor constraintEqualToAnchor:container.bottomAnchor],
    [right.leadingAnchor constraintEqualToAnchor:left.trailingAnchor constant:spacing],
    [right.trailingAnchor constraintEqualToAnchor:container.trailingAnchor],
    [right.topAnchor constraintEqualToAnchor:container.topAnchor],
    [right.bottomAnchor constraintEqualToAnchor:container.bottomAnchor],
    [left.widthAnchor constraintEqualToAnchor:right.widthAnchor],
  ]];
  return container;
}

NSView *editor_column(NSString *title, NSString *subtitle, NSView *editor) {
  NSStackView *column = vertical_stack(6.0);
  column.alignment = NSLayoutAttributeWidth;
  [column addArrangedSubview:title_label(title, 12.5)];
  if (subtitle.length > 0) {
    NSTextField *hint = status_label(subtitle);
    hint.textColor = NSColor.tertiaryLabelColor;
    [column addArrangedSubview:hint];
  }
  [column addArrangedSubview:editor];
  return column;
}

void stretch_page_contents(NSStackView *page) {
  for (NSView *view in page.arrangedSubviews) {
    if ([view isKindOfClass:NSButton.class])
      continue;
    NSLayoutConstraint *constraint =
        [view.widthAnchor constraintEqualToAnchor:page.widthAnchor];
    constraint.priority = NSLayoutPriorityRequired;
    constraint.active = YES;
  }
}

NSView *section_heading(NSString *title, NSString *subtitle) {
  NSView *container = [[NSView alloc] initWithFrame:NSZeroRect];
  container.translatesAutoresizingMaskIntoConstraints = NO;
  NSTextField *heading = title_label(title, 13.0);
  heading.translatesAutoresizingMaskIntoConstraints = NO;
  [container addSubview:heading];
  NSMutableArray<NSLayoutConstraint *> *constraints = [NSMutableArray arrayWithArray:@[
    [heading.leadingAnchor constraintEqualToAnchor:container.leadingAnchor],
    [heading.trailingAnchor constraintLessThanOrEqualToAnchor:container.trailingAnchor],
    [heading.topAnchor constraintEqualToAnchor:container.topAnchor],
  ]];
  if (subtitle.length > 0) {
    NSTextField *hint = status_label(subtitle);
    hint.translatesAutoresizingMaskIntoConstraints = NO;
    hint.textColor = NSColor.tertiaryLabelColor;
    [container addSubview:hint];
    [constraints addObjectsFromArray:@[
      [hint.leadingAnchor constraintEqualToAnchor:container.leadingAnchor],
      [hint.trailingAnchor constraintLessThanOrEqualToAnchor:container.trailingAnchor],
      [hint.topAnchor constraintEqualToAnchor:heading.bottomAnchor constant:2.0],
      [hint.bottomAnchor constraintEqualToAnchor:container.bottomAnchor],
    ]];
  } else {
    [constraints addObject:[heading.bottomAnchor constraintEqualToAnchor:container.bottomAnchor]];
  }
  [NSLayoutConstraint activateConstraints:constraints];
  return container;
}

NSBox *card_with_stack(NSStackView **content_out) {
  NSBox *box = [[NSBox alloc] initWithFrame:NSZeroRect];
  box.boxType = NSBoxCustom;
  box.borderWidth = 1.0;
  box.borderColor = card_border_color();
  box.fillColor = card_background_color();
  box.cornerRadius = 11.0;
  box.translatesAutoresizingMaskIntoConstraints = NO;
  [box setContentHuggingPriority:NSLayoutPriorityDefaultLow
                 forOrientation:NSLayoutConstraintOrientationHorizontal];
  [box setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow
                                forOrientation:NSLayoutConstraintOrientationHorizontal];
  NSView *content = [[NSView alloc] initWithFrame:NSZeroRect];
  box.contentView = content;
  NSStackView *stack = vertical_stack(14.0);
  stack.alignment = NSLayoutAttributeWidth;
  [content addSubview:stack];
  [NSLayoutConstraint activateConstraints:@[
    [stack.leadingAnchor constraintEqualToAnchor:content.leadingAnchor constant:18.0],
    [stack.trailingAnchor constraintEqualToAnchor:content.trailingAnchor constant:-18.0],
    [stack.topAnchor constraintEqualToAnchor:content.topAnchor constant:16.0],
    [stack.bottomAnchor constraintEqualToAnchor:content.bottomAnchor constant:-16.0],
  ]];
  if (content_out)
    *content_out = stack;
  return box;
}

NSView *settings_row(NSString *title, NSString *subtitle, NSView *control) {
  NSView *container = [[NSView alloc] initWithFrame:NSZeroRect];
  container.translatesAutoresizingMaskIntoConstraints = NO;
  NSStackView *row = horizontal_stack(18.0);
  row.alignment = NSLayoutAttributeCenterY;
  [container addSubview:row];
  NSStackView *description = vertical_stack(2.0);
  NSTextField *heading = plain_label(title);
  heading.font = [NSFont systemFontOfSize:13.0 weight:NSFontWeightMedium];
  heading.textColor = NSColor.labelColor;
  [description addArrangedSubview:heading];
  if (subtitle.length > 0) {
    NSTextField *detail = status_label(subtitle);
    detail.textColor = NSColor.secondaryLabelColor;
    [description addArrangedSubview:detail];
  }
  [description.widthAnchor constraintEqualToConstant:255.0].active = YES;
  [row addArrangedSubview:description];
  [row addArrangedSubview:flexible_spacer()];
  if (control) {
    control.translatesAutoresizingMaskIntoConstraints = NO;
    [control setContentCompressionResistancePriority:NSLayoutPriorityRequired
                                      forOrientation:NSLayoutConstraintOrientationHorizontal];
    [row addArrangedSubview:control];
  }
  [NSLayoutConstraint activateConstraints:@[
    [row.leadingAnchor constraintEqualToAnchor:container.leadingAnchor],
    [row.trailingAnchor constraintEqualToAnchor:container.trailingAnchor],
    [row.topAnchor constraintEqualToAnchor:container.topAnchor],
    [row.bottomAnchor constraintEqualToAnchor:container.bottomAnchor],
    [container.heightAnchor constraintGreaterThanOrEqualToConstant:44.0],
  ]];
  return container;
}

NSView *labeled_content_block(NSString *title, NSString *subtitle,
                              NSView *content) {
  NSView *container = [[NSView alloc] initWithFrame:NSZeroRect];
  container.translatesAutoresizingMaskIntoConstraints = NO;
  NSTextField *heading = title_label(title, 13.0);
  heading.translatesAutoresizingMaskIntoConstraints = NO;
  [container addSubview:heading];
  NSTextField *hint = nil;
  if (subtitle.length > 0) {
    hint = status_label(subtitle);
    hint.translatesAutoresizingMaskIntoConstraints = NO;
    [container addSubview:hint];
  }
  content.translatesAutoresizingMaskIntoConstraints = NO;
  [container addSubview:content];
  NSMutableArray<NSLayoutConstraint *> *constraints = [NSMutableArray arrayWithArray:@[
    [heading.leadingAnchor constraintEqualToAnchor:container.leadingAnchor],
    [heading.trailingAnchor constraintLessThanOrEqualToAnchor:container.trailingAnchor],
    [heading.topAnchor constraintEqualToAnchor:container.topAnchor],
    [content.leadingAnchor constraintEqualToAnchor:container.leadingAnchor],
    [content.trailingAnchor constraintEqualToAnchor:container.trailingAnchor],
    [content.bottomAnchor constraintEqualToAnchor:container.bottomAnchor],
  ]];
  if (hint) {
    [constraints addObjectsFromArray:@[
      [hint.leadingAnchor constraintEqualToAnchor:container.leadingAnchor],
      [hint.trailingAnchor constraintLessThanOrEqualToAnchor:container.trailingAnchor],
      [hint.topAnchor constraintEqualToAnchor:heading.bottomAnchor constant:2.0],
      [content.topAnchor constraintEqualToAnchor:hint.bottomAnchor constant:10.0],
    ]];
  } else {
    [constraints addObject:
        [content.topAnchor constraintEqualToAnchor:heading.bottomAnchor constant:8.0]];
  }
  [NSLayoutConstraint activateConstraints:constraints];
  return container;
}

NSBox *tutorial_step(NSInteger number, NSString *title, NSString *detail) {
  NSBox *box = [[NSBox alloc] initWithFrame:NSZeroRect];
  box.boxType = NSBoxCustom;
  box.borderWidth = 1.0;
  box.borderColor = card_border_color();
  box.fillColor = card_background_color();
  box.cornerRadius = 11.0;
  box.translatesAutoresizingMaskIntoConstraints = NO;
  [box setContentHuggingPriority:NSLayoutPriorityDefaultLow
                 forOrientation:NSLayoutConstraintOrientationHorizontal];
  [box setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow
                                forOrientation:NSLayoutConstraintOrientationHorizontal];
  NSView *content = [[NSView alloc] initWithFrame:NSZeroRect];
  box.contentView = content;

  VocoTypeNumberBadgeView *numberLabel =
      [[VocoTypeNumberBadgeView alloc] initWithNumber:number];
  numberLabel.translatesAutoresizingMaskIntoConstraints = NO;
  [content addSubview:numberLabel];

  NSTextField *heading = title_label(title, 13.5);
  heading.translatesAutoresizingMaskIntoConstraints = NO;
  [content addSubview:heading];
  NSTextField *body = status_label(detail);
  body.translatesAutoresizingMaskIntoConstraints = NO;
  body.font = [NSFont systemFontOfSize:12.0];
  [content addSubview:body];

  [NSLayoutConstraint activateConstraints:@[
    [numberLabel.leadingAnchor constraintEqualToAnchor:content.leadingAnchor constant:16.0],
    [numberLabel.topAnchor constraintEqualToAnchor:content.topAnchor constant:16.0],
    [numberLabel.widthAnchor constraintEqualToConstant:24.0],
    [numberLabel.heightAnchor constraintEqualToConstant:24.0],
    [heading.leadingAnchor constraintEqualToAnchor:numberLabel.trailingAnchor constant:12.0],
    [heading.trailingAnchor constraintEqualToAnchor:content.trailingAnchor constant:-16.0],
    [heading.topAnchor constraintEqualToAnchor:content.topAnchor constant:14.0],
    [body.leadingAnchor constraintEqualToAnchor:heading.leadingAnchor],
    [body.trailingAnchor constraintEqualToAnchor:content.trailingAnchor constant:-16.0],
    [body.topAnchor constraintEqualToAnchor:heading.bottomAnchor constant:4.0],
    [body.bottomAnchor constraintEqualToAnchor:content.bottomAnchor constant:-15.0],
    [box.heightAnchor constraintGreaterThanOrEqualToConstant:70.0],
  ]];
  return box;
}

NSScrollView *text_editor(NSTextView **view_out, CGFloat height, bool editable,
                          bool wrap = true) {
  NSScrollView *scroll = [[NSScrollView alloc] initWithFrame:NSZeroRect];
  scroll.hasVerticalScroller = YES;
  scroll.hasHorizontalScroller = !wrap;
  scroll.borderType = NSLineBorder;
  scroll.drawsBackground = YES;
  scroll.backgroundColor = NSColor.textBackgroundColor;
  scroll.wantsLayer = NO;
  NSTextView *view = [[NSTextView alloc]
      initWithFrame:NSMakeRect(0.0, 0.0, 680.0, height)];
  view.editable = editable;
  view.selectable = YES;
  view.richText = NO;
  view.allowsUndo = editable;
  view.usesFindBar = YES;
  view.drawsBackground = YES;
  view.backgroundColor = NSColor.textBackgroundColor;
  view.textColor = NSColor.textColor;
  view.textContainerInset = NSMakeSize(10.0, 10.0);
  view.font = [NSFont monospacedSystemFontOfSize:12.5
                                         weight:NSFontWeightRegular];
  view.minSize = NSMakeSize(0.0, height);
  view.maxSize = NSMakeSize(CGFLOAT_MAX, CGFLOAT_MAX);
  view.verticallyResizable = YES;
  view.horizontallyResizable = !wrap;
  view.autoresizingMask = wrap ? NSViewWidthSizable
                               : (NSViewWidthSizable | NSViewHeightSizable);
  view.textContainer.widthTracksTextView = wrap;
  view.textContainer.heightTracksTextView = NO;
  view.textContainer.containerSize =
      NSMakeSize(wrap ? 680.0 : CGFLOAT_MAX, CGFLOAT_MAX);
  scroll.documentView = view;
  [scroll.heightAnchor constraintEqualToConstant:height].active = YES;
  if (view_out)
    *view_out = view;
  return scroll;
}

std::string text_view_text(NSTextView *view) {
  return view ? to_utf8(view.string) : std::string();
}

void set_text(NSTextView *view, const std::string &text) {
  if (view)
    view.string = to_ns(text);
}

void set_status(NSTextField *label, NSString *text, bool error = false) {
  if (!label)
    return;
  label.stringValue = text ? text : @"";
  label.font = [NSFont systemFontOfSize:11.5 weight:error ? NSFontWeightMedium
                                                       : NSFontWeightRegular];
  label.textColor = error ? NSColor.systemRedColor : NSColor.secondaryLabelColor;
  label.alignment = NSTextAlignmentLeft;
}

bool valid_hotkey(NSString *value) {
  NSArray<NSString *> *parts = [trimmed(value) componentsSeparatedByString:@"+"];
  if (parts.count == 0)
    return false;
  bool found_key = false;
  for (NSString *raw in parts) {
    NSString *part = [trimmed(raw) lowercaseString];
    if ([part isEqualToString:@"shift"] || [part isEqualToString:@"ctrl"] ||
        [part isEqualToString:@"control"] || [part isEqualToString:@"alt"] ||
        [part isEqualToString:@"option"] || [part isEqualToString:@"cmd"] ||
        [part isEqualToString:@"command"] || [part isEqualToString:@"meta"] ||
        [part isEqualToString:@"super"])
      continue;
    if (found_key || part.length < 2 || [part characterAtIndex:0] != 'f')
      return false;
    NSInteger number = [[part substringFromIndex:1] integerValue];
    NSString *canonical = [NSString stringWithFormat:@"f%ld", (long)number];
    if (number < 1 || number > 20 || ![part isEqualToString:canonical])
      return false;
    found_key = true;
  }
  return found_key;
}

NSString *hotkey_from_event(NSEvent *event) {
  NSString *characters = event.charactersIgnoringModifiers;
  if (characters.length == 0)
    return nil;
  unichar key = [characters characterAtIndex:0];
  if (key < NSF1FunctionKey || key > NSF20FunctionKey)
    return nil;
  NSInteger number = static_cast<NSInteger>(key - NSF1FunctionKey + 1);
  NSMutableArray<NSString *> *parts = [NSMutableArray array];
  NSEventModifierFlags modifiers =
      event.modifierFlags & NSEventModifierFlagDeviceIndependentFlagsMask;
  if (modifiers & NSEventModifierFlagShift)
    [parts addObject:@"Shift"];
  if (modifiers & NSEventModifierFlagControl)
    [parts addObject:@"Ctrl"];
  if (modifiers & NSEventModifierFlagOption)
    [parts addObject:@"Option"];
  if (modifiers & NSEventModifierFlagCommand)
    [parts addObject:@"Command"];
  [parts addObject:[NSString stringWithFormat:@"F%ld", (long)number]];
  return [parts componentsJoinedByString:@"+"];
}

void stop_native_core(void) {
  const std::string socket = vocotype::desktop::backend_socket_path();
  if (!vocotype::desktop::native_core_ready(socket, 250))
    return;
  try {
    (void)vocotype::desktop::unix_json_request(
        socket, {{"type", "core_stop"}}, 1500);
  } catch (const std::exception &) {
  }
}

void run_async(std::function<Json()> work,
               std::function<void(Json)> completion) {
  std::thread([work = std::move(work), completion = std::move(completion)]() mutable {
    Json result;
    try {
      result = work();
    } catch (const std::exception &error) {
      result = {{"success", false}, {"error", error.what()}};
    }
    dispatch_async(dispatch_get_main_queue(), ^{
      completion(std::move(result));
    });
  }).detach();
}

NSString *percent_encode(NSString *text) {
  NSCharacterSet *allowed =
      [NSCharacterSet URLQueryAllowedCharacterSet].mutableCopy;
  NSString *encoded = [text stringByAddingPercentEncodingWithAllowedCharacters:allowed];
  return encoded ? encoded : @"";
}

} // namespace

@implementation VocoTypeNumberBadgeView {
  NSInteger _number;
}

- (instancetype)initWithNumber:(NSInteger)number {
  self = [super initWithFrame:NSZeroRect];
  if (self) {
    _number = number;
    self.wantsLayer = YES;
    self.layerContentsRedrawPolicy = NSViewLayerContentsRedrawOnSetNeedsDisplay;
    self.accessibilityLabel =
        [NSString stringWithFormat:@"步骤 %ld", (long)number];
  }
  return self;
}

- (BOOL)isOpaque { return NO; }

- (void)viewDidChangeEffectiveAppearance {
  [super viewDidChangeEffectiveAppearance];
  [self setNeedsDisplay:YES];
}

- (void)drawRect:(NSRect)dirtyRect {
  (void)dirtyRect;
  NSRect circle = NSInsetRect(self.bounds, 0.5, 0.5);
  [NSColor.controlAccentColor setFill];
  [[NSBezierPath bezierPathWithOvalInRect:circle] fill];

  NSString *text = [NSString stringWithFormat:@"%ld", (long)_number];
  NSFont *base = [NSFont systemFontOfSize:12.0 weight:NSFontWeightSemibold];
  NSFontDescriptor *roundedDescriptor =
      [base.fontDescriptor fontDescriptorWithDesign:NSFontDescriptorSystemDesignRounded];
  NSFont *font = roundedDescriptor
      ? [NSFont fontWithDescriptor:roundedDescriptor size:12.0]
      : base;
  NSDictionary<NSAttributedStringKey, id> *attributes = @{
    NSFontAttributeName: font,
    NSForegroundColorAttributeName: NSColor.whiteColor,
  };
  NSSize size = [text sizeWithAttributes:attributes];
  NSPoint origin = NSMakePoint(
      floor(NSMidX(self.bounds) - size.width / 2.0),
      floor(NSMidY(self.bounds) - size.height / 2.0 + 0.5));
  [text drawAtPoint:origin withAttributes:attributes];
}

@end

@interface VocoTypeSidebarButton : NSButton
@end

@implementation VocoTypeSidebarButton

+ (instancetype)buttonWithTitle:(NSString *)title
                         symbol:(NSString *)symbol
                         target:(id)target
                         action:(SEL)action {
  VocoTypeSidebarButton *button = [[self alloc] initWithFrame:NSZeroRect];
  button.title = title;
  button.target = target;
  button.action = action;
  button.buttonType = NSButtonTypeToggle;
  button.bordered = NO;
  button.alignment = NSTextAlignmentLeft;
  button.image = [NSImage imageWithSystemSymbolName:symbol
                           accessibilityDescription:title];
  button.imagePosition = NSImageLeading;
  button.imageHugsTitle = YES;
  button.font = [NSFont systemFontOfSize:13.0 weight:NSFontWeightMedium];
  button.contentTintColor = NSColor.secondaryLabelColor;
  button.wantsLayer = YES;
  button.layer.cornerRadius = 7.0;
  [button.heightAnchor constraintEqualToConstant:34.0].active = YES;
  return button;
}

- (void)setState:(NSControlStateValue)state {
  [super setState:state];
  [self refreshAppearance];
}

- (void)viewDidChangeEffectiveAppearance {
  [super viewDidChangeEffectiveAppearance];
  [self refreshAppearance];
}

- (void)refreshAppearance {
  const BOOL selected = self.state == NSControlStateValueOn;
  NSColor *background = selected
      ? [NSColor.controlAccentColor colorWithAlphaComponent:0.14]
      : NSColor.clearColor;
  self.layer.backgroundColor = background.CGColor;
  self.contentTintColor = selected ? NSColor.controlAccentColor
                                   : NSColor.secondaryLabelColor;
}

@end

@interface VocoTypeFlippedView : NSView
@end

@implementation VocoTypeFlippedView
- (BOOL)isFlipped { return YES; }
@end

@interface VocoTypeWaveformView : NSView
- (void)clearWaveform;
- (void)appendMinimum:(double)minimum maximum:(double)maximum;
@end

@implementation VocoTypeWaveformView {
  std::vector<std::pair<double, double>> _points;
  double _displayGain;
}

- (BOOL)isFlipped { return YES; }

- (void)clearWaveform {
  _points.clear();
  _displayGain = 1.0;
  [self setNeedsDisplay:YES];
}

- (void)appendMinimum:(double)minimum maximum:(double)maximum {
  _points.emplace_back(minimum, maximum);
  if (_points.size() > 360)
    _points.erase(_points.begin(), _points.begin() +
                                     static_cast<std::ptrdiff_t>(_points.size() - 360));

  const std::size_t window = std::min<std::size_t>(_points.size(), 96);
  double recentPeak = 0.0;
  for (std::size_t index = _points.size() - window; index < _points.size(); ++index) {
    recentPeak = std::max(recentPeak,
                          std::max(std::abs(_points[index].first),
                                   std::abs(_points[index].second)));
  }
  const double desiredGain = recentPeak < 0.004
                                 ? 1.0
                                 : std::clamp(0.72 / std::max(recentPeak, 0.012),
                                              1.0, 12.0);
  if (_displayGain <= 0.0)
    _displayGain = desiredGain;
  const double smoothing = desiredGain < _displayGain ? 0.42 : 0.10;
  _displayGain += (desiredGain - _displayGain) * smoothing;
  [self setNeedsDisplay:YES];
}

- (void)drawRect:(NSRect)dirtyRect {
  [super drawRect:dirtyRect];
  NSRect surface = NSInsetRect(self.bounds, 0.5, 0.5);
  NSBezierPath *background = [NSBezierPath bezierPathWithRoundedRect:surface
                                                             xRadius:9.0
                                                             yRadius:9.0];
  [NSColor.textBackgroundColor setFill];
  [background fill];
  [[NSColor.separatorColor colorWithAlphaComponent:0.65] setStroke];
  background.lineWidth = 1.0;
  [background stroke];
  [[NSColor.separatorColor colorWithAlphaComponent:0.45] setStroke];
  NSBezierPath *center = [NSBezierPath bezierPath];
  [center moveToPoint:NSMakePoint(14.0, NSMidY(self.bounds))];
  [center lineToPoint:NSMakePoint(NSWidth(self.bounds) - 14.0,
                                  NSMidY(self.bounds))];
  [center stroke];
  if (_points.empty()) {
    NSString *hint = @"录音后将在这里显示实时波形";
    NSDictionary *attributes = @{
      NSFontAttributeName: [NSFont systemFontOfSize:11.5],
      NSForegroundColorAttributeName: NSColor.tertiaryLabelColor,
    };
    NSSize size = [hint sizeWithAttributes:attributes];
    [hint drawAtPoint:NSMakePoint((NSWidth(self.bounds) - size.width) / 2.0,
                                  (NSHeight(self.bounds) - size.height) / 2.0 - 15.0)
       withAttributes:attributes];
    return;
  }
  [NSColor.controlAccentColor setStroke];
  NSBezierPath *wave = [NSBezierPath bezierPath];
  wave.lineWidth = 1.25;
  const double middle = NSMidY(self.bounds);
  const double amplitude = NSHeight(self.bounds) * 0.43;
  const double left = 14.0;
  const double width = std::max(1.0, NSWidth(self.bounds) - 28.0);
  for (std::size_t index = 0; index < _points.size(); ++index) {
    const double x = _points.size() <= 1
                         ? left
                         : left + width * static_cast<double>(index) /
                                      static_cast<double>(_points.size() - 1);
    const auto [minimum, maximum] = _points[index];
    const double scaledMinimum = std::clamp(minimum * _displayGain, -1.0, 1.0);
    const double scaledMaximum = std::clamp(maximum * _displayGain, -1.0, 1.0);
    [wave moveToPoint:NSMakePoint(x, middle - scaledMaximum * amplitude)];
    [wave lineToPoint:NSMakePoint(x, middle - scaledMinimum * amplitude)];
  }
  [wave stroke];
  if (_displayGain > 1.15) {
    NSString *zoom = [NSString stringWithFormat:@"自动 ×%.1f", _displayGain];
    NSDictionary *attributes = @{
      NSFontAttributeName: [NSFont monospacedDigitSystemFontOfSize:10.0
                                                            weight:NSFontWeightRegular],
      NSForegroundColorAttributeName: NSColor.tertiaryLabelColor,
    };
    NSSize size = [zoom sizeWithAttributes:attributes];
    [zoom drawAtPoint:NSMakePoint(NSWidth(self.bounds) - size.width - 12.0, 8.0)
       withAttributes:attributes];
  }
}
@end

@interface VocoTypeApplicationController ()
- (void)installStandardMainMenu;
- (void)closeSettingsWindow:(id)sender;
- (void)performControlUndo:(id)sender;
- (void)performControlRedo:(id)sender;
- (NSWindow *)buildSettingsWindow;
- (void)buildOverviewPage;
- (void)buildGeneralPage;
- (void)buildPlaygroundPage;
- (void)buildTermsPage;
- (void)buildAIPage;
- (void)buildDoctorPage;
- (void)buildTutorialPage;
- (void)buildFeedbackPage;
- (NSScrollView *)pageWithTitle:(NSString *)title
                       subtitle:(NSString *)subtitle
                          stack:(NSStackView **)stackOut;
- (void)addPage:(NSView *)view identifier:(NSString *)identifier;
- (void)selectPage:(NSInteger)index;
- (void)selectPageFromSender:(id)sender;
- (void)showSettings:(id)sender;
- (void)saveSettings:(id)sender;
- (BOOL)persistSettings;
- (void)reloadFields;
- (void)refreshOverview:(id)sender;
- (void)activatePalette:(id)sender;
- (void)restartCore:(id)sender;
- (void)downloadModels:(id)sender;
- (void)refreshAudioDevices:(id)sender;
- (void)audioSelectionChanged:(id)sender;
- (void)beginHotkeyCapture:(id)sender;
- (void)previewNormalization:(id)sender;
- (void)addTerm:(id)sender;
- (void)addProtectedPhrase:(id)sender;
- (void)importTerms:(id)sender;
- (void)reloadTerms:(id)sender;
- (void)openTerms:(id)sender;
- (void)testAI:(id)sender;
- (void)aiEnabledChanged:(id)sender;
- (void)aiConfigurationChanged:(NSNotification *)notification;
- (void)runAIConnectionTest;
- (void)recordPlayground:(id)sender;
- (void)startPlaygroundRecording;
- (void)requestMicrophonePermission:(id)sender;
- (void)openMicrophonePrivacy:(id)sender;
- (void)ensureMicrophoneAccess:(void (^)(BOOL granted))completion;
- (void)playPlayground:(id)sender;
- (void)transcribePlayground:(id)sender;
- (void)polishPlayground:(id)sender;
- (void)applyEditExample:(id)sender;
- (void)runDoctor:(id)sender;
- (void)toggleDoctorRaw:(id)sender;
- (void)queryLatestRelease:(id)sender;
- (void)exportSupportBundle:(id)sender;
- (void)openSupportDirectory:(id)sender;
- (void)openGitHubIssue:(id)sender;
- (void)sendFeedback:(id)sender;
- (void)feedbackEndpointToggled:(id)sender;
- (void)openConfiguration:(id)sender;
- (void)quit:(id)sender;
@end

@implementation VocoTypeApplicationController {
  NSStatusItem *_statusItem;
  NSWindow *_settingsWindow;
  NSUInteger _settingsWindowPresentationCount;
  NSView *_pageContainer;
  NSMutableArray<NSView *> *_pages;
  NSInteger _selectedPage;
  NSMutableArray<NSButton *> *_sidebarButtons;
  NSTextField *_globalStatus;
  NSProgressIndicator *_globalProgress;

  NSTextField *_overviewStatus;
  NSTextField *_overviewPaletteStatus;
  NSTextField *_overviewModelStatus;
  NSTextField *_overviewDoctorStatus;

  NSPopUpButton *_audioInput;
  NSPopUpButton *_playgroundAudioInput;
  NSPopUpButton *_audioOutput;
  NSTextField *_audioRate;
  NSTextField *_minimumRecording;
  NSTextField *_audioStatus;
  NSButton *_streamingEnabled;
  NSButton *_normalizationEnabled;
  NSButton *_compactDates;
  NSButton *_compactTimes;
  NSButton *_compactDistances;
  NSButton *_currencySymbols;
  NSTextField *_normalizationInput;
  NSTextField *_normalizationOutput;
  std::vector<vocotype::desktop::AudioDevice> _inputDevices;
  std::vector<vocotype::desktop::AudioOutputDevice> _outputDevices;

  std::array<NSButton *, 3> _hotkeyButtons;
  id _hotkeyMonitor;
  NSInteger _capturingHotkey;
  NSString *_hotkeyBackup;

  NSTextField *_termsStatus;

  NSButton *_slmEnabled;
  NSTextField *_endpoint;
  NSTextField *_model;
  NSSecureTextField *_apiKey;
  NSTextField *_apiKeyEnvironment;
  NSButton *_clearApiKey;
  NSButton *_remoteStreaming;
  NSButton *_thinking;
  NSButton *_voiceEdit;
  NSTextField *_minimumCharacters;
  NSTextField *_timeoutMilliseconds;
  NSTextField *_aiStatus;
  NSUInteger _aiHealthGeneration;

  VocoTypeWaveformView *_waveform;
  NSTextField *_playgroundStatus;
  NSTextView *_transcriptionResult;
  NSTextView *_polishSource;
  NSTextView *_polishResult;
  NSTextField *_polishStatus;
  NSTextView *_editSource;
  NSTextField *_editStatus;
  NSArray<NSDictionary *> *_editExamples;
  std::filesystem::path _lastRecording;

  NSTextField *_versionStatus;
  NSTextField *_doctorSummary;
  NSStackView *_doctorChecks;
  NSView *_doctorRawContainer;
  NSButton *_doctorRawToggle;
  NSTextView *_doctorOutput;
  NSTextField *_supportStatus;
  std::string _lastDoctorReport;

  NSPopUpButton *_feedbackCategory;
  NSTextField *_feedbackContact;
  NSTextView *_feedbackMessage;
  NSButton *_feedbackIncludeDoctor;
  NSButton *_feedbackIncludeBundle;
  NSButton *_feedbackCustomEndpoint;
  NSTextField *_feedbackEndpoint;
  NSTextField *_feedbackStatus;

  Json _config;
}

- (void)installStandardMainMenu {
  NSMenu *mainMenu = [[NSMenu alloc] initWithTitle:@""];

  NSMenuItem *(^command)(NSString *, SEL, NSString *) =
      ^NSMenuItem *(NSString *title, SEL action, NSString *key) {
    NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:title
                                                  action:action
                                           keyEquivalent:key];
    item.target = nil;
    return item;
  };

  NSMenuItem *applicationItem =
      [[NSMenuItem alloc] initWithTitle:@"" action:nil keyEquivalent:@""];
  NSMenu *applicationMenu =
      [[NSMenu alloc] initWithTitle:@"VoCoType-linux"];
  NSMenuItem *about = [[NSMenuItem alloc]
      initWithTitle:@"关于 VoCoType-linux"
             action:@selector(orderFrontStandardAboutPanel:)
      keyEquivalent:@""];
  about.target = NSApp;
  [applicationMenu addItem:about];
  NSMenuItem *preferences = [[NSMenuItem alloc]
      initWithTitle:@"设置…"
             action:@selector(showSettings:)
      keyEquivalent:@","];
  preferences.target = self;
  [applicationMenu addItem:preferences];
  [applicationMenu addItem:NSMenuItem.separatorItem];
  NSMenuItem *hide = [[NSMenuItem alloc]
      initWithTitle:@"隐藏 VoCoType-linux"
             action:@selector(hide:)
      keyEquivalent:@"h"];
  hide.target = NSApp;
  [applicationMenu addItem:hide];
  NSMenuItem *hideOthers = [[NSMenuItem alloc]
      initWithTitle:@"隐藏其他应用"
             action:@selector(hideOtherApplications:)
      keyEquivalent:@"h"];
  hideOthers.keyEquivalentModifierMask =
      NSEventModifierFlagCommand | NSEventModifierFlagOption;
  hideOthers.target = NSApp;
  [applicationMenu addItem:hideOthers];
  NSMenuItem *showAll = [[NSMenuItem alloc]
      initWithTitle:@"显示全部"
             action:@selector(unhideAllApplications:)
      keyEquivalent:@""];
  showAll.target = NSApp;
  [applicationMenu addItem:showAll];
  [applicationMenu addItem:NSMenuItem.separatorItem];
  NSMenuItem *quit = [[NSMenuItem alloc]
      initWithTitle:@"退出 VoCoType-linux"
             action:@selector(terminate:)
      keyEquivalent:@"q"];
  quit.target = NSApp;
  [applicationMenu addItem:quit];
  applicationItem.submenu = applicationMenu;
  [mainMenu addItem:applicationItem];

  NSMenuItem *fileItem =
      [[NSMenuItem alloc] initWithTitle:@"文件" action:nil keyEquivalent:@""];
  NSMenu *fileMenu = [[NSMenu alloc] initWithTitle:@"文件"];
  NSMenuItem *save = [[NSMenuItem alloc] initWithTitle:@"保存设置"
                                                 action:@selector(saveSettings:)
                                          keyEquivalent:@"s"];
  save.target = self;
  [fileMenu addItem:save];
  [fileMenu addItem:NSMenuItem.separatorItem];
  NSMenuItem *closeWindow =
      command(@"关闭窗口", @selector(closeSettingsWindow:), @"w");
  closeWindow.target = self;
  [fileMenu addItem:closeWindow];
  fileItem.submenu = fileMenu;
  [mainMenu addItem:fileItem];

  NSMenuItem *editItem =
      [[NSMenuItem alloc] initWithTitle:@"编辑" action:nil keyEquivalent:@""];
  NSMenu *editMenu = [[NSMenu alloc] initWithTitle:@"编辑"];
  [editMenu addItem:command(@"撤销", @selector(undo:), @"z")];
  NSMenuItem *redo = command(@"重做", @selector(redo:), @"z");
  redo.keyEquivalentModifierMask =
      NSEventModifierFlagCommand | NSEventModifierFlagShift;
  [editMenu addItem:redo];
  NSMenuItem *controlUndo =
      command(@"撤销（Ctrl+Z）", @selector(performControlUndo:), @"z");
  controlUndo.target = self;
  controlUndo.keyEquivalentModifierMask = NSEventModifierFlagControl;
  controlUndo.hidden = YES;
  controlUndo.allowsKeyEquivalentWhenHidden = YES;
  [editMenu addItem:controlUndo];
  NSMenuItem *controlRedo =
      command(@"重做（Ctrl+Shift+Z）", @selector(performControlRedo:), @"z");
  controlRedo.target = self;
  controlRedo.keyEquivalentModifierMask =
      NSEventModifierFlagControl | NSEventModifierFlagShift;
  controlRedo.hidden = YES;
  controlRedo.allowsKeyEquivalentWhenHidden = YES;
  [editMenu addItem:controlRedo];
  [editMenu addItem:NSMenuItem.separatorItem];
  [editMenu addItem:command(@"剪切", @selector(cut:), @"x")];
  [editMenu addItem:command(@"复制", @selector(copy:), @"c")];
  [editMenu addItem:command(@"粘贴", @selector(paste:), @"v")];
  [editMenu addItem:command(@"删除", @selector(delete:), @"")];
  [editMenu addItem:NSMenuItem.separatorItem];
  [editMenu addItem:command(@"全选", @selector(selectAll:), @"a")];
  editItem.submenu = editMenu;
  [mainMenu addItem:editItem];

  NSMenuItem *viewItem =
      [[NSMenuItem alloc] initWithTitle:@"显示" action:nil keyEquivalent:@""];
  NSMenu *viewMenu = [[NSMenu alloc] initWithTitle:@"显示"];
  NSArray<NSString *> *pageNames = @[
    @"概览", @"通用", @"Playground", @"用户词典",
    @"AI 功能", @"诊断", @"使用指南", @"反馈"
  ];
  for (NSInteger index = 0; index < static_cast<NSInteger>(pageNames.count); ++index) {
    NSMenuItem *page = [[NSMenuItem alloc]
        initWithTitle:pageNames[static_cast<NSUInteger>(index)]
               action:@selector(selectPageFromSender:)
        keyEquivalent:[NSString stringWithFormat:@"%ld", index + 1]];
    page.target = self;
    page.tag = index;
    [viewMenu addItem:page];
  }
  [viewMenu addItem:NSMenuItem.separatorItem];
  NSMenuItem *fullScreen = command(@"进入全屏幕", @selector(toggleFullScreen:), @"f");
  fullScreen.keyEquivalentModifierMask =
      NSEventModifierFlagCommand | NSEventModifierFlagControl;
  [viewMenu addItem:fullScreen];
  viewItem.submenu = viewMenu;
  [mainMenu addItem:viewItem];

  NSMenuItem *windowItem =
      [[NSMenuItem alloc] initWithTitle:@"窗口" action:nil keyEquivalent:@""];
  NSMenu *windowMenu = [[NSMenu alloc] initWithTitle:@"窗口"];
  [windowMenu addItem:command(@"最小化", @selector(performMiniaturize:), @"m")];
  [windowMenu addItem:command(@"缩放", @selector(performZoom:), @"")];
  [windowMenu addItem:NSMenuItem.separatorItem];
  NSMenuItem *front = [[NSMenuItem alloc] initWithTitle:@"前置全部窗口"
                                                 action:@selector(arrangeInFront:)
                                          keyEquivalent:@""];
  front.target = NSApp;
  [windowMenu addItem:front];
  windowItem.submenu = windowMenu;
  [mainMenu addItem:windowItem];
  NSApp.windowsMenu = windowMenu;

  NSMenuItem *helpItem =
      [[NSMenuItem alloc] initWithTitle:@"帮助" action:nil keyEquivalent:@""];
  NSMenu *helpMenu = [[NSMenu alloc] initWithTitle:@"帮助"];
  NSMenuItem *guide = [[NSMenuItem alloc] initWithTitle:@"VoCoType 使用指南"
                                                 action:@selector(selectPageFromSender:)
                                          keyEquivalent:@"?"];
  guide.target = self;
  guide.tag = 6;
  [helpMenu addItem:guide];
  NSMenuItem *issue = [[NSMenuItem alloc] initWithTitle:@"报告问题…"
                                                 action:@selector(openGitHubIssue:)
                                          keyEquivalent:@""];
  issue.target = self;
  [helpMenu addItem:issue];
  helpItem.submenu = helpMenu;
  [mainMenu addItem:helpItem];
  NSApp.helpMenu = helpMenu;

  NSApp.mainMenu = mainMenu;
}

- (void)closeSettingsWindow:(id)sender {
  [_settingsWindow performClose:sender];
}

- (void)performControlUndo:(id)sender {
  (void)sender;
  NSWindow *window = _settingsWindow ? _settingsWindow : NSApp.keyWindow;
  NSResponder *responder = window.firstResponder;
  NSUndoManager *manager = responder.undoManager;
  if (manager.canUndo)
    [manager undo];
}

- (void)performControlRedo:(id)sender {
  (void)sender;
  NSWindow *window = _settingsWindow ? _settingsWindow : NSApp.keyWindow;
  NSResponder *responder = window.firstResponder;
  NSUndoManager *manager = responder.undoManager;
  if (manager.canRedo)
    [manager redo];
}

- (void)install {
  [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
  [self installStandardMainMenu];
  _sidebarButtons = [NSMutableArray array];
  _pages = [NSMutableArray array];
  _selectedPage = -1;
  _capturingHotkey = -1;

  _statusItem = [NSStatusBar.systemStatusBar
      statusItemWithLength:NSSquareStatusItemLength];
  NSImage *image = [NSImage imageWithSystemSymbolName:@"waveform.circle.fill"
                            accessibilityDescription:@"VoCoType-linux"];
  [image setTemplate:YES];
  _statusItem.button.image = image;
  _statusItem.button.toolTip = @"VoCoType-linux";

  NSMenu *menu = [[NSMenu alloc] initWithTitle:@"VoCoType-linux"];
  NSArray<NSArray *> *items = @[
    @[ @"设置…", @(kGeneralPage), @"," ],
    @[ @"Playground…", @(kPlaygroundPage), @"" ],
    @[ @"用户词典…", @(kTermsPage), @"" ],
    @[ @"诊断…", @(kDoctorPage), @"" ],
  ];
  for (NSArray *entry in items) {
    NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:entry[0]
                                                  action:@selector(selectPageFromSender:)
                                           keyEquivalent:entry[2]];
    item.target = self;
    item.tag = [entry[1] integerValue];
    [menu addItem:item];
  }
  [menu addItem:NSMenuItem.separatorItem];
  NSMenuItem *models = [[NSMenuItem alloc]
      initWithTitle:@"校验并下载模型…"
             action:@selector(downloadModels:)
      keyEquivalent:@""];
  models.target = self;
  [menu addItem:models];
  NSMenuItem *config = [[NSMenuItem alloc]
      initWithTitle:@"打开配置目录"
             action:@selector(openConfiguration:)
      keyEquivalent:@""];
  config.target = self;
  [menu addItem:config];
  [menu addItem:NSMenuItem.separatorItem];
  NSMenuItem *quit = [[NSMenuItem alloc] initWithTitle:@"退出 VoCoType-linux"
                                                 action:@selector(quit:)
                                          keyEquivalent:@"q"];
  quit.target = self;
  [menu addItem:quit];
  _statusItem.menu = menu;
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
  (void)sender;
  return NO;
}

- (BOOL)applicationShouldHandleReopen:(NSApplication *)sender
                    hasVisibleWindows:(BOOL)hasVisibleWindows {
  (void)sender;
  if (!hasVisibleWindows)
    [self showSettingsWindow];
  return YES;
}

- (void)applicationWillTerminate:(NSNotification *)notification {
  (void)notification;
  if (_hotkeyMonitor)
    [NSEvent removeMonitor:_hotkeyMonitor];
  [NSNotificationCenter.defaultCenter removeObserver:self];
  if (!_lastRecording.empty())
    std::filesystem::remove(_lastRecording);
}

- (NSScrollView *)pageWithTitle:(NSString *)title
                       subtitle:(NSString *)subtitle
                          stack:(NSStackView **)stackOut {
  NSScrollView *scroll = [[NSScrollView alloc] initWithFrame:NSZeroRect];
  scroll.hasVerticalScroller = YES;
  scroll.autohidesScrollers = YES;
  scroll.drawsBackground = YES;
  scroll.backgroundColor = page_background_color();
  VocoTypeFlippedView *document =
      [[VocoTypeFlippedView alloc] initWithFrame:NSZeroRect];
  document.translatesAutoresizingMaskIntoConstraints = NO;
  document.wantsLayer = YES;
  document.layer.backgroundColor = page_background_color().CGColor;
  NSStackView *stack = vertical_stack(20.0);
  stack.alignment = NSLayoutAttributeWidth;
  [document addSubview:stack];
  scroll.documentView = document;

  NSView *pageHeader = [[NSView alloc] initWithFrame:NSZeroRect];
  pageHeader.translatesAutoresizingMaskIntoConstraints = NO;
  NSTextField *pageTitle = title_label(title, 25.0);
  pageTitle.translatesAutoresizingMaskIntoConstraints = NO;
  [pageHeader addSubview:pageTitle];
  NSMutableArray<NSLayoutConstraint *> *headerConstraints =
      [NSMutableArray arrayWithArray:@[
        [pageTitle.leadingAnchor constraintEqualToAnchor:pageHeader.leadingAnchor],
        [pageTitle.trailingAnchor constraintLessThanOrEqualToAnchor:pageHeader.trailingAnchor],
        [pageTitle.topAnchor constraintEqualToAnchor:pageHeader.topAnchor],
      ]];
  if (subtitle.length > 0) {
    NSTextField *pageSubtitle = status_label(subtitle);
    pageSubtitle.translatesAutoresizingMaskIntoConstraints = NO;
    pageSubtitle.font = [NSFont systemFontOfSize:12.5];
    pageSubtitle.textColor = NSColor.secondaryLabelColor;
    [pageHeader addSubview:pageSubtitle];
    [headerConstraints addObjectsFromArray:@[
      [pageSubtitle.leadingAnchor constraintEqualToAnchor:pageHeader.leadingAnchor],
      [pageSubtitle.trailingAnchor constraintLessThanOrEqualToAnchor:pageHeader.trailingAnchor],
      [pageSubtitle.topAnchor constraintEqualToAnchor:pageTitle.bottomAnchor constant:5.0],
      [pageSubtitle.bottomAnchor constraintEqualToAnchor:pageHeader.bottomAnchor],
      [pageSubtitle.widthAnchor constraintLessThanOrEqualToConstant:720.0],
    ]];
  } else {
    [headerConstraints addObject:
        [pageTitle.bottomAnchor constraintEqualToAnchor:pageHeader.bottomAnchor]];
  }
  [NSLayoutConstraint activateConstraints:headerConstraints];
  [stack addArrangedSubview:pageHeader];

  NSLayoutConstraint *preferredWidth =
      [stack.widthAnchor constraintEqualToConstant:760.0];
  preferredWidth.priority = NSLayoutPriorityDefaultHigh;
  [NSLayoutConstraint activateConstraints:@[
    [document.widthAnchor constraintEqualToAnchor:scroll.contentView.widthAnchor],
    [stack.centerXAnchor constraintEqualToAnchor:document.centerXAnchor],
    [stack.leadingAnchor constraintGreaterThanOrEqualToAnchor:document.leadingAnchor
                                                       constant:32.0],
    [stack.trailingAnchor constraintLessThanOrEqualToAnchor:document.trailingAnchor
                                                    constant:-32.0],
    [stack.topAnchor constraintEqualToAnchor:document.topAnchor constant:30.0],
    [stack.bottomAnchor constraintEqualToAnchor:document.bottomAnchor constant:-40.0],
    preferredWidth,
  ]];
  if (stackOut)
    *stackOut = stack;
  return scroll;
}

- (void)addPage:(NSView *)view identifier:(NSString *)identifier {
  view.identifier = identifier;
  view.frame = _pageContainer.bounds;
  view.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
  view.hidden = YES;
  [_pageContainer addSubview:view];
  [_pages addObject:view];
}

- (NSWindow *)buildSettingsWindow {
  NSWindow *window = [[NSWindow alloc]
      initWithContentRect:NSMakeRect(0, 0, 1040, 720)
                styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                          NSWindowStyleMaskMiniaturizable |
                          NSWindowStyleMaskResizable |
                          NSWindowStyleMaskFullSizeContentView
                  backing:NSBackingStoreBuffered
                    defer:NO];
  window.title = @"VoCoType-linux";
  window.titleVisibility = NSWindowTitleHidden;
  window.titlebarAppearsTransparent = YES;
  window.movableByWindowBackground = YES;
  window.releasedWhenClosed = NO;
  window.minSize = NSMakeSize(900, 620);

  NSView *container = [[NSView alloc] initWithFrame:NSZeroRect];
  container.wantsLayer = YES;
  container.layer.backgroundColor = page_background_color().CGColor;
  window.contentView = container;

  NSStackView *root = vertical_stack(0.0);
  root.alignment = NSLayoutAttributeWidth;
  [container addSubview:root];
  [NSLayoutConstraint activateConstraints:@[
    [root.leadingAnchor constraintEqualToAnchor:container.leadingAnchor],
    [root.trailingAnchor constraintEqualToAnchor:container.trailingAnchor],
    [root.topAnchor constraintEqualToAnchor:container.topAnchor],
    [root.bottomAnchor constraintEqualToAnchor:container.bottomAnchor],
  ]];

  NSVisualEffectView *header = [[NSVisualEffectView alloc] initWithFrame:NSZeroRect];
  header.material = NSVisualEffectMaterialHeaderView;
  header.blendingMode = NSVisualEffectBlendingModeWithinWindow;
  header.state = NSVisualEffectStateFollowsWindowActiveState;
  header.translatesAutoresizingMaskIntoConstraints = NO;
  NSStackView *headerRow = horizontal_stack(10.0);
  headerRow.translatesAutoresizingMaskIntoConstraints = NO;
  [header addSubview:headerRow];

  NSImageView *brandIcon = [[NSImageView alloc] initWithFrame:NSZeroRect];
  NSImage *brandImage = [NSImage imageWithSystemSymbolName:@"waveform.circle.fill"
                                  accessibilityDescription:@"VoCoType-linux"];
  brandImage = [brandImage imageWithSymbolConfiguration:
      [NSImageSymbolConfiguration configurationWithPointSize:20.0
                                                       weight:NSFontWeightMedium]];
  brandIcon.image = brandImage;
  brandIcon.contentTintColor = NSColor.controlAccentColor;
  [brandIcon.widthAnchor constraintEqualToConstant:25.0].active = YES;
  [brandIcon.heightAnchor constraintEqualToConstant:25.0].active = YES;
  [headerRow addArrangedSubview:brandIcon];

  NSStackView *brand = vertical_stack(0.0);
  NSTextField *brandTitle = title_label(@"VoCoType-linux", 13.5);
  [brand addArrangedSubview:brandTitle];
  NSTextField *brandSubtitle = status_label(
      [NSString stringWithFormat:@"原生语音输入 · %@", app_version()]);
  brandSubtitle.font = [NSFont systemFontOfSize:10.5];
  [brand addArrangedSubview:brandSubtitle];
  [headerRow addArrangedSubview:brand];
  [headerRow addArrangedSubview:flexible_spacer()];

  _globalProgress = [[NSProgressIndicator alloc] initWithFrame:NSZeroRect];
  _globalProgress.style = NSProgressIndicatorStyleSpinning;
  _globalProgress.displayedWhenStopped = NO;
  _globalProgress.controlSize = NSControlSizeSmall;
  [headerRow addArrangedSubview:_globalProgress];
  _globalStatus = status_label(@"");
  _globalStatus.alignment = NSTextAlignmentRight;
  [_globalStatus.widthAnchor constraintLessThanOrEqualToConstant:300.0].active = YES;
  [headerRow addArrangedSubview:_globalStatus];
  NSButton *save = primary_button(@"保存", self, @selector(saveSettings:));
  [headerRow addArrangedSubview:save];
  [NSLayoutConstraint activateConstraints:@[
    [header.heightAnchor constraintEqualToConstant:58.0],
    [headerRow.leadingAnchor constraintEqualToAnchor:header.leadingAnchor constant:76.0],
    [headerRow.trailingAnchor constraintEqualToAnchor:header.trailingAnchor constant:-16.0],
    [headerRow.centerYAnchor constraintEqualToAnchor:header.centerYAnchor constant:4.0],
  ]];
  [root addArrangedSubview:header];

  NSView *body = [[NSView alloc] initWithFrame:NSZeroRect];
  body.translatesAutoresizingMaskIntoConstraints = NO;

  NSVisualEffectView *sidebar = [[NSVisualEffectView alloc] initWithFrame:NSZeroRect];
  sidebar.material = NSVisualEffectMaterialSidebar;
  sidebar.blendingMode = NSVisualEffectBlendingModeWithinWindow;
  sidebar.state = NSVisualEffectStateFollowsWindowActiveState;
  sidebar.translatesAutoresizingMaskIntoConstraints = NO;
  [sidebar.widthAnchor constraintEqualToConstant:194.0].active = YES;
  [sidebar setContentHuggingPriority:NSLayoutPriorityRequired
                     forOrientation:NSLayoutConstraintOrientationHorizontal];
  [sidebar setContentCompressionResistancePriority:NSLayoutPriorityRequired
                                    forOrientation:NSLayoutConstraintOrientationHorizontal];

  NSStackView *sidebarStack = vertical_stack(5.0);
  sidebarStack.alignment = NSLayoutAttributeWidth;
  [sidebar addSubview:sidebarStack];
  [NSLayoutConstraint activateConstraints:@[
    [sidebarStack.leadingAnchor constraintEqualToAnchor:sidebar.leadingAnchor constant:12.0],
    [sidebarStack.trailingAnchor constraintEqualToAnchor:sidebar.trailingAnchor constant:-12.0],
    [sidebarStack.topAnchor constraintEqualToAnchor:sidebar.topAnchor constant:16.0],
  ]];

  NSArray<NSString *> *pageNames = @[
    @"概览", @"通用", @"Playground", @"用户词典",
    @"AI 功能", @"诊断", @"使用指南", @"反馈"
  ];
  NSArray<NSString *> *symbols = @[
    @"house", @"slider.horizontal.3", @"waveform", @"text.book.closed",
    @"sparkles", @"stethoscope", @"book", @"bubble.left.and.bubble.right"
  ];
  for (NSInteger index = 0; index < static_cast<NSInteger>(pageNames.count); ++index) {
    if (index == 0 || index == 5) {
      if (index == 5) {
        NSView *gap = [[NSView alloc] initWithFrame:NSZeroRect];
        [gap.heightAnchor constraintEqualToConstant:10.0].active = YES;
        [sidebarStack addArrangedSubview:gap];
      }
      NSTextField *group = plain_label(index == 0 ? @"功能" : @"支持");
      group.font = [NSFont systemFontOfSize:10.5 weight:NSFontWeightSemibold];
      group.textColor = NSColor.tertiaryLabelColor;
      [sidebarStack addArrangedSubview:group];
    }
    VocoTypeSidebarButton *button =
        [VocoTypeSidebarButton buttonWithTitle:pageNames[index]
                                        symbol:symbols[index]
                                        target:self
                                        action:@selector(selectPageFromSender:)];
    button.tag = index;
    [sidebarStack addArrangedSubview:button];
    [_sidebarButtons addObject:button];
  }

  _pageContainer = [[NSView alloc] initWithFrame:NSZeroRect];
  _pageContainer.translatesAutoresizingMaskIntoConstraints = NO;
  _pageContainer.wantsLayer = YES;
  _pageContainer.layer.backgroundColor = page_background_color().CGColor;
  [_pageContainer setContentHuggingPriority:NSLayoutPriorityDefaultLow
                             forOrientation:NSLayoutConstraintOrientationHorizontal];
  [_pageContainer setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow
                                            forOrientation:NSLayoutConstraintOrientationHorizontal];

  NSView *separator = [[NSView alloc] initWithFrame:NSZeroRect];
  separator.translatesAutoresizingMaskIntoConstraints = NO;
  separator.wantsLayer = YES;
  separator.layer.backgroundColor = NSColor.separatorColor.CGColor;
  [separator.widthAnchor constraintEqualToConstant:1.0].active = YES;

  [body addSubview:sidebar];
  [body addSubview:separator];
  [body addSubview:_pageContainer];
  [NSLayoutConstraint activateConstraints:@[
    [sidebar.leadingAnchor constraintEqualToAnchor:body.leadingAnchor],
    [sidebar.topAnchor constraintEqualToAnchor:body.topAnchor],
    [sidebar.bottomAnchor constraintEqualToAnchor:body.bottomAnchor],
    [separator.leadingAnchor constraintEqualToAnchor:sidebar.trailingAnchor],
    [separator.topAnchor constraintEqualToAnchor:body.topAnchor],
    [separator.bottomAnchor constraintEqualToAnchor:body.bottomAnchor],
    [_pageContainer.leadingAnchor constraintEqualToAnchor:separator.trailingAnchor],
    [_pageContainer.trailingAnchor constraintEqualToAnchor:body.trailingAnchor],
    [_pageContainer.topAnchor constraintEqualToAnchor:body.topAnchor],
    [_pageContainer.bottomAnchor constraintEqualToAnchor:body.bottomAnchor],
    [body.heightAnchor constraintGreaterThanOrEqualToConstant:540.0],
  ]];
  [root addArrangedSubview:body];

  [self buildOverviewPage];
  [self buildGeneralPage];
  [self buildPlaygroundPage];
  [self buildTermsPage];
  [self buildAIPage];
  [self buildDoctorPage];
  [self buildTutorialPage];
  [self buildFeedbackPage];
  [self selectPage:kOverviewPage];
  return window;
}

- (void)buildOverviewPage {
  NSStackView *page = nil;
  NSScrollView *scroll = [self pageWithTitle:@"概览与安装"
                                    subtitle:@"检查 Palette Input Method、原生 Core、模型与运行环境。配置、术语和模型缓存会被保留。"
                                       stack:&page];
  NSStackView *card = nil;
  [page addArrangedSubview:card_with_stack(&card)];
  _overviewStatus = status_label(@"尚未检查");
  [card addArrangedSubview:settings_row(@"安装环境", @"显示版本、资源、Core 与音频设备状态。", _overviewStatus)];
  NSStackView *paletteActions = horizontal_stack(8.0);
  NSButton *activate = action_button(@"重新激活", self, @selector(activatePalette:));
  [paletteActions addArrangedSubview:activate];
  _overviewPaletteStatus = status_label(@"尚未检查");
  [paletteActions addArrangedSubview:_overviewPaletteStatus];
  [card addArrangedSubview:settings_row(@"macOS Palette Input Method",
                                        @"与当前双拼、拼音或鼠须管同时工作，不替换键盘输入法。",
                                        paletteActions)];
  NSStackView *coreActions = horizontal_stack(8.0);
  [coreActions addArrangedSubview:action_button(@"重启 Core", self,
                                                @selector(restartCore:))];
  [coreActions addArrangedSubview:action_button(@"刷新状态", self,
                                                @selector(refreshOverview:))];
  [card addArrangedSubview:settings_row(@"原生后台", @"按需启动 C++ Core 与原生 FunASR worker。",
                                        coreActions)];

  NSStackView *modelCard = nil;
  [page addArrangedSubview:card_with_stack(&modelCard)];
  NSStackView *modelActions = horizontal_stack(8.0);
  [modelActions addArrangedSubview:action_button(@"校验并下载模型", self,
                                                 @selector(downloadModels:))];
  _overviewModelStatus = status_label(@"尚未校验");
  [modelActions addArrangedSubview:_overviewModelStatus];
  [modelCard addArrangedSubview:settings_row(@"ASR 模型", @"校验离线、流式、VAD 与标点模型的冻结 SHA-256。",
                                             modelActions)];

  NSStackView *doctorCard = nil;
  [page addArrangedSubview:card_with_stack(&doctorCard)];
  NSStackView *doctorActions = horizontal_stack(8.0);
  [doctorActions addArrangedSubview:action_button(@"运行快速检查", self,
                                                  @selector(runDoctor:))];
  NSButton *details = action_button(@"查看详情", self,
                                    @selector(selectPageFromSender:));
  details.tag = kDoctorPage;
  [doctorActions addArrangedSubview:details];
  _overviewDoctorStatus = status_label(@"尚未运行");
  [doctorActions addArrangedSubview:_overviewDoctorStatus];
  [doctorCard addArrangedSubview:settings_row(@"运行状态", @"详细结果与支持包位于“诊断”页。",
                                              doctorActions)];
  stretch_page_contents(page);
  [self addPage:scroll identifier:@"overview"];
}

- (void)buildGeneralPage {
  NSStackView *page = nil;
  NSScrollView *scroll = [self pageWithTitle:@"通用设置"
                                    subtitle:@"集中配置麦克风、采样率、语音快捷键、实时预览与文本规范化。"
                                       stack:&page];
  [page addArrangedSubview:section_heading(@"音频输入", @"这组设置同时用于 F9、Playground 和语音编辑。")];
  NSStackView *audioCard = nil;
  [page addArrangedSubview:card_with_stack(&audioCard)];
  _audioInput = [[NSPopUpButton alloc] initWithFrame:NSZeroRect pullsDown:NO];
  _audioInput.target = self;
  _audioInput.action = @selector(audioSelectionChanged:);
  NSStackView *inputActions = horizontal_stack(8.0);
  [inputActions addArrangedSubview:_audioInput];
  [inputActions addArrangedSubview:action_button(@"刷新", self,
                                                 @selector(refreshAudioDevices:))];
  [audioCard addArrangedSubview:settings_row(@"输入设备", @"成功保存后，F9 将使用这里选择的麦克风。",
                                             inputActions)];
  _audioRate = text_field(@"44100");
  _audioRate.formatter = [[NSNumberFormatter alloc] init];
  [audioCard addArrangedSubview:settings_row(@"采样率", @"按设备支持的采样率采集；ASR 内部重采样至 16 kHz。",
                                             _audioRate)];
  _minimumRecording = text_field(@"500");
  _minimumRecording.formatter = [[NSNumberFormatter alloc] init];
  [audioCard addArrangedSubview:settings_row(@"最短录音时长（毫秒）", @"不足此时长的快捷键录音直接丢弃。",
                                             _minimumRecording)];
  _audioStatus = status_label(@"尚未枚举音频设备");
  [audioCard addArrangedSubview:settings_row(@"设备状态", @"", _audioStatus)];

  [page addArrangedSubview:section_heading(@"语音快捷键", @"点击按钮后直接按新的 F1–F20 组合；Esc 取消。")];
  NSStackView *hotkeyCard = nil;
  [page addArrangedSubview:card_with_stack(&hotkeyCard)];
  NSArray<NSString *> *titles = @[ @"普通识别", @"AI 润色", @"语音编辑" ];
  NSArray<NSString *> *hints = @[
    @"按住录音，松开后直接识别并提交。",
    @"按住录音，松开后识别并调用 AI 端点润色。",
    @"对当前文本上下文说编辑指令。"
  ];
  for (NSInteger index = 0; index < 3; ++index) {
    NSButton *button = action_button(index == 0 ? @"F9" :
                                     index == 1 ? @"Shift+F9" : @"Ctrl+F9",
                                     self, @selector(beginHotkeyCapture:));
    button.tag = index;
    _hotkeyButtons[static_cast<std::size_t>(index)] = button;
    [hotkeyCard addArrangedSubview:settings_row(titles[index], hints[index], button)];
  }

  [page addArrangedSubview:section_heading(@"实时识别预览（2-pass）",
                                            @"在线模型只负责录音期间的预览，松键后仍由高精度离线模型给出最终结果。")];
  NSStackView *streamCard = nil;
  [page addArrangedSubview:card_with_stack(&streamCard)];
  _streamingEnabled = switch_button();
  [streamCard addArrangedSubview:settings_row(@"启用实时预览", @"首次使用会按需加载在线模型。",
                                              _streamingEnabled)];

  [page addArrangedSubview:section_heading(@"文本规范化与 ITN",
                                            @"与 Linux 版使用同一 Core 规则；可单独控制日期、时间、距离和货币格式。")];
  NSStackView *normalizationCard = nil;
  [page addArrangedSubview:card_with_stack(&normalizationCard)];
  _normalizationEnabled = switch_button();
  _compactDates = switch_button();
  _compactTimes = switch_button();
  _compactDistances = switch_button();
  _currencySymbols = switch_button();
  [normalizationCard addArrangedSubview:settings_row(@"启用文本规范化", @"识别后执行术语替换和中文数字 ITN。", _normalizationEnabled)];
  [normalizationCard addArrangedSubview:settings_row(@"紧凑日期", @"例如 2026/07/27。", _compactDates)];
  [normalizationCard addArrangedSubview:settings_row(@"紧凑时间", @"例如 15:20。", _compactTimes)];
  [normalizationCard addArrangedSubview:settings_row(@"紧凑距离", @"例如 320m。", _compactDistances)];
  [normalizationCard addArrangedSubview:settings_row(@"货币符号", @"例如 ¥256。", _currencySymbols)];
  _normalizationInput = text_field(@"下午三点二十分跑了三百二十米，价格二百五十六元");
  NSStackView *preview = horizontal_stack(8.0);
  [preview addArrangedSubview:_normalizationInput];
  [preview addArrangedSubview:action_button(@"预览", self,
                                            @selector(previewNormalization:))];
  [normalizationCard addArrangedSubview:settings_row(@"测试文本", @"调用当前原生 Core 实时预览。", preview)];
  _normalizationOutput = status_label(@"预览结果会显示在这里。");
  [normalizationCard addArrangedSubview:settings_row(@"结果", @"", _normalizationOutput)];
  stretch_page_contents(page);
  [self addPage:scroll identifier:@"general"];
}

- (void)buildPlaygroundPage {
  NSStackView *page = nil;
  NSScrollView *scroll = [self pageWithTitle:@"Playground"
                                    subtitle:@"依次验证麦克风、真实转录、AI 润色与语音编辑；这里使用的设备和参数与 F9 完全一致。"
                                       stack:&page];

  [page addArrangedSubview:section_heading(
      @"1. 录音与波形",
      @"先确认输入设备与实时音量，再使用同一段录音测试回放和识别。")];
  NSStackView *audioCard = nil;
  [page addArrangedSubview:card_with_stack(&audioCard)];

  NSStackView *inputBlock = vertical_stack(5.0);
  inputBlock.alignment = NSLayoutAttributeWidth;
  [inputBlock addArrangedSubview:title_label(@"输入设备", 12.5)];
  NSTextField *inputHint = status_label(@"录音与 F9 将使用这里选择的麦克风");
  inputHint.textColor = NSColor.tertiaryLabelColor;
  [inputBlock addArrangedSubview:inputHint];
  _playgroundAudioInput =
      [[NSPopUpButton alloc] initWithFrame:NSZeroRect pullsDown:NO];
  _playgroundAudioInput.target = self;
  _playgroundAudioInput.action = @selector(audioSelectionChanged:);
  [_playgroundAudioInput.heightAnchor constraintEqualToConstant:30.0].active = YES;
  [inputBlock addArrangedSubview:_playgroundAudioInput];

  NSStackView *outputBlock = vertical_stack(5.0);
  outputBlock.alignment = NSLayoutAttributeWidth;
  [outputBlock addArrangedSubview:title_label(@"输出设备", 12.5)];
  NSTextField *outputHint = status_label(@"回放只发送到这里选择的扬声器或耳机");
  outputHint.textColor = NSColor.tertiaryLabelColor;
  [outputBlock addArrangedSubview:outputHint];
  _audioOutput = [[NSPopUpButton alloc] initWithFrame:NSZeroRect pullsDown:NO];
  [_audioOutput.heightAnchor constraintEqualToConstant:30.0].active = YES;
  [outputBlock addArrangedSubview:_audioOutput];
  [audioCard addArrangedSubview:equal_columns(inputBlock, outputBlock, 18.0)];
  [audioCard addArrangedSubview:separator_line()];

  NSStackView *audioActions = horizontal_stack(8.0);
  NSButton *recordButton = primary_button(@"录音 3 秒", self,
                                          @selector(recordPlayground:));
  recordButton.image = [NSImage imageWithSystemSymbolName:@"mic.fill"
                                  accessibilityDescription:@"录音"];
  recordButton.imagePosition = NSImageLeading;
  [audioActions addArrangedSubview:recordButton];
  NSButton *playButton = action_button(@"回放", self,
                                       @selector(playPlayground:));
  playButton.image = [NSImage imageWithSystemSymbolName:@"play.fill"
                                accessibilityDescription:@"回放"];
  playButton.imagePosition = NSImageLeading;
  [audioActions addArrangedSubview:playButton];
  NSButton *refreshButton = action_button(@"刷新设备", self,
                                          @selector(refreshAudioDevices:));
  refreshButton.image = [NSImage imageWithSystemSymbolName:@"arrow.clockwise"
                                   accessibilityDescription:@"刷新设备"];
  refreshButton.imagePosition = NSImageLeading;
  [audioActions addArrangedSubview:refreshButton];
  NSButton *permissionButton = action_button(@"麦克风权限", self,
                                             @selector(requestMicrophonePermission:));
  permissionButton.image = [NSImage imageWithSystemSymbolName:@"lock.open"
                                      accessibilityDescription:@"麦克风权限"];
  permissionButton.imagePosition = NSImageLeading;
  [audioActions addArrangedSubview:permissionButton];
  [audioActions addArrangedSubview:flexible_spacer()];
  _playgroundStatus = status_label(@"准备就绪");
  _playgroundStatus.alignment = NSTextAlignmentRight;
  _playgroundStatus.maximumNumberOfLines = 1;
  _playgroundStatus.lineBreakMode = NSLineBreakByTruncatingTail;
  [_playgroundStatus.widthAnchor constraintLessThanOrEqualToConstant:210.0].active = YES;
  [audioActions addArrangedSubview:_playgroundStatus];
  [audioCard addArrangedSubview:audioActions];

  _waveform = [[VocoTypeWaveformView alloc] initWithFrame:NSZeroRect];
  [_waveform.heightAnchor constraintEqualToConstant:82.0].active = YES;
  [audioCard addArrangedSubview:labeled_content_block(
      @"实时波形",
      @"按最近音量自动缩放；静音时保持噪声底，不会把底噪无限放大。",
      _waveform)];

  [page addArrangedSubview:section_heading(
      @"2. 真实转录",
      @"使用当前安装的离线 ASR 处理上一次录音；结果可以直接编辑和对照。")];
  NSStackView *asrCard = nil;
  [page addArrangedSubview:card_with_stack(&asrCard)];
  NSStackView *asrHeader = horizontal_stack(12.0);
  NSStackView *asrDescription = vertical_stack(2.0);
  [asrDescription addArrangedSubview:title_label(@"转录结果", 13.0)];
  NSTextField *asrHint = status_label(@"先完成一段录音，再运行真实识别；最终结果不会被流式预览替代。 ");
  asrHint.textColor = NSColor.tertiaryLabelColor;
  [asrDescription addArrangedSubview:asrHint];
  [asrHeader addArrangedSubview:asrDescription];
  [asrHeader addArrangedSubview:flexible_spacer()];
  NSButton *transcribeButton = action_button(@"转录上次录音", self,
                                             @selector(transcribePlayground:));
  transcribeButton.image = [NSImage imageWithSystemSymbolName:@"text.bubble"
                                      accessibilityDescription:@"转录"];
  transcribeButton.imagePosition = NSImageLeading;
  [asrHeader addArrangedSubview:transcribeButton];
  [asrCard addArrangedSubview:asrHeader];
  NSTextView *transcriptionResult = nil;
  [asrCard addArrangedSubview:text_editor(&transcriptionResult, 132.0, true)];
  _transcriptionResult = transcriptionResult;
  set_text(_transcriptionResult, "转录结果会显示在这里。可以直接修改文本，与实际口述逐句对照。");

  [page addArrangedSubview:section_heading(
      @"3. AI 润色",
      @"左右对照原文与结果；测试使用“AI 功能”页当前保存的端点、模型与凭据。")];
  NSStackView *polishCard = nil;
  [page addArrangedSubview:card_with_stack(&polishCard)];
  NSStackView *polishHeader = horizontal_stack(10.0);
  NSStackView *polishDescription = vertical_stack(2.0);
  [polishDescription addArrangedSubview:title_label(@"文本对照", 13.0)];
  NSTextField *polishHint = status_label(@"结果不会覆盖原文，可以反复修改后重新运行。");
  polishHint.textColor = NSColor.tertiaryLabelColor;
  [polishDescription addArrangedSubview:polishHint];
  [polishHeader addArrangedSubview:polishDescription];
  [polishHeader addArrangedSubview:flexible_spacer()];
  NSButton *polishButton = action_button(@"运行 AI 润色", self,
                                         @selector(polishPlayground:));
  polishButton.bezelColor = NSColor.controlAccentColor;
  polishButton.image = [NSImage imageWithSystemSymbolName:@"sparkles"
                                  accessibilityDescription:@"AI 润色"];
  polishButton.imagePosition = NSImageLeading;
  [polishHeader addArrangedSubview:polishButton];
  [polishCard addArrangedSubview:polishHeader];

  NSTextView *polishSource = nil;
  NSScrollView *polishSourceEditor = text_editor(&polishSource, 165.0, true);
  _polishSource = polishSource;
  set_text(_polishSource, "这是一段有一点啰嗦而且表达不够自然的测试文本，希望 AI 帮我整理得更清楚。");
  NSTextView *polishResult = nil;
  NSScrollView *polishResultEditor = text_editor(&polishResult, 165.0, true);
  _polishResult = polishResult;
  set_text(_polishResult, "AI 润色结果会显示在这里。");
  [polishCard addArrangedSubview:equal_columns(
      editor_column(@"原文", @"可直接编辑或粘贴一段文本。", polishSourceEditor),
      editor_column(@"润色结果", @"保留在右侧，便于逐句比较。", polishResultEditor),
      16.0)];
  _polishStatus = status_label(@"尚未运行");
  [polishCard addArrangedSubview:_polishStatus];

  [page addArrangedSubview:section_heading(
      @"4. 语音编辑练习",
      @"点击模板载入修改前文本，然后把光标放进下方文本框，直接按住 Ctrl+F9 口述编辑指令。")];
  NSStackView *editCard = nil;
  [page addArrangedSubview:card_with_stack(&editCard)];

  NSStackView *editHeader = horizontal_stack(10.0);
  NSStackView *editDescription = vertical_stack(2.0);
  [editDescription addArrangedSubview:title_label(@"原地编辑工作台", 13.0)];
  NSTextField *editHint = status_label(
      @"这里不再录制一段模拟指令；它直接使用真实 Ctrl+F9 全局语音编辑链路。");
  editHint.textColor = NSColor.tertiaryLabelColor;
  [editDescription addArrangedSubview:editHint];
  [editHeader addArrangedSubview:editDescription];
  [editHeader addArrangedSubview:flexible_spacer()];
  [editCard addArrangedSubview:editHeader];

  _editExamples = @[
    @{ @"label": @"替换", @"source": @"A 是旧版本标记，后文仍然引用 A。", @"instruction": @"把 A 替换成 B" },
    @{ @"label": @"翻译", @"source": @"勾股定理是一项伟大的发明。", @"instruction": @"把这句话翻译成英文" },
    @{ @"label": @"LaTeX", @"source": @"x 的平方加 y 的平方等于 z 的平方。", @"instruction": @"把公式改成 LaTeX" },
    @{ @"label": @"写评论", @"source": @"这个语音输入工具识别快、隐私好、安装方便。", @"instruction": @"根据这段内容写一条简短好评" },
  ];
  NSStackView *examples = horizontal_stack(6.0);
  [examples addArrangedSubview:status_label(@"练习模板")];
  for (NSInteger exampleIndex = 0;
       exampleIndex < static_cast<NSInteger>(_editExamples.count);
       ++exampleIndex) {
    NSDictionary *example = _editExamples[static_cast<NSUInteger>(exampleIndex)];
    NSButton *button = action_button(example[@"label"], self,
                                     @selector(applyEditExample:));
    button.controlSize = NSControlSizeSmall;
    button.tag = exampleIndex;
    [examples addArrangedSubview:button];
  }
  [examples addArrangedSubview:flexible_spacer()];
  [editCard addArrangedSubview:examples];

  NSTextView *editSource = nil;
  NSScrollView *editSourceEditor = text_editor(&editSource, 210.0, true);
  _editSource = editSource;
  set_text(_editSource, "A 是旧版本标记，后文仍然引用 A。");
  [editCard addArrangedSubview:labeled_content_block(
      @"可编辑文本", @"点击模板后，先单击这里定位光标，再按住 Ctrl+F9 说出页面提示的指令。",
      editSourceEditor)];
  _editStatus = status_label(@"建议口述：把 A 替换成 B");
  _editStatus.font = [NSFont systemFontOfSize:12.0 weight:NSFontWeightMedium];
  [editCard addArrangedSubview:_editStatus];
  stretch_page_contents(page);
  [self addPage:scroll identifier:@"playground"];
}

- (void)buildTermsPage {
  NSStackView *page = nil;
  NSScrollView *scroll = [self pageWithTitle:@"用户词典"
                                    subtitle:@"通过图形界面添加常用术语；高级用户仍可直接维护 terms.yaml。"
                                       stack:&page];

  [page addArrangedSubview:section_heading(
      @"快速添加",
      @"新增内容会先验证，再以原子方式写入词典；已有注释和手工配置会保留。")];
  NSStackView *quickCard = nil;
  [page addArrangedSubview:card_with_stack(&quickCard)];

  NSStackView *termRow = horizontal_stack(14.0);
  NSStackView *termDescription = vertical_stack(3.0);
  [termDescription addArrangedSubview:title_label(@"术语与热词", 13.5)];
  [termDescription addArrangedSubview:status_label(
      @"填写标准写法与多个别名，并分别选择是否作为 ASR 热词、是否禁止 ITN 改写。")];
  [termRow addArrangedSubview:termDescription];
  [termRow addArrangedSubview:flexible_spacer()];
  NSButton *addTerm = action_button(@"新增热词…", self,
                                    @selector(addTerm:));
  addTerm.bezelColor = NSColor.controlAccentColor;
  addTerm.image = [NSImage imageWithSystemSymbolName:@"text.badge.plus"
                             accessibilityDescription:@"新增热词"];
  addTerm.imagePosition = NSImageLeading;
  [termRow addArrangedSubview:addTerm];
  [quickCard addArrangedSubview:termRow];
  [quickCard addArrangedSubview:separator_line()];

  NSStackView *protectRow = horizontal_stack(14.0);
  NSStackView *protectDescription = vertical_stack(3.0);
  [protectDescription addArrangedSubview:title_label(@"仅保护短语", 13.5)];
  [protectDescription addArrangedSubview:status_label(
      @"只需填写短语；它会追加到顶层 protect 列表，不参与别名归一化。")];
  [protectRow addArrangedSubview:protectDescription];
  [protectRow addArrangedSubview:flexible_spacer()];
  NSButton *addProtect = action_button(@"新增保护词…", self,
                                       @selector(addProtectedPhrase:));
  addProtect.image = [NSImage imageWithSystemSymbolName:@"shield.badge.plus"
                                accessibilityDescription:@"新增保护词"];
  addProtect.imagePosition = NSImageLeading;
  [protectRow addArrangedSubview:addProtect];
  [quickCard addArrangedSubview:protectRow];

  [page addArrangedSubview:section_heading(
      @"文件与批量维护",
      @"词典不在设置页中预览；需要检查或批量修改时，可使用 Finder 和任意文本编辑器。")];
  NSStackView *fileCard = nil;
  [page addArrangedSubview:card_with_stack(&fileCard)];
  NSStackView *actions = horizontal_stack(8.0);
  NSButton *reload = action_button(@"热更新词典", self,
                                   @selector(reloadTerms:));
  reload.image = [NSImage imageWithSystemSymbolName:@"arrow.clockwise"
                            accessibilityDescription:@"热更新词典"];
  reload.imagePosition = NSImageLeading;
  [actions addArrangedSubview:reload];
  [actions addArrangedSubview:action_button(@"导入用户词典…", self,
                                            @selector(importTerms:))];
  [actions addArrangedSubview:action_button(@"在 Finder 中显示", self,
                                            @selector(openTerms:))];
  [actions addArrangedSubview:flexible_spacer()];
  [fileCard addArrangedSubview:actions];
  [fileCard addArrangedSubview:separator_line()];
  _termsStatus = status_label(
      @"图形化添加与导入后立即生效；在外部手工修改当前文件后，点击“热更新词典”。");
  _termsStatus.font = [NSFont systemFontOfSize:12.0];
  NSStackView *statusRow = horizontal_stack(0.0);
  [statusRow addArrangedSubview:_termsStatus];
  [statusRow addArrangedSubview:flexible_spacer()];
  [fileCard addArrangedSubview:statusRow];

  NSStackView *explanationCard = nil;
  [page addArrangedSubview:card_with_stack(&explanationCard)];
  NSStackView *explanationHeading = horizontal_stack(0.0);
  [explanationHeading addArrangedSubview:title_label(@"字段说明", 13.5)];
  [explanationHeading addArrangedSubview:flexible_spacer()];
  [explanationCard addArrangedSubview:explanationHeading];
  [explanationCard addArrangedSubview:status_label(
      @"canonical 是最终标准写法；aliases 是可能被识别出的变体；热词提高最终离线 ASR 命中率；保护阻止日期、数字等 ITN 规则误改该术语。热词与保护可以同时启用。")];

  stretch_page_contents(page);
  [self addPage:scroll identifier:@"terms"];
}

- (void)buildAIPage {
  NSStackView *page = nil;
  NSScrollView *scroll = [self pageWithTitle:@"AI 润色与语音编辑"
                                    subtitle:@"连接任意 OpenAI-compatible API；VoCoType 只发起请求，不管理模型进程。"
                                       stack:&page];

  _slmEnabled = switch_button();
  _endpoint = text_field(@"http://127.0.0.1:18080/v1/chat/completions");
  _model = text_field(@"Qwen/Qwen3.5-0.8B");
  _apiKeyEnvironment = text_field(@"例如 DEEPSEEK_API_KEY（这里只填变量名）");
  _apiKey = secure_field(@"直接粘贴 sk-...；留空则保留现有凭据");
  _clearApiKey = [NSButton checkboxWithTitle:@"清除已保存的直接 API Key"
                                      target:nil action:nil];
  _minimumCharacters = text_field(@"8");
  _timeoutMilliseconds = text_field(@"20000");
  _remoteStreaming = switch_button();
  _thinking = switch_button();
  _voiceEdit = switch_button();

  _slmEnabled.target = self;
  _slmEnabled.action = @selector(aiEnabledChanged:);
  NSNotificationCenter *notifications = NSNotificationCenter.defaultCenter;
  for (NSControl *field in @[ _endpoint, _model, _apiKeyEnvironment, _apiKey ]) {
    [notifications addObserver:self
                      selector:@selector(aiConfigurationChanged:)
                          name:NSControlTextDidChangeNotification
                        object:field];
  }

  [page addArrangedSubview:section_heading(
      @"连接",
      @"先指定兼容端点与模型；F9 普通识别不依赖这组设置。")];
  NSStackView *connectionCard = nil;
  [page addArrangedSubview:card_with_stack(&connectionCard)];
  [connectionCard addArrangedSubview:settings_row(
      @"启用 AI 功能", @"Shift+F9 润色，Ctrl+F9 语音编辑。", _slmEnabled)];
  [connectionCard addArrangedSubview:settings_row(
      @"API 地址", @"可填写服务根地址或 /v1/chat/completions。", _endpoint)];
  [connectionCard addArrangedSubview:settings_row(@"模型", @"填写服务端实际暴露的模型标识。", _model)];

  [page addArrangedSubview:section_heading(
      @"凭据",
      @"环境变量与直接 API Key 二选一；直接凭据只保存在权限为 0600 的配置文件中。")];
  NSStackView *credentialCard = nil;
  [page addArrangedSubview:card_with_stack(&credentialCard)];
  [credentialCard addArrangedSubview:settings_row(
      @"API Key 环境变量名", @"高级方式：这里只填写变量名，不粘贴密钥。", _apiKeyEnvironment)];
  [credentialCard addArrangedSubview:settings_row(
      @"直接 API Key", @"安全输入框支持标准 ⌘V；无鉴权本地服务可留空。", _apiKey)];
  [credentialCard addArrangedSubview:settings_row(
      @"清除直接凭据", @"切换到环境变量或无鉴权服务时删除旧值。", _clearApiKey)];

  [page addArrangedSubview:section_heading(
      @"行为",
      @"控制何时润色、流式超时以及模型输出能力。")];
  NSStackView *behaviorCard = nil;
  [page addArrangedSubview:card_with_stack(&behaviorCard)];
  [behaviorCard addArrangedSubview:settings_row(
      @"最少润色字符数", @"0 表示不限制。", _minimumCharacters)];
  [behaviorCard addArrangedSubview:settings_row(
      @"流式空闲超时（毫秒）", @"超过该时间未收到增量则终止请求。", _timeoutMilliseconds)];
  [behaviorCard addArrangedSubview:settings_row(
      @"流式输出", @"支持 SSE 的端点可实时显示增量。", _remoteStreaming)];
  [behaviorCard addArrangedSubview:settings_row(
      @"允许 reasoning/thinking", @"思考内容不会进入最终提交。", _thinking)];
  [behaviorCard addArrangedSubview:settings_row(
      @"启用 Ctrl+F9 语音编辑", @"模型只返回受限替换、提交或按键计划。", _voiceEdit)];

  [page addArrangedSubview:section_heading(
      @"可选连接测试",
      @"仅在需要诊断端点时主动执行；测试会发送一次真实 LLM 请求，可能产生延迟或费用，且无需在每次启动后重复。")];
  NSStackView *testCard = nil;
  [page addArrangedSubview:card_with_stack(&testCard)];
  NSStackView *test = horizontal_stack(10.0);
  NSButton *testButton = action_button(@"测试 AI 连接", self,
                                       @selector(testAI:));
  testButton.bezelColor = NSColor.controlAccentColor;
  testButton.image = [NSImage imageWithSystemSymbolName:@"bolt.horizontal.circle"
                                accessibilityDescription:@"测试 AI 连接"];
  testButton.imagePosition = NSImageLeading;
  [test addArrangedSubview:testButton];
  _aiStatus = status_label(@"尚未执行连接测试；启用并保存后可直接使用 AI 功能");
  [test addArrangedSubview:_aiStatus];
  [test addArrangedSubview:flexible_spacer()];
  [testCard addArrangedSubview:test];
  stretch_page_contents(page);
  [self addPage:scroll identifier:@"ai"];
}

- (void)buildDoctorPage {
  NSStackView *page = nil;
  NSScrollView *scroll = [self pageWithTitle:@"诊断"
                                    subtitle:@"自动检查安装与运行环境；仍无法解决时，可生成不含录音和凭据的支持包。"
                                       stack:&page];

  NSStackView *statusCard = nil;
  [page addArrangedSubview:card_with_stack(&statusCard)];
  NSStackView *statusHeader = horizontal_stack(10.0);
  NSStackView *statusText = vertical_stack(2.0);
  [statusText addArrangedSubview:title_label(@"运行检查", 13.0)];
  _doctorSummary = status_label(@"尚未运行 Doctor");
  [statusText addArrangedSubview:_doctorSummary];
  [statusHeader addArrangedSubview:statusText];
  [statusHeader addArrangedSubview:flexible_spacer()];
  NSButton *run = action_button(@"运行 Doctor", self, @selector(runDoctor:));
  run.bezelColor = NSColor.controlAccentColor;
  run.image = [NSImage imageWithSystemSymbolName:@"stethoscope"
                         accessibilityDescription:@"运行 Doctor"];
  run.imagePosition = NSImageLeading;
  [statusHeader addArrangedSubview:run];
  [statusCard addArrangedSubview:statusHeader];
  [statusCard addArrangedSubview:separator_line()];
  NSStackView *versionRow = horizontal_stack(8.0);
  [versionRow addArrangedSubview:action_button(@"查询 GitHub 最新版本", self,
                                               @selector(queryLatestRelease:))];
  _versionStatus = status_label([NSString stringWithFormat:@"当前版本：%@；尚未查询。", app_version()]);
  [versionRow addArrangedSubview:_versionStatus];
  [versionRow addArrangedSubview:flexible_spacer()];
  [statusCard addArrangedSubview:versionRow];

  [page addArrangedSubview:section_heading(@"检查结果", @"每一项都会给出明确状态与路径；未运行前不占用大块空白。")];
  _doctorChecks = vertical_stack(8.0);
  _doctorChecks.alignment = NSLayoutAttributeWidth;
  NSStackView *emptyCard = nil;
  NSBox *emptyBox = card_with_stack(&emptyCard);
  [_doctorChecks addArrangedSubview:emptyBox];
  [emptyBox.widthAnchor constraintEqualToAnchor:_doctorChecks.widthAnchor].active = YES;
  NSTextField *empty = status_label(@"点击“运行 Doctor”开始检查 Core、模型、音频设备、配置和零 Python 运行时。 ");
  empty.alignment = NSTextAlignmentCenter;
  [emptyCard addArrangedSubview:empty];
  [page addArrangedSubview:_doctorChecks];

  [page addArrangedSubview:section_heading(@"支持与导出", @"支持包会脱敏配置，并排除原始录音、API Key 与词典正文。")];
  NSStackView *supportCard = nil;
  [page addArrangedSubview:card_with_stack(&supportCard)];
  NSStackView *supportActions = horizontal_stack(8.0);
  [supportActions addArrangedSubview:action_button(@"导出支持包", self,
                                                   @selector(exportSupportBundle:))];
  [supportActions addArrangedSubview:action_button(@"打开支持目录", self,
                                                   @selector(openSupportDirectory:))];
  [supportActions addArrangedSubview:action_button(@"在 GitHub 创建 Issue", self,
                                                   @selector(openGitHubIssue:))];
  [supportActions addArrangedSubview:flexible_spacer()];
  _supportStatus = status_label(@"尚未生成支持包");
  _supportStatus.alignment = NSTextAlignmentRight;
  [supportActions addArrangedSubview:_supportStatus];
  [supportCard addArrangedSubview:supportActions];

  _doctorRawToggle = action_button(@"显示原始 Doctor 输出", self,
                                    @selector(toggleDoctorRaw:));
  NSStackView *rawToggleRow = horizontal_stack(8.0);
  [rawToggleRow addArrangedSubview:_doctorRawToggle];
  [rawToggleRow addArrangedSubview:flexible_spacer()];
  [page addArrangedSubview:rawToggleRow];
  NSStackView *rawCard = nil;
  _doctorRawContainer = card_with_stack(&rawCard);
  NSTextView *doctorOutput = nil;
  [rawCard addArrangedSubview:labeled_content_block(
      @"原始输出", @"高级排障信息；可以选择并复制。",
      text_editor(&doctorOutput, 220.0, false, false))];
  _doctorOutput = doctorOutput;
  _doctorRawContainer.hidden = YES;
  [page addArrangedSubview:_doctorRawContainer];
  stretch_page_contents(page);
  [self addPage:scroll identifier:@"doctor"];
}

- (void)buildTutorialPage {
  NSStackView *page = nil;
  NSScrollView *scroll = [self pageWithTitle:@"使用指南"
                                    subtitle:@"从安装检查到语音编辑，按顺序完成一次即可。"
                                       stack:&page];
  NSArray<NSArray<NSString *> *> *steps = @[
    @[ @"激活输入服务", @"在“概览”确认 Palette 已启用并选中；它与当前双拼、拼音或鼠须管同时工作。" ],
    @[ @"准备模型", @"校验并下载离线、流式、VAD 与标点模型。" ],
    @[ @"选择麦克风", @"在“通用”选择输入设备并保存，然后在 Playground 录音和回放。" ],
    @[ @"测试真实 ASR", @"Playground 录音后执行真实转录，确认设备和模型链路。" ],
    @[ @"使用快捷键", @"按住 F9 普通识别，Shift+F9 润色，Ctrl+F9 语音编辑。" ],
    @[ @"添加术语", @"用户词典中的项目名、人名和专业术语用于最终离线 hotword 和识别后标准化；实时预览仅作反馈。" ],
    @[ @"配置 AI", @"填写 OpenAI-compatible 端点，启用并保存后即可测试润色和语音编辑；连接测试为可选诊断。" ],
    @[ @"诊断与反馈", @"Doctor、支持包和反馈功能用于定位无法自行解决的问题。" ],
  ];
  for (NSInteger row = 0; row < 4; ++row) {
    const NSInteger leftIndex = row * 2;
    const NSInteger rightIndex = leftIndex + 1;
    NSArray<NSString *> *left = steps[static_cast<NSUInteger>(leftIndex)];
    NSArray<NSString *> *right = steps[static_cast<NSUInteger>(rightIndex)];
    [page addArrangedSubview:equal_columns(
        tutorial_step(leftIndex + 1, left[0], left[1]),
        tutorial_step(rightIndex + 1, right[0], right[1]),
        14.0)];
  }
  NSStackView *shortcutCard = nil;
  [page addArrangedSubview:card_with_stack(&shortcutCard)];
  [shortcutCard addArrangedSubview:title_label(@"快捷键速查", 13.0)];
  [shortcutCard addArrangedSubview:settings_row(@"普通识别", @"按住说话，松开提交最终识别。", plain_label(@"F9"))];
  [shortcutCard addArrangedSubview:settings_row(@"AI 润色", @"识别后调用当前 AI 配置整理文本。", plain_label(@"Shift+F9"))];
  [shortcutCard addArrangedSubview:settings_row(@"语音编辑", @"根据当前文本上下文执行受限编辑。", plain_label(@"Ctrl+F9"))];
  stretch_page_contents(page);
  [self addPage:scroll identifier:@"tutorial"];
}

- (void)buildFeedbackPage {
  NSStackView *page = nil;
  NSScrollView *scroll = [self pageWithTitle:@"反馈"
                                    subtitle:@"直接发送给维护者，或创建公开 GitHub Issue；诊断信息默认不附带。"
                                       stack:&page];

  [page addArrangedSubview:section_heading(@"基本信息", @"帮助维护者分类和联系；联系方式可以留空。")];
  NSStackView *metaCard = nil;
  [page addArrangedSubview:card_with_stack(&metaCard)];
  _feedbackCategory = [[NSPopUpButton alloc] initWithFrame:NSZeroRect pullsDown:NO];
  [_feedbackCategory addItemsWithTitles:@[ @"问题 / Bug", @"安装与升级", @"兼容性", @"易用性", @"功能建议", @"其他" ]];
  [metaCard addArrangedSubview:settings_row(@"反馈类型", @"用于分类和合并重复报告。", _feedbackCategory)];
  _feedbackContact = text_field(@"可选：邮箱或 GitHub 用户名");
  [metaCard addArrangedSubview:settings_row(@"联系方式", @"不填写也可以匿名提交。", _feedbackContact)];

  [page addArrangedSubview:section_heading(@"反馈正文", @"描述现象、复现步骤、期望结果和实际结果。")];
  NSStackView *messageCard = nil;
  [page addArrangedSubview:card_with_stack(&messageCard)];
  NSTextView *feedbackMessage = nil;
  NSScrollView *messageEditor = text_editor(&feedbackMessage, 230.0, true);
  _feedbackMessage = feedbackMessage;
  [messageCard addArrangedSubview:labeled_content_block(
      @"详细说明", @"请勿粘贴 API Key、密码或其他敏感信息。", messageEditor)];

  [page addArrangedSubview:section_heading(@"诊断附件", @"默认全部关闭；只有你主动勾选后才会附带。")];
  NSStackView *diagnosticCard = nil;
  [page addArrangedSubview:card_with_stack(&diagnosticCard)];
  _feedbackIncludeDoctor = [NSButton checkboxWithTitle:@"附带 Doctor 结果"
                                                 target:nil action:nil];
  _feedbackIncludeBundle = [NSButton checkboxWithTitle:@"附带脱敏支持包（最大 5 MiB）"
                                                 target:nil action:nil];
  [diagnosticCard addArrangedSubview:settings_row(
      @"Doctor 结果", @"不含原始录音、API Key 或词典正文。", _feedbackIncludeDoctor)];
  [diagnosticCard addArrangedSubview:settings_row(
      @"支持包", @"包含脱敏配置、服务日志与输入法诊断。", _feedbackIncludeBundle)];
  _feedbackCustomEndpoint = [NSButton checkboxWithTitle:@"启用自定义端点"
                                                  target:self
                                                  action:@selector(feedbackEndpointToggled:)];
  _feedbackEndpoint = text_field(@"https://example.org/v1/feedback");
  _feedbackEndpoint.stringValue = @"https://feedback.vocotype-linux.lsamc.website/v1/feedback";
  _feedbackEndpoint.enabled = NO;
  NSStackView *endpoint = vertical_stack(6.0);
  [endpoint addArrangedSubview:_feedbackCustomEndpoint];
  [endpoint addArrangedSubview:_feedbackEndpoint];
  [diagnosticCard addArrangedSubview:settings_row(
      @"自托管反馈服务器", @"高级选项；普通用户使用官方端点。", endpoint)];

  NSStackView *submitCard = nil;
  [page addArrangedSubview:card_with_stack(&submitCard)];
  NSStackView *actions = horizontal_stack(8.0);
  NSButton *send = action_button(@"发送给维护者", self,
                                 @selector(sendFeedback:));
  send.bezelColor = NSColor.controlAccentColor;
  send.image = [NSImage imageWithSystemSymbolName:@"paperplane.fill"
                          accessibilityDescription:@"发送反馈"];
  send.imagePosition = NSImageLeading;
  [actions addArrangedSubview:send];
  [actions addArrangedSubview:action_button(@"在 GitHub 创建公开 Issue", self,
                                            @selector(openGitHubIssue:))];
  [actions addArrangedSubview:flexible_spacer()];
  _feedbackStatus = status_label(@"尚未提交");
  _feedbackStatus.alignment = NSTextAlignmentRight;
  [actions addArrangedSubview:_feedbackStatus];
  [submitCard addArrangedSubview:actions];
  stretch_page_contents(page);
  [self addPage:scroll identifier:@"feedback"];
}

- (void)showSettings:(id)sender {
  (void)sender;
  [self showSettingsWindow];
}

- (void)showSettingsWindow {
  ++_settingsWindowPresentationCount;
  if (!_settingsWindow)
    _settingsWindow = [self buildSettingsWindow];
  [self reloadFields];
  [self refreshAudioDevices:nil];
  [self refreshOverview:nil];
  [NSApp activateIgnoringOtherApps:YES];
  [_settingsWindow center];
  [_settingsWindow makeKeyAndOrderFront:nil];
}

- (void)showSettingsPage:(NSInteger)index {
  [self showSettingsWindow];
  [self selectPage:index];
}

- (void)runMicrophoneSmokeTestToPath:(NSString *)path
                          completion:(void (^)(void))completion {
  [self ensureMicrophoneAccess:^(BOOL granted) {
    if (!granted) {
      Json result = {{"success", false},
                     {"authorization", "denied"},
                     {"error", "microphone permission is not granted"}};
      std::ofstream output(path.fileSystemRepresentation,
                           std::ios::binary | std::ios::trunc);
      output << result.dump(2) << '\n';
      output.flush();
      output.close();
      if (completion)
        completion();
      return;
    }
    __weak VocoTypeApplicationController *weakSelf = self;
    run_async([] {
      std::size_t blocks = 0;
      double peak = 0.0;
      Json result = settings::capture_recording(
          1500, [&](double minimum, double maximum) {
        ++blocks;
        peak = std::max(peak, std::max(std::abs(minimum), std::abs(maximum)));
      });
      result["authorization"] = "authorized";
      result["waveform_blocks"] = blocks;
      result["peak"] = peak;
      return result;
    }, [weakSelf, path, completion](Json result) {
      VocoTypeApplicationController *self = weakSelf;
      if (self && result.value("success", false)) {
        const std::filesystem::path recording = result.value("path", "");
        if (!recording.empty())
          std::filesystem::remove(recording);
      }
      std::ofstream output(path.fileSystemRepresentation,
                           std::ios::binary | std::ios::trunc);
      output << result.dump(2) << '\n';
      output.flush();
      output.close();
      if (completion)
        completion();
    });
  }];
}

- (void)scrollCurrentPageToFraction:(double)fraction {
  if (!_pages || _selectedPage < 0 ||
      _selectedPage >= static_cast<NSInteger>(_pages.count))
    return;
  NSView *view = _pages[static_cast<NSUInteger>(_selectedPage)];
  if (![view isKindOfClass:NSScrollView.class])
    return;
  NSScrollView *scroll = (NSScrollView *)view;
  [scroll layoutSubtreeIfNeeded];
  NSClipView *clip = scroll.contentView;
  NSView *document = scroll.documentView;
  if (!document)
    return;
  const CGFloat maximum = std::max<CGFloat>(
      0.0, NSHeight(document.bounds) - NSHeight(clip.bounds));
  const CGFloat y = maximum * std::clamp(fraction, 0.0, 1.0);
  [clip setBoundsOrigin:NSMakePoint(0.0, y)];
  [scroll reflectScrolledClipView:clip];
  [scroll displayIfNeeded];
}

- (BOOL)runApiKeyPasteSmokeTestWithText:(NSString *)text {
  [self showSettingsPage:4];
  if (!_apiKey || !_settingsWindow || text.length == 0)
    return NO;

  NSPasteboard *pasteboard = NSPasteboard.generalPasteboard;
  [pasteboard clearContents];
  if (![pasteboard setString:text forType:NSPasteboardTypeString])
    return NO;

  [NSApp activateIgnoringOtherApps:YES];
  [_settingsWindow makeKeyAndOrderFront:nil];
  [_apiKey scrollRectToVisible:_apiKey.bounds];
  [_settingsWindow.contentView layoutSubtreeIfNeeded];
  if (![_settingsWindow makeFirstResponder:_apiKey])
    return NO;
  [_apiKey selectText:nil];
  NSText *editor = _apiKey.currentEditor;
  if (!editor)
    return NO;
  [editor setSelectedRange:NSMakeRange(0, editor.string.length)];

  BOOL has_command_v = NO;
  for (NSMenuItem *top_level in NSApp.mainMenu.itemArray) {
    NSMenu *submenu = top_level.submenu;
    if (!submenu)
      continue;
    for (NSMenuItem *item in submenu.itemArray) {
      if (item.action == @selector(paste:) &&
          [item.keyEquivalent.lowercaseString isEqualToString:@"v"] &&
          (item.keyEquivalentModifierMask & NSEventModifierFlagCommand) != 0) {
        has_command_v = YES;
        break;
      }
    }
    if (has_command_v)
      break;
  }

  const BOOL dispatched = [NSApp sendAction:@selector(paste:)
                                          to:editor
                                        from:self];
  const BOOL editor_matches = [editor.string isEqualToString:text];
  [_settingsWindow makeFirstResponder:nil];
  return has_command_v && dispatched && editor_matches &&
         [_apiKey.stringValue isEqualToString:text];
}

- (BOOL)runMenuSmokeTestToPath:(NSString *)path {
  [self showSettingsWindow];
  [NSApp activateIgnoringOtherApps:YES];
  [_settingsWindow makeKeyAndOrderFront:nil];

  Json menus = Json::array();
  NSMenuItem *closeItem = nil;
  for (NSMenuItem *topLevel in NSApp.mainMenu.itemArray) {
    NSMenu *submenu = topLevel.submenu;
    const std::string title = to_utf8(
        submenu.title.length > 0 ? submenu.title : topLevel.title);
    Json entry{{"title", title},
               {"hidden", static_cast<bool>(topLevel.hidden)},
               {"enabled", static_cast<bool>(topLevel.enabled)},
               {"items", Json::array()}};
    for (NSMenuItem *item in submenu.itemArray) {
      entry["items"].push_back({
          {"title", to_utf8(item.title)},
          {"key", to_utf8(item.keyEquivalent)},
          {"hidden", static_cast<bool>(item.hidden)},
          {"enabled", static_cast<bool>(item.enabled)},
      });
      if ([item.title isEqualToString:@"关闭窗口"])
        closeItem = item;
    }
    menus.push_back(std::move(entry));
  }

  const BOOL hasCommandW = closeItem &&
      [closeItem.keyEquivalent.lowercaseString isEqualToString:@"w"] &&
      (closeItem.keyEquivalentModifierMask & NSEventModifierFlagCommand) != 0;
  const BOOL visibleBefore = _settingsWindow.visible;
  NSEvent *commandW = [NSEvent keyEventWithType:NSEventTypeKeyDown
                                      location:NSZeroPoint
                                 modifierFlags:NSEventModifierFlagCommand
                                     timestamp:NSProcessInfo.processInfo.systemUptime
                                  windowNumber:_settingsWindow.windowNumber
                                       context:nil
                                    characters:@"w"
                   charactersIgnoringModifiers:@"w"
                                      isARepeat:NO
                                        keyCode:13];
  const BOOL dispatched = commandW && [NSApp.mainMenu performKeyEquivalent:commandW];
  const BOOL closed = !_settingsWindow.visible;
  [self showSettingsWindow];
  const BOOL reopened = _settingsWindow.visible;
  const BOOL success = hasCommandW && visibleBefore && dispatched && closed && reopened;

  Json result{{"success", static_cast<bool>(success)},
              {"has_command_w", static_cast<bool>(hasCommandW)},
              {"visible_before", static_cast<bool>(visibleBefore)},
              {"dispatched", static_cast<bool>(dispatched)},
              {"closed", static_cast<bool>(closed)},
              {"reopened", static_cast<bool>(reopened)},
              {"menus", std::move(menus)}};
  if (path.length > 0) {
    std::ofstream output(path.fileSystemRepresentation,
                         std::ios::binary | std::ios::trunc);
    output << result.dump(2) << '\n';
  }
  return success;
}

- (BOOL)runTabSwitchSmokeTestToPath:(NSString *)path {
  [self showSettingsWindow];
  [_settingsWindow.contentView layoutSubtreeIfNeeded];
  const NSUInteger presentationsBefore = _settingsWindowPresentationCount;
  const NSTimeInterval started = NSProcessInfo.processInfo.systemUptime;
  constexpr NSInteger kSwitchCount = 32;
  for (NSInteger iteration = 0; iteration < kSwitchCount; ++iteration) {
    const NSInteger index = iteration % 2 == 0 ? kPlaygroundPage : kGeneralPage;
    [_sidebarButtons[static_cast<NSUInteger>(index)] performClick:nil];
    [_settingsWindow.contentView layoutSubtreeIfNeeded];
  }
  const NSTimeInterval elapsed =
      NSProcessInfo.processInfo.systemUptime - started;
  const NSUInteger presentationsAfter = _settingsWindowPresentationCount;
  const NSInteger selected = _selectedPage;
  const BOOL success = presentationsAfter == presentationsBefore &&
                       selected == kGeneralPage && elapsed < 1.0;
  Json result{{"success", static_cast<bool>(success)},
              {"switches", kSwitchCount},
              {"elapsed_ms", elapsed * 1000.0},
              {"presentation_count_before", presentationsBefore},
              {"presentation_count_after", presentationsAfter},
              {"selected_page", selected}};
  if (path.length > 0) {
    std::ofstream output(path.fileSystemRepresentation,
                         std::ios::binary | std::ios::trunc);
    output << result.dump(2) << '\n';
  }
  return success;
}

- (BOOL)runTermsPageSmokeTestToPath:(NSString *)path {
  [self showSettingsWindow];
  [self showSettingsPage:kTermsPage];
  [_settingsWindow.contentView layoutSubtreeIfNeeded];

  NSMutableSet<NSString *> *buttonTitles = [NSMutableSet set];
  NSInteger textViewCount = 0;
  collect_terms_page_views(_pages[static_cast<NSUInteger>(kTermsPage)],
                           buttonTitles, &textViewCount);

  NSArray<NSString *> *requiredButtons = @[
    @"新增热词…", @"新增保护词…", @"热更新词典",
    @"导入用户词典…", @"在 Finder 中显示"
  ];
  BOOL buttonsPresent = YES;
  for (NSString *title in requiredButtons) {
    if (![buttonTitles containsObject:title]) {
      buttonsPresent = NO;
      break;
    }
  }

  BOOL hasControlUndo = NO;
  for (NSMenuItem *topLevel in NSApp.mainMenu.itemArray) {
    for (NSMenuItem *item in topLevel.submenu.itemArray) {
      if (item.action == @selector(performControlUndo:) &&
          [item.keyEquivalent.lowercaseString isEqualToString:@"z"] &&
          item.keyEquivalentModifierMask == NSEventModifierFlagControl) {
        hasControlUndo = item.allowsKeyEquivalentWhenHidden;
        break;
      }
    }
    if (hasControlUndo)
      break;
  }

  [self showSettingsPage:kPlaygroundPage];
  NSString *original = _editSource.string;
  NSString *before = @"撤销测试";
  _editSource.string = before;
  [_editSource.undoManager removeAllActions];
  [_settingsWindow makeKeyAndOrderFront:nil];
  [_settingsWindow makeFirstResponder:_editSource];
  [_editSource setSelectedRange:NSMakeRange(before.length, 0)];
  [_editSource insertText:@"成功"
         replacementRange:NSMakeRange(NSNotFound, NSNotFound)];
  const BOOL undoRegistered = _editSource.allowsUndo &&
                              _editSource.undoManager.canUndo;
  NSEvent *controlZ = [NSEvent keyEventWithType:NSEventTypeKeyDown
                                       location:NSZeroPoint
                                  modifierFlags:NSEventModifierFlagControl
                                      timestamp:NSProcessInfo.processInfo.systemUptime
                                   windowNumber:_settingsWindow.windowNumber
                                        context:nil
                                     characters:@"z"
                    charactersIgnoringModifiers:@"z"
                                       isARepeat:NO
                                         keyCode:6];
  const BOOL controlUndoDispatched =
      controlZ && [NSApp.mainMenu performKeyEquivalent:controlZ];
  const BOOL controlUndoWorked = [_editSource.string isEqualToString:before];
  _editSource.string = original;
  [_editSource.undoManager removeAllActions];
  [_settingsWindow makeFirstResponder:nil];
  [self showSettingsPage:kTermsPage];

  const BOOL success = buttonsPresent && textViewCount == 0 &&
                       hasControlUndo && undoRegistered &&
                       controlUndoDispatched && controlUndoWorked;
  Json result{{"success", static_cast<bool>(success)},
              {"required_buttons_present", static_cast<bool>(buttonsPresent)},
              {"raw_text_view_count", textViewCount},
              {"has_control_z", static_cast<bool>(hasControlUndo)},
              {"undo_registered", static_cast<bool>(undoRegistered)},
              {"control_z_dispatched", static_cast<bool>(controlUndoDispatched)},
              {"control_z_worked", static_cast<bool>(controlUndoWorked)}};
  if (path.length > 0) {
    std::ofstream output(path.fileSystemRepresentation,
                         std::ios::binary | std::ios::trunc);
    output << result.dump(2) << '\n';
  }
  return success;
}

- (BOOL)captureSettingsWindowToPath:(NSString *)path {
  if (path.length == 0)
    return NO;
  [self showSettingsWindow];
  NSView *view = _settingsWindow.contentView;
  [view layoutSubtreeIfNeeded];
  NSBitmapImageRep *bitmap =
      [view bitmapImageRepForCachingDisplayInRect:view.bounds];
  if (!bitmap)
    return NO;
  [view cacheDisplayInRect:view.bounds toBitmapImageRep:bitmap];
  NSData *png = [bitmap representationUsingType:NSBitmapImageFileTypePNG
                                     properties:@{}];
  return png && [png writeToFile:path atomically:YES];
}

- (void)selectPage:(NSInteger)index {
  if (!_pages || index < 0 || index >= static_cast<NSInteger>(_pages.count))
    return;
  const BOOL changed = index != _selectedPage;
  if (changed) {
    if (_selectedPage >= 0 &&
        _selectedPage < static_cast<NSInteger>(_pages.count))
      _pages[static_cast<NSUInteger>(_selectedPage)].hidden = YES;
    NSView *page = _pages[static_cast<NSUInteger>(index)];
    page.frame = _pageContainer.bounds;
    page.hidden = NO;
    _selectedPage = index;
  }
  for (NSInteger buttonIndex = 0;
       buttonIndex < static_cast<NSInteger>(_sidebarButtons.count);
       ++buttonIndex) {
    _sidebarButtons[buttonIndex].state =
        buttonIndex == index ? NSControlStateValueOn : NSControlStateValueOff;
  }
  if (!changed)
    return;
  if (index == kOverviewPage)
    [self refreshOverview:nil];
}

- (void)selectPageFromSender:(id)sender {
  if (!_settingsWindow || !_settingsWindow.visible)
    [self showSettingsWindow];
  [self selectPage:[sender tag]];
}

- (void)reloadFields {
  try {
    _config = vocotype::desktop::read_shared_config(true);
    if (!_config.is_object())
      _config = Json::object();
    const Json audio = _config.value("audio", Json::object());
    _audioRate.integerValue = audio.value("sample_rate", 44100);
    _minimumRecording.integerValue = audio.value("min_recording_ms", 500);
    _streamingEnabled.state =
        _config.value("asr_streaming", Json::object()).value("enabled", true)
            ? NSControlStateValueOn
            : NSControlStateValueOff;
    const Json normalization = _config.value("normalization", Json::object());
    _normalizationEnabled.state = normalization.value("enabled", true)
                                      ? NSControlStateValueOn
                                      : NSControlStateValueOff;
    _compactDates.state = normalization.value("compact_dates", true)
                              ? NSControlStateValueOn
                              : NSControlStateValueOff;
    _compactTimes.state = normalization.value("compact_times", true)
                              ? NSControlStateValueOn
                              : NSControlStateValueOff;
    _compactDistances.state = normalization.value("compact_distances", true)
                                  ? NSControlStateValueOn
                                  : NSControlStateValueOff;
    _currencySymbols.state = normalization.value("currency_symbols", true)
                                 ? NSControlStateValueOn
                                 : NSControlStateValueOff;

    const Json slm = _config.value("slm", Json::object());
    _slmEnabled.state = slm.value("enabled", false) ? NSControlStateValueOn
                                                    : NSControlStateValueOff;
    _endpoint.stringValue = to_ns(slm.value(
        "endpoint", "http://127.0.0.1:18080/v1/chat/completions"));
    _model.stringValue = to_ns(slm.value("model", "Qwen/Qwen3.5-0.8B"));
    _apiKey.stringValue = @"";
    _apiKey.placeholderString = slm.value("api_key", std::string()).empty()
                                    ? @"可选"
                                    : @"已保存；留空则保留原值";
    _apiKeyEnvironment.stringValue =
        to_ns(slm.value("api_key_env", std::string()));
    _clearApiKey.state = NSControlStateValueOff;
    _minimumCharacters.integerValue = slm.value("min_chars", 8);
    _timeoutMilliseconds.integerValue = slm.value("timeout_ms", 20000);
    _remoteStreaming.state = slm.value("remote_stream", true)
                                 ? NSControlStateValueOn
                                 : NSControlStateValueOff;
    _thinking.state = slm.value("enable_thinking", false)
                          ? NSControlStateValueOn
                          : NSControlStateValueOff;
    _voiceEdit.state = slm.value("edit_enabled", true)
                          ? NSControlStateValueOn
                          : NSControlStateValueOff;

    const Json platform = vocotype::desktop::read_macos_config(true);
    const Json hotkeys = platform.value("hotkeys", Json::object());
    _hotkeyButtons[0].title = to_ns(hotkeys.value("transcribe", "F9"));
    _hotkeyButtons[1].title = to_ns(hotkeys.value("polish", "Shift+F9"));
    _hotkeyButtons[2].title = to_ns(hotkeys.value("edit", "Ctrl+F9"));
    set_status(_globalStatus, @"");
  } catch (const std::exception &error) {
    set_status(_globalStatus,
               [@"读取配置失败：" stringByAppendingString:to_ns(error.what())],
               true);
  }
}

- (BOOL)persistSettings {
  for (NSButton *button : _hotkeyButtons) {
    if (!valid_hotkey(button.title)) {
      set_status(_globalStatus,
                 @"快捷键仅支持 F1–F20 与 Shift/Ctrl/Option/Command 组合。",
                 true);
      return NO;
    }
  }
  if ([_hotkeyButtons[0].title caseInsensitiveCompare:_hotkeyButtons[1].title] ==
          NSOrderedSame ||
      [_hotkeyButtons[0].title caseInsensitiveCompare:_hotkeyButtons[2].title] ==
          NSOrderedSame ||
      [_hotkeyButtons[1].title caseInsensitiveCompare:_hotkeyButtons[2].title] ==
          NSOrderedSame) {
    set_status(_globalStatus, @"三个快捷键不能重复。", true);
    return NO;
  }
  NSString *endpoint = trimmed(_endpoint.stringValue);
  NSString *model = trimmed(_model.stringValue);
  if (_slmEnabled.state == NSControlStateValueOn &&
      (endpoint.length == 0 || model.length == 0)) {
    set_status(_globalStatus, @"启用 AI 功能时必须填写 API 地址和模型。", true);
    return NO;
  }
  try {
    Json shared = _config.is_object() ? _config
                                      : vocotype::desktop::read_shared_config(true);
    Json &audio = shared["audio"];
    NSInteger selected = _audioInput.indexOfSelectedItem;
    if (selected >= 0 &&
        static_cast<std::size_t>(selected) < _inputDevices.size()) {
      const auto &device = _inputDevices[static_cast<std::size_t>(selected)];
      audio["device"] = device.id;
      audio["device_name"] = device.name;
    } else {
      audio["device"] = nullptr;
      audio.erase("device_name");
    }
    audio["sample_rate"] =
        std::clamp(static_cast<int>(_audioRate.integerValue), 8000, 192000);
    audio["block_ms"] = 20;
    audio["min_recording_ms"] =
        std::clamp(static_cast<int>(_minimumRecording.integerValue), 0, 10000);
    shared["asr"]["native_enabled"] = true;
    shared["asr_streaming"]["enabled"] =
        _streamingEnabled.state == NSControlStateValueOn;
    Json &normalization = shared["normalization"];
    normalization["enabled"] =
        _normalizationEnabled.state == NSControlStateValueOn;
    normalization["compact_dates"] = _compactDates.state == NSControlStateValueOn;
    normalization["compact_times"] = _compactTimes.state == NSControlStateValueOn;
    normalization["compact_distances"] =
        _compactDistances.state == NSControlStateValueOn;
    normalization["currency_symbols"] =
        _currencySymbols.state == NSControlStateValueOn;

    Json &slm = shared["slm"];
    slm["enabled"] = _slmEnabled.state == NSControlStateValueOn;
    slm["endpoint"] = to_utf8(endpoint);
    slm["model"] = to_utf8(model);
    slm["api_key_env"] = to_utf8(trimmed(_apiKeyEnvironment.stringValue));
    NSString *entered = trimmed(_apiKey.stringValue);
    if (_clearApiKey.state == NSControlStateValueOn)
      slm["api_key"] = "";
    else if (entered.length > 0)
      slm["api_key"] = to_utf8(entered);
    slm["min_chars"] =
        std::clamp(static_cast<int>(_minimumCharacters.integerValue), 0, 2000);
    slm["timeout_ms"] = std::clamp(
        static_cast<int>(_timeoutMilliseconds.integerValue), 1000, 120000);
    slm["remote_stream"] =
        _remoteStreaming.state == NSControlStateValueOn;
    slm["enable_thinking"] = _thinking.state == NSControlStateValueOn;
    slm["edit_enabled"] = _voiceEdit.state == NSControlStateValueOn;
    slm["edit_max_tokens"] = std::max(1024, slm.value("edit_max_tokens", 1024));

    vocotype::desktop::write_shared_config(shared);
    vocotype::desktop::write_macos_hotkeys(
        {{"transcribe", to_utf8(_hotkeyButtons[0].title)},
         {"polish", to_utf8(_hotkeyButtons[1].title)},
         {"edit", to_utf8(_hotkeyButtons[2].title)}});
    _config = std::move(shared);
    CFNotificationCenterPostNotification(
        CFNotificationCenterGetDarwinNotifyCenter(),
        CFSTR("io.github.LeonardNJU.VoCoTypeLinux.ReloadHotkeys"), nullptr,
        nullptr, true);
    stop_native_core();
    _apiKey.stringValue = @"";
    _clearApiKey.state = NSControlStateValueOff;
    return YES;
  } catch (const std::exception &error) {
    set_status(_globalStatus,
               [@"保存失败：" stringByAppendingString:to_ns(error.what())],
               true);
    return NO;
  }
}

- (void)saveSettings:(id)sender {
  (void)sender;
  if ([self persistSettings]) {
    set_status(_globalStatus,
               @"设置已保存；下一次语音操作将使用新配置。", false);
    [self refreshAudioDevices:nil];
  }
}

- (void)refreshOverview:(id)sender {
  (void)sender;
  if (!_overviewStatus)
    return;
  set_status(_overviewStatus, @"正在检查…");
  const std::string version = to_utf8(app_version());
  __weak VocoTypeApplicationController *weakSelf = self;
  run_async([version] {
    Json result = settings::overview_status(version);
    const auto app = vocotype::desktop::runtime_root().parent_path().parent_path();
    const auto tool = vocotype::desktop::runtime_root() /
                      "bin/vocotype-input-source-tool";
    result["palette_state"] = settings::run_process(
        {tool.string(), "--list",
         "io.github.LeonardNJU.VoCoTypeLinux.InputMethod"});
    result["model_state"] = settings::model_status();
    result["application_path"] = app.string();
    return result;
  }, [weakSelf](Json result) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self)
      return;
    NSString *summary = [NSString stringWithFormat:
        @"版本 %@；Core %@；输入设备 %lld；输出设备 %lld\n资源：%@",
        app_version(), result.value("core_ready", false) ? @"运行中" : @"按需启动",
        static_cast<long long>(result.value("input_devices", 0U)),
        static_cast<long long>(result.value("output_devices", 0U)),
        to_ns(result.value("runtime_root", std::string()))];
    set_status(self->_overviewStatus, summary,
               !result.value("success", false));

    const Json palette = result.value("palette_state", Json::object());
    const std::string output = palette.value("output", "");
    const bool enabled = output.find("\"enabled\":true") != std::string::npos;
    const bool selected = output.find("\"selected\":true") != std::string::npos;
    set_status(self->_overviewPaletteStatus,
               [NSString stringWithFormat:@"%@%@ · %@",
                    enabled ? @"已启用" : @"未启用",
                    selected ? @"并已激活" : @"",
                    to_ns(result.value("application_path", std::string()))],
               !enabled || !selected);

    const Json models = result.value("model_state", Json::object());
    set_status(self->_overviewModelStatus,
               models.value("success", false)
                   ? @"全部模型已通过 SHA-256 校验"
                   : [@"模型需要处理：" stringByAppendingString:
                          to_ns(models.value("error", models.value("output", "unknown")))],
               !models.value("success", false));
  });
}

- (void)activatePalette:(id)sender {
  (void)sender;
  set_status(_overviewPaletteStatus, @"正在激活…");
  const auto tool = vocotype::desktop::runtime_root() /
                    "bin/vocotype-input-source-tool";
  __weak VocoTypeApplicationController *weakSelf = self;
  run_async([tool] {
    return settings::run_process(
        {tool.string(), "--activate",
         "io.github.LeonardNJU.VoCoTypeLinux.InputMethod"});
  }, [weakSelf](Json result) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self)
      return;
    set_status(self->_overviewPaletteStatus,
               result.value("success", false)
                   ? @"Palette 已启用并激活"
                   : [@"激活失败：" stringByAppendingString:
                          to_ns(result.value("error", "unknown"))],
               !result.value("success", false));
    [self refreshOverview:nil];
  });
}

- (void)restartCore:(id)sender {
  (void)sender;
  set_status(_globalStatus, @"正在重启 Core…");
  stop_native_core();
  __weak VocoTypeApplicationController *weakSelf = self;
  run_async([] {
    bool ready = vocotype::desktop::ensure_native_core(
        vocotype::desktop::backend_socket_path(),
        vocotype::desktop::runtime_config_path(), 45000);
    return Json{{"success", ready},
                {"error", ready ? "" : "Core 启动失败"}};
  }, [weakSelf](Json result) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self)
      return;
    set_status(self->_globalStatus,
               result.value("success", false) ? @"Core 已重启"
                                               : to_ns(result.value("error", "失败")),
               !result.value("success", false));
    [self refreshOverview:nil];
  });
}

- (void)downloadModels:(id)sender {
  (void)sender;
  [_globalProgress startAnimation:nil];
  set_status(_globalStatus, @"正在校验并下载全部原生语音模型…");
  __weak VocoTypeApplicationController *weakSelf = self;
  run_async([] { return settings::download_models(); }, [weakSelf](Json result) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self)
      return;
    [self->_globalProgress stopAnimation:nil];
    set_status(self->_globalStatus,
               result.value("success", false)
                   ? @"全部语音模型已下载并通过 SHA-256 校验。"
                   : [@"模型下载或校验失败：" stringByAppendingString:
                          to_ns(result.value("error", result.value("output", "unknown")))],
               !result.value("success", false));
    [self refreshOverview:nil];
  });
}

- (void)refreshAudioDevices:(id)sender {
  (void)sender;
  if (!_audioInput)
    return;
  set_status(_audioStatus, @"正在枚举音频设备…");
  __weak VocoTypeApplicationController *weakSelf = self;
  run_async([] {
    try {
      auto inventory = vocotype::desktop::list_audio_devices();
      Json inputs = Json::array();
      Json outputs = Json::array();
      for (const auto &device : inventory.inputs)
        inputs.push_back({{"id", device.id}, {"name", device.name},
                          {"channels", device.max_input_channels},
                          {"sample_rate", device.default_sample_rate},
                          {"default", device.is_default}});
      for (const auto &device : inventory.outputs)
        outputs.push_back({{"id", device.id}, {"name", device.name},
                           {"channels", device.max_output_channels},
                           {"sample_rate", device.default_sample_rate},
                           {"default", device.is_default}});
      return Json{{"success", true}, {"inputs", inputs}, {"outputs", outputs}};
    } catch (const std::exception &error) {
      return Json{{"success", false}, {"error", error.what()}};
    }
  }, [weakSelf](Json result) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self)
      return;
    if (!result.value("success", false)) {
      set_status(self->_audioStatus,
                 [@"音频设备枚举失败：" stringByAppendingString:
                        to_ns(result.value("error", "unknown"))], true);
      return;
    }
    self->_inputDevices.clear();
    self->_outputDevices.clear();
    [self->_audioInput removeAllItems];
    [self->_playgroundAudioInput removeAllItems];
    [self->_audioOutput removeAllItems];
    const Json audio = self->_config.value("audio", Json::object());
    const std::optional<int> configuredId =
        audio.contains("device") && audio["device"].is_number_integer()
            ? std::optional<int>(audio["device"].get<int>())
            : std::nullopt;
    const std::string configuredName = audio.value("device_name", "");
    NSInteger selectedInput = -1;
    for (const auto &item : result["inputs"]) {
      vocotype::desktop::AudioDevice device;
      device.id = item.value("id", -1);
      device.name = item.value("name", "");
      device.max_input_channels = item.value("channels", 0);
      device.default_sample_rate = item.value("sample_rate", 16000);
      device.is_default = item.value("default", false);
      self->_inputDevices.push_back(device);
      NSString *display = [NSString stringWithFormat:@"%@%@",
          to_ns(device.name), device.is_default ? @"（默认）" : @""];
      [self->_audioInput addItemWithTitle:display];
      [self->_playgroundAudioInput addItemWithTitle:display];
      if ((configuredId && *configuredId == device.id) ||
          (!configuredName.empty() && configuredName == device.name))
        selectedInput = static_cast<NSInteger>(self->_inputDevices.size() - 1);
    }
    if (selectedInput < 0) {
      auto found = std::find_if(self->_inputDevices.begin(),
                                self->_inputDevices.end(),
                                [](const auto &device) { return device.is_default; });
      selectedInput = found == self->_inputDevices.end()
                          ? (self->_inputDevices.empty() ? -1 : 0)
                          : static_cast<NSInteger>(found - self->_inputDevices.begin());
    }
    [self->_audioInput selectItemAtIndex:selectedInput];
    [self->_playgroundAudioInput selectItemAtIndex:selectedInput];

    NSInteger selectedOutput = -1;
    for (const auto &item : result["outputs"]) {
      vocotype::desktop::AudioOutputDevice device;
      device.id = item.value("id", -1);
      device.name = item.value("name", "");
      device.max_output_channels = item.value("channels", 0);
      device.default_sample_rate = item.value("sample_rate", 48000);
      device.is_default = item.value("default", false);
      self->_outputDevices.push_back(device);
      [self->_audioOutput addItemWithTitle:[NSString stringWithFormat:@"%@%@",
          to_ns(device.name), device.is_default ? @"（默认）" : @""]];
      if (device.is_default)
        selectedOutput = static_cast<NSInteger>(self->_outputDevices.size() - 1);
    }
    if (selectedOutput < 0 && !self->_outputDevices.empty())
      selectedOutput = 0;
    [self->_audioOutput selectItemAtIndex:selectedOutput];
    AVAudioApplicationRecordPermission permission =
        AVAudioApplication.sharedInstance.recordPermission;
    NSString *permissionText = @"未请求";
    if (permission == AVAudioApplicationRecordPermissionGranted)
      permissionText = @"已允许";
    else if (permission == AVAudioApplicationRecordPermissionDenied)
      permissionText = @"已拒绝";
    set_status(self->_audioStatus,
               [NSString stringWithFormat:
                    @"已发现 %zu 个输入设备、%zu 个输出设备 · 麦克风权限：%@",
                    self->_inputDevices.size(), self->_outputDevices.size(),
                    permissionText],
               permission == AVAudioApplicationRecordPermissionDenied);
    if (self->_lastRecording.empty())
      set_status(self->_playgroundStatus, self->_audioStatus.stringValue);
  });
}

- (void)audioSelectionChanged:(id)sender {
  if (sender == _audioInput)
    [_playgroundAudioInput selectItemAtIndex:_audioInput.indexOfSelectedItem];
  else if (sender == _playgroundAudioInput)
    [_audioInput selectItemAtIndex:_playgroundAudioInput.indexOfSelectedItem];
  if (_audioInput.indexOfSelectedItem >= 0 &&
      static_cast<std::size_t>(_audioInput.indexOfSelectedItem) <
          _inputDevices.size()) {
    const auto &device = _inputDevices[static_cast<std::size_t>(
        _audioInput.indexOfSelectedItem)];
    _audioRate.integerValue = device.default_sample_rate;
  }
}

- (void)beginHotkeyCapture:(id)sender {
  NSInteger index = [sender tag];
  if (index < 0 || index >= 3)
    return;
  if (_hotkeyMonitor) {
    [NSEvent removeMonitor:_hotkeyMonitor];
    _hotkeyMonitor = nil;
  }
  _capturingHotkey = index;
  _hotkeyBackup = _hotkeyButtons[static_cast<std::size_t>(index)].title;
  _hotkeyButtons[static_cast<std::size_t>(index)].title = @"请按快捷键…";
  set_status(_globalStatus, @"正在录制快捷键；Esc 取消。");
  __weak VocoTypeApplicationController *weakSelf = self;
  _hotkeyMonitor = [NSEvent addLocalMonitorForEventsMatchingMask:NSEventMaskKeyDown
                                                          handler:^NSEvent *(NSEvent *event) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self)
      return event;
    if (event.keyCode == 53) {
      self->_hotkeyButtons[static_cast<std::size_t>(self->_capturingHotkey)].title =
          (self->_hotkeyBackup ? self->_hotkeyBackup : @"F9");
      [NSEvent removeMonitor:self->_hotkeyMonitor];
      self->_hotkeyMonitor = nil;
      self->_capturingHotkey = -1;
      set_status(self->_globalStatus, @"已取消快捷键录制。");
      return nil;
    }
    NSString *captured = hotkey_from_event(event);
    if (!captured) {
      set_status(self->_globalStatus, @"仅支持 F1–F20 与修饰键组合。", true);
      return nil;
    }
    self->_hotkeyButtons[static_cast<std::size_t>(self->_capturingHotkey)].title = captured;
    [NSEvent removeMonitor:self->_hotkeyMonitor];
    self->_hotkeyMonitor = nil;
    self->_capturingHotkey = -1;
    set_status(self->_globalStatus,
               [@"已录制快捷键：" stringByAppendingString:captured]);
    return nil;
  }];
}

- (void)previewNormalization:(id)sender {
  (void)sender;
  if (![self persistSettings])
    return;
  NSString *source = trimmed(_normalizationInput.stringValue);
  if (source.length == 0) {
    set_status(_normalizationOutput, @"请输入测试文本。", true);
    return;
  }
  set_status(_normalizationOutput, @"正在调用 Core…");
  const std::string text = to_utf8(source);
  __weak VocoTypeApplicationController *weakSelf = self;
  run_async([text] { return settings::normalize_text(text); }, [weakSelf](Json result) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self)
      return;
    set_status(self->_normalizationOutput,
               to_ns(result.value("text", result.value("error", "失败"))),
               !result.value("success", false));
  });
}

- (void)addTerm:(id)sender {
  (void)sender;
  NSAlert *alert = [[NSAlert alloc] init];
  alert.messageText = @"新增术语";
  alert.informativeText =
      @"标准写法用于最终输出；别名可输入多项，按回车或逗号分隔。";
  [alert addButtonWithTitle:@"添加"];
  [alert addButtonWithTitle:@"取消"];

  NSTextField *canonical = text_field(@"例如 ChatGPT");
  NSTokenField *aliases = [[NSTokenField alloc] initWithFrame:NSZeroRect];
  aliases.placeholderString = @"例如 chat gbt，chat GPT";
  aliases.font = [NSFont systemFontOfSize:13.0];
  aliases.tokenizingCharacterSet =
      [NSCharacterSet characterSetWithCharactersInString:@",，\n"];
  [aliases.heightAnchor constraintEqualToConstant:32.0].active = YES;
  [aliases.widthAnchor constraintEqualToConstant:340.0].active = YES;
  NSButton *hotword = [NSButton checkboxWithTitle:@"作为 ASR 热词"
                                            target:nil action:nil];
  hotword.state = NSControlStateValueOn;
  NSButton *protect = [NSButton checkboxWithTitle:@"禁止 ITN 改写"
                                            target:nil action:nil];
  protect.state = NSControlStateValueOn;

  NSStackView *form = vertical_stack(7.0);
  [form addArrangedSubview:title_label(@"标准写法（canonical）", 12.0)];
  [form addArrangedSubview:canonical];
  [form addArrangedSubview:title_label(@"识别别名（aliases）", 12.0)];
  [form addArrangedSubview:aliases];
  NSStackView *options = horizontal_stack(18.0);
  [options addArrangedSubview:hotword];
  [options addArrangedSubview:protect];
  [options addArrangedSubview:flexible_spacer()];
  [form addArrangedSubview:options];
  NSView *container = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, 390, 165)];
  [container addSubview:form];
  [NSLayoutConstraint activateConstraints:@[
    [form.leadingAnchor constraintEqualToAnchor:container.leadingAnchor],
    [form.trailingAnchor constraintEqualToAnchor:container.trailingAnchor],
    [form.topAnchor constraintEqualToAnchor:container.topAnchor],
    [form.bottomAnchor constraintLessThanOrEqualToAnchor:container.bottomAnchor],
  ]];
  alert.accessoryView = container;
  alert.window.initialFirstResponder = canonical;
  if ([alert runModal] != NSAlertFirstButtonReturn)
    return;
  [alert.window makeFirstResponder:nil];

  NSString *canonicalValue = trimmed(canonical.stringValue);
  if (canonicalValue.length == 0) {
    set_status(_termsStatus, @"未添加：标准写法不能为空。", true);
    return;
  }
  const Json result = settings::append_term(
      to_utf8(canonicalValue), token_field_values(aliases),
      hotword.state == NSControlStateValueOn,
      protect.state == NSControlStateValueOn);
  const bool success = result.value("success", false);
  set_status(
      _termsStatus,
      success
          ? [NSString stringWithFormat:
                @"已添加“%@”：%lld 个别名；词典现有 %lld 个术语、%lld 个保护短语。下一次识别自动使用。",
                canonicalValue,
                static_cast<long long>(result.value("aliases", 0U)),
                static_cast<long long>(result.value("terms", 0U)),
                static_cast<long long>(result.value("protected_phrases", 0U))]
          : [@"未添加：" stringByAppendingString:
                to_ns(result.value("error", "unknown"))],
      !success);
}

- (void)addProtectedPhrase:(id)sender {
  (void)sender;
  NSAlert *alert = [[NSAlert alloc] init];
  alert.messageText = @"新增保护词";
  alert.informativeText = @"该短语将追加到顶层 protect 列表，防止 ITN 自动改写。";
  [alert addButtonWithTitle:@"添加"];
  [alert addButtonWithTitle:@"取消"];
  NSTextField *phrase = text_field(@"例如 GPT-5.6");
  NSView *container = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, 360, 42)];
  phrase.translatesAutoresizingMaskIntoConstraints = NO;
  [container addSubview:phrase];
  [NSLayoutConstraint activateConstraints:@[
    [phrase.leadingAnchor constraintEqualToAnchor:container.leadingAnchor],
    [phrase.trailingAnchor constraintEqualToAnchor:container.trailingAnchor],
    [phrase.topAnchor constraintEqualToAnchor:container.topAnchor constant:6.0],
  ]];
  alert.accessoryView = container;
  alert.window.initialFirstResponder = phrase;
  if ([alert runModal] != NSAlertFirstButtonReturn)
    return;
  [alert.window makeFirstResponder:nil];
  NSString *value = trimmed(phrase.stringValue);
  if (value.length == 0) {
    set_status(_termsStatus, @"未添加：保护词不能为空。", true);
    return;
  }
  const Json result = settings::append_protected_phrase(to_utf8(value));
  const bool success = result.value("success", false);
  set_status(
      _termsStatus,
      success
          ? [NSString stringWithFormat:
                @"已添加保护词“%@”；当前共有 %lld 个保护短语。下一次识别自动使用。",
                value,
                static_cast<long long>(result.value("protected_phrases", 0U))]
          : [@"未添加：" stringByAppendingString:
                to_ns(result.value("error", "unknown"))],
      !success);
}

- (void)importTerms:(id)sender {
  (void)sender;
  NSOpenPanel *panel = [NSOpenPanel openPanel];
  panel.title = @"导入用户词典";
  panel.prompt = @"验证并导入";
  panel.message = @"选择 YAML 词典文件。验证失败时不会覆盖现有词典。";
  panel.canChooseDirectories = NO;
  panel.canChooseFiles = YES;
  panel.allowsMultipleSelection = NO;
  NSMutableArray<UTType *> *types = [NSMutableArray array];
  UTType *yamlType = [UTType typeWithFilenameExtension:@"yaml"];
  UTType *ymlType = [UTType typeWithFilenameExtension:@"yml"];
  if (yamlType)
    [types addObject:yamlType];
  if (ymlType && ![types containsObject:ymlType])
    [types addObject:ymlType];
  panel.allowedContentTypes = types;
  if ([panel runModal] != NSModalResponseOK || panel.URLs.count == 0)
    return;
  const Json result =
      settings::import_terms(std::filesystem::path(to_utf8(panel.URL.path)));
  const bool success = result.value("success", false);
  set_status(
      _termsStatus,
      success
          ? [NSString stringWithFormat:
                @"导入成功：%lld 个术语、%lld 个保护短语；下一次识别自动热更新。",
                static_cast<long long>(result.value("terms", 0U)),
                static_cast<long long>(result.value("protected_phrases", 0U))]
          : [@"导入失败：" stringByAppendingString:
                to_ns(result.value("error", "unknown"))],
      !success);
}

- (void)reloadTerms:(id)sender {
  (void)sender;
  const Json result = settings::reload_terms();
  const bool success = result.value("success", false);
  set_status(
      _termsStatus,
      success
          ? [NSString stringWithFormat:
                @"热更新完成：已验证 %lld 个术语、%lld 个保护短语；运行中的 Core 将在下一次识别读取新版本。",
                static_cast<long long>(result.value("terms", 0U)),
                static_cast<long long>(result.value("protected_phrases", 0U))]
          : [@"热更新失败，继续使用上一次有效词典："
                stringByAppendingString:to_ns(result.value("error", "unknown"))],
      !success);
}

- (void)openTerms:(id)sender {
  (void)sender;
  if (!std::filesystem::is_regular_file(vocotype::desktop::terms_path()))
    (void)settings::reload_terms();
  std::filesystem::create_directories(vocotype::desktop::terms_path().parent_path());
  [NSWorkspace.sharedWorkspace activateFileViewerSelectingURLs:@[
    [NSURL fileURLWithPath:to_ns(vocotype::desktop::terms_path().string())]
  ]];
}

- (void)testAI:(id)sender {
  (void)sender;
  [self runAIConnectionTest];
}

- (void)aiEnabledChanged:(id)sender {
  (void)sender;
  ++_aiHealthGeneration;
  if (_slmEnabled.state == NSControlStateValueOn) {
    set_status(_aiStatus,
               @"AI 已启用；保存后可直接使用。连接测试是可选诊断。");
  } else {
    set_status(_aiStatus, @"AI 功能已关闭");
  }
}

- (void)aiConfigurationChanged:(NSNotification *)notification {
  (void)notification;
  ++_aiHealthGeneration;
  if (_slmEnabled.state == NSControlStateValueOn) {
    set_status(_aiStatus,
               @"连接设置已修改；保存后可直接使用，也可选择测试连接。");
  }
}

- (void)runAIConnectionTest {
  if (!_aiStatus || _slmEnabled.state != NSControlStateValueOn) {
    set_status(_aiStatus, @"请先启用 AI 功能，再执行可选连接测试。", true);
    return;
  }
  NSString *endpoint = trimmed(_endpoint.stringValue);
  NSString *model = trimmed(_model.stringValue);
  if (endpoint.length == 0 || model.length == 0) {
    set_status(_aiStatus, @"请先填写 API 地址和模型。", true);
    return;
  }
  if (![self persistSettings])
    return;

  const NSUInteger generation = ++_aiHealthGeneration;
  set_status(_aiStatus, @"正在发送真实测试请求；可能产生延迟或费用…");
  __weak VocoTypeApplicationController *weakSelf = self;
  run_async([] { return settings::test_ai(); }, [weakSelf, generation](Json result) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self || generation != self->_aiHealthGeneration)
      return;
    if (result.value("success", false)) {
      set_status(self->_aiStatus,
                 [@"连接测试成功：" stringByAppendingString:
                        to_ns(result.value("text", "服务已响应"))]);
    } else {
      set_status(self->_aiStatus,
                 [@"连接测试失败：" stringByAppendingString:
                        to_ns(result.value("error", "unknown"))], true);
    }
  });
}

- (void)ensureMicrophoneAccess:(void (^)(BOOL granted))completion {
  AVAudioApplicationRecordPermission permission =
      AVAudioApplication.sharedInstance.recordPermission;
  if (permission == AVAudioApplicationRecordPermissionGranted) {
    completion(YES);
    return;
  }
  if (permission == AVAudioApplicationRecordPermissionUndetermined) {
    set_status(_playgroundStatus, @"正在请求 macOS 麦克风权限…");
    [AVAudioApplication requestRecordPermissionWithCompletionHandler:
        ^(BOOL granted) {
      dispatch_async(dispatch_get_main_queue(), ^{
        [self refreshAudioDevices:nil];
        completion(granted);
      });
    }];
    return;
  }
  completion(NO);
}

- (void)requestMicrophonePermission:(id)sender {
  (void)sender;
  [self ensureMicrophoneAccess:^(BOOL granted) {
    if (granted) {
      set_status(self->_playgroundStatus, @"麦克风权限已允许，可以开始录音。");
      return;
    }
    set_status(self->_playgroundStatus,
               @"麦克风权限未开启。请在系统设置中允许 VoCoType-linux 访问麦克风。",
               true);
    [self openMicrophonePrivacy:nil];
  }];
}

- (void)openMicrophonePrivacy:(id)sender {
  (void)sender;
  NSURL *url = [NSURL URLWithString:
      @"x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"];
  if (url)
    [NSWorkspace.sharedWorkspace openURL:url];
}

- (void)recordPlayground:(id)sender {
  (void)sender;
  [self ensureMicrophoneAccess:^(BOOL granted) {
    if (!granted) {
      set_status(self->_playgroundStatus,
                 @"无法录音：麦克风权限未开启。点击“麦克风权限”前往系统设置。",
                 true);
      return;
    }
    [self startPlaygroundRecording];
  }];
}

- (void)startPlaygroundRecording {
  if (![self persistSettings])
    return;
  [_waveform clearWaveform];
  set_status(_playgroundStatus, @"正在录制 3 秒…请对着所选麦克风说话。");
  __weak VocoTypeApplicationController *weakSelf = self;
  run_async([weakSelf] {
    return settings::capture_recording(3000, [weakSelf](double minimum,
                                                        double maximum) {
      dispatch_async(dispatch_get_main_queue(), ^{
        VocoTypeApplicationController *self = weakSelf;
        if (!self)
          return;
        [self->_waveform appendMinimum:minimum maximum:maximum];
      });
    });
  }, [weakSelf](Json result) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self)
      return;
    if (result.value("success", false)) {
      if (!self->_lastRecording.empty())
        std::filesystem::remove(self->_lastRecording);
      self->_lastRecording = result.value("path", "");
      set_status(self->_playgroundStatus,
                 [NSString stringWithFormat:@"已录制：%@，%d Hz，%zu 帧",
                      to_ns(result.value("device", "")),
                      result.value("sample_rate", 0),
                      result.value("frames", static_cast<std::size_t>(0))]);
    } else {
      set_status(self->_playgroundStatus,
                 [@"录音失败：" stringByAppendingString:
                        to_ns(result.value("error", "unknown"))], true);
    }
  });
}

- (void)playPlayground:(id)sender {
  (void)sender;
  if (_lastRecording.empty()) {
    set_status(_playgroundStatus, @"请先录音。", true);
    return;
  }
  int outputId = -1;
  NSInteger selected = _audioOutput.indexOfSelectedItem;
  if (selected >= 0 && static_cast<std::size_t>(selected) < _outputDevices.size())
    outputId = _outputDevices[static_cast<std::size_t>(selected)].id;
  const auto path = _lastRecording;
  set_status(_playgroundStatus, @"正在回放…");
  __weak VocoTypeApplicationController *weakSelf = self;
  run_async([path, outputId] { return settings::play_recording(path, outputId); },
            [weakSelf](Json result) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self)
      return;
    set_status(self->_playgroundStatus,
               result.value("success", false)
                   ? [@"已回放到：" stringByAppendingString:
                          to_ns(result.value("device", ""))]
                   : [@"回放失败：" stringByAppendingString:
                          to_ns(result.value("error", "unknown"))],
               !result.value("success", false));
  });
}

- (void)transcribePlayground:(id)sender {
  (void)sender;
  if (_lastRecording.empty()) {
    set_status(_playgroundStatus, @"请先录音。", true);
    return;
  }
  const auto path = _lastRecording;
  set_status(_playgroundStatus, @"正在真实转录…");
  __weak VocoTypeApplicationController *weakSelf = self;
  run_async([path] { return settings::transcribe_recording(path); },
            [weakSelf](Json result) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self)
      return;
    set_text(self->_transcriptionResult,
             result.value("text", result.value("error", "失败")));
    set_status(self->_playgroundStatus,
               result.value("success", false) ? @"识别完成" : @"识别失败",
               !result.value("success", false));
  });
}

- (void)polishPlayground:(id)sender {
  (void)sender;
  if (![self persistSettings])
    return;
  const std::string source = text_view_text(_polishSource);
  if (source.empty()) {
    set_status(_polishStatus, @"请输入待润色文本。", true);
    return;
  }
  set_status(_polishStatus, @"正在调用 AI 润色…");
  __weak VocoTypeApplicationController *weakSelf = self;
  run_async([source] { return settings::polish_text(source); },
            [weakSelf](Json result) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self)
      return;
    set_text(self->_polishResult,
             result.value("text", result.value("error", "失败")));
    set_status(self->_polishStatus,
               result.value("success", false) ? @"润色完成" : @"润色失败",
               !result.value("success", false));
  });
}


- (void)applyEditExample:(id)sender {
  NSInteger index = [sender tag];
  if (index < 0 || index >= static_cast<NSInteger>(_editExamples.count))
    return;
  NSDictionary *example = _editExamples[static_cast<NSUInteger>(index)];
  set_text(_editSource, to_utf8(example[@"source"] ? example[@"source"] : @""));
  NSString *instruction = example[@"instruction"] ? example[@"instruction"] : @"";
  set_status(_editStatus,
             [@"建议口述：" stringByAppendingString:instruction]);
  [_settingsWindow makeFirstResponder:_editSource];
  [_editSource setSelectedRange:NSMakeRange(_editSource.string.length, 0)];
}

- (void)toggleDoctorRaw:(id)sender {
  (void)sender;
  if (!_doctorRawContainer || !_doctorRawToggle)
    return;
  _doctorRawContainer.hidden = !_doctorRawContainer.hidden;
  _doctorRawToggle.title = _doctorRawContainer.hidden
                               ? @"显示原始 Doctor 输出"
                               : @"隐藏原始 Doctor 输出";
}

- (void)runDoctor:(id)sender {
  (void)sender;
  set_status(_doctorSummary, @"正在运行 Doctor…");
  const std::string version = to_utf8(app_version());
  __weak VocoTypeApplicationController *weakSelf = self;
  run_async([version] { return settings::run_doctor(version); },
            [weakSelf](Json result) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self)
      return;
    self->_lastDoctorReport = result.value("report", "");
    set_text(self->_doctorOutput, self->_lastDoctorReport);
    for (NSView *view in self->_doctorChecks.arrangedSubviews)
      [self->_doctorChecks removeArrangedSubview:view], [view removeFromSuperview];
    NSInteger passed = 0;
    NSInteger failed = 0;
    for (const auto &check : result.value("checks", Json::array())) {
      bool success = check.value("status", "fail") == "pass";
      success ? ++passed : ++failed;
      NSStackView *card = nil;
      NSBox *box = card_with_stack(&card);
      NSTextField *state = plain_label(success ? @"通过" : @"需要处理");
      state.textColor = success ? NSColor.systemGreenColor : NSColor.systemRedColor;
      [card addArrangedSubview:settings_row(
          to_ns(check.value("title", "")),
          to_ns(check.value("details", "")), state)];
      [self->_doctorChecks addArrangedSubview:box];
      [box.widthAnchor constraintEqualToAnchor:self->_doctorChecks.widthAnchor].active = YES;
    }
    NSString *summary = failed == 0
        ? [NSString stringWithFormat:@"%ld 项检查全部通过", (long)passed]
        : [NSString stringWithFormat:@"%ld 项通过，%ld 项需要处理",
              (long)passed, (long)failed];
    set_status(self->_doctorSummary, summary, failed != 0);
    set_status(self->_overviewDoctorStatus, summary, failed != 0);
  });
}

- (void)queryLatestRelease:(id)sender {
  (void)sender;
  set_status(_versionStatus, @"正在查询 GitHub release…");
  const std::string version = to_utf8(app_version());
  __weak VocoTypeApplicationController *weakSelf = self;
  run_async([version] { return settings::query_latest_release(version); },
            [weakSelf](Json result) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self)
      return;
    set_status(self->_versionStatus,
               result.value("success", false)
                   ? [NSString stringWithFormat:@"当前：%@；最新：%@；发布：%@",
                        app_version(), to_ns(result.value("latest", "unknown")),
                        to_ns(result.value("published_at", ""))]
                   : [@"版本查询失败：" stringByAppendingString:
                          to_ns(result.value("error", "unknown"))],
               !result.value("success", false));
  });
}

- (void)exportSupportBundle:(id)sender {
  (void)sender;
  if (_lastDoctorReport.empty()) {
    Json doctor = settings::run_doctor(to_utf8(app_version()));
    _lastDoctorReport = doctor.value("report", "");
    set_text(_doctorOutput, _lastDoctorReport);
  }
  set_status(_supportStatus, @"正在生成脱敏支持包…");
  const std::string doctor = _lastDoctorReport;
  const std::string version = to_utf8(app_version());
  __weak VocoTypeApplicationController *weakSelf = self;
  run_async([doctor, version] {
    return settings::create_support_bundle(doctor, version);
  }, [weakSelf](Json result) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self)
      return;
    set_status(self->_supportStatus,
               result.value("success", false)
                   ? [@"支持包已生成：" stringByAppendingString:
                          to_ns(result.value("path", ""))]
                   : [@"支持包生成失败：" stringByAppendingString:
                          to_ns(result.value("error", "unknown"))],
               !result.value("success", false));
  });
}

- (void)openSupportDirectory:(id)sender {
  (void)sender;
  [NSWorkspace.sharedWorkspace openURL:
      [NSURL fileURLWithPath:to_ns(settings::support_directory().string())]];
}

- (void)openGitHubIssue:(id)sender {
  (void)sender;
  std::string body;
  if (_feedbackMessage && !text_view_text(_feedbackMessage).empty()) {
    body = "类别：" + std::to_string(_feedbackCategory.indexOfSelectedItem) +
           "\n联系方式：" + to_utf8(_feedbackContact.stringValue) + "\n\n" +
           text_view_text(_feedbackMessage);
    if (_feedbackIncludeDoctor.state == NSControlStateValueOn) {
      if (_lastDoctorReport.empty())
        _lastDoctorReport =
            settings::run_doctor(to_utf8(app_version())).value("report", "");
      body += "\n\n<details><summary>VoCoType Doctor</summary>\n\n```text\n" +
              _lastDoctorReport + "\n```\n</details>";
    }
  } else {
    body = _lastDoctorReport.empty()
               ? "请描述问题、复现步骤和期望结果。"
               : "<details><summary>VoCoType Doctor</summary>\n\n```text\n" +
                     _lastDoctorReport + "\n```\n</details>";
  }
  NSString *url = [NSString stringWithFormat:
      @"https://github.com/LeonardNJU/VocoType-linux/issues/new?labels=feedback&title=%@&body=%@",
      percent_encode(@"[Feedback] "), percent_encode(to_ns(body))];
  [NSWorkspace.sharedWorkspace openURL:[NSURL URLWithString:url]];
}

- (void)sendFeedback:(id)sender {
  (void)sender;
  const std::string message = text_view_text(_feedbackMessage);
  if (message.empty()) {
    set_status(_feedbackStatus, @"请先填写反馈内容。", true);
    return;
  }
  NSArray<NSString *> *categories = @[
    @"bug", @"installation", @"compatibility", @"usability", @"feature", @"other"
  ];
  NSInteger index = _feedbackCategory.indexOfSelectedItem;
  const std::string category =
      to_utf8(categories[static_cast<NSUInteger>(std::clamp<NSInteger>(index, 0, categories.count - 1))]);
  const std::string contact = to_utf8(_feedbackContact.stringValue);
  const std::string endpoint = to_utf8(_feedbackEndpoint.stringValue);
  const bool includeDoctor = _feedbackIncludeDoctor.state == NSControlStateValueOn;
  const bool includeBundle = _feedbackIncludeBundle.state == NSControlStateValueOn;
  const std::string version = to_utf8(app_version());
  set_status(_feedbackStatus, @"正在发送反馈…");
  __weak VocoTypeApplicationController *weakSelf = self;
  run_async([=] {
    std::string doctor;
    std::filesystem::path bundle;
    if (includeDoctor || includeBundle)
      doctor = settings::run_doctor(version).value("report", "");
    if (includeBundle) {
      Json generated = settings::create_support_bundle(doctor, version);
      if (!generated.value("success", false))
        return generated;
      bundle = generated.value("path", "");
    }
    settings::FeedbackRequest request{endpoint, category, contact, message,
                                      includeDoctor ? doctor : "", bundle,
                                      version};
    Json result = settings::submit_feedback(request);
    if (!bundle.empty())
      result["bundle"] = bundle.string();
    return result;
  }, [weakSelf](Json result) {
    VocoTypeApplicationController *self = weakSelf;
    if (!self)
      return;
    set_status(self->_feedbackStatus,
               result.value("success", false)
                   ? [@"反馈已发送" stringByAppendingString:
                          result.value("bundle", "").empty()
                              ? @""
                              : [@"；支持包保留于 " stringByAppendingString:
                                     to_ns(result.value("bundle", ""))]]
                   : [@"反馈发送失败：" stringByAppendingString:
                          to_ns(result.value("error", "unknown"))],
               !result.value("success", false));
  });
}

- (void)feedbackEndpointToggled:(id)sender {
  (void)sender;
  _feedbackEndpoint.enabled =
      _feedbackCustomEndpoint.state == NSControlStateValueOn;
  if (!_feedbackEndpoint.enabled)
    _feedbackEndpoint.stringValue =
        @"https://feedback.vocotype-linux.lsamc.website/v1/feedback";
}

- (void)openConfiguration:(id)sender {
  (void)sender;
  const auto path = vocotype::desktop::config_dir();
  std::filesystem::create_directories(path);
  [NSWorkspace.sharedWorkspace openURL:[NSURL fileURLWithPath:to_ns(path.string())]];
}

- (void)quit:(id)sender {
  [NSApp terminate:sender];
}

@end
