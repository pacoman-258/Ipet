#import <AppKit/AppKit.h>
#import <UserNotifications/UserNotifications.h>

@interface IpetNotificationDelegate : NSObject <NSApplicationDelegate, UNUserNotificationCenterDelegate>
@property(nonatomic, copy) NSString *proposalID;
@property(nonatomic) BOOL finished;
@property(nonatomic, strong) dispatch_source_t terminationSource;
@end

@implementation IpetNotificationDelegate
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
    NSData *input = [[NSFileHandle fileHandleWithStandardInput] readDataToEndOfFile];
    NSDictionary *request = [NSJSONSerialization JSONObjectWithData:input options:0 error:nil];
    self.proposalID = [request[@"proposal_id"] isKindOfClass:NSString.class] ? request[@"proposal_id"] : @"";
    if (self.proposalID.length == 0) { [self finish:NO reason:@"invalid_request"]; return; }

    UNUserNotificationCenter *center = UNUserNotificationCenter.currentNotificationCenter;
    signal(SIGTERM, SIG_IGN);
    self.terminationSource = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, SIGTERM, 0, dispatch_get_main_queue());
    dispatch_source_set_event_handler(self.terminationSource, ^{
        NSString *identifier = [@"ipet-approval-" stringByAppendingString:self.proposalID];
        [center removePendingNotificationRequestsWithIdentifiers:@[identifier]];
        [center removeDeliveredNotificationsWithIdentifiers:@[identifier]];
        [self finish:NO reason:@"cancelled_by_stop"];
    });
    dispatch_resume(self.terminationSource);
    center.delegate = self;
    UNNotificationAction *approve = [UNNotificationAction actionWithIdentifier:@"IPET_APPROVE" title:@"✅ 批准此操作" options:UNNotificationActionOptionNone];
    UNNotificationAction *reject = [UNNotificationAction actionWithIdentifier:@"IPET_REJECT" title:@"⛔ 拒绝此操作" options:UNNotificationActionOptionDestructive];
    UNNotificationCategory *category = [UNNotificationCategory categoryWithIdentifier:@"IPET_HUMAN_OPS_APPROVAL" actions:@[approve, reject] intentIdentifiers:@[] options:UNNotificationCategoryOptionCustomDismissAction];
    [center setNotificationCategories:[NSSet setWithObject:category]];
    [center requestAuthorizationWithOptions:(UNAuthorizationOptionAlert | UNAuthorizationOptionSound) completionHandler:^(BOOL granted, NSError *error) {
        if (!granted || error) { [self finish:NO reason:@"notification_permission_denied"]; return; }
        UNMutableNotificationContent *content = [UNMutableNotificationContent new];
        content.title = [request[@"title"] isKindOfClass:NSString.class] ? request[@"title"] : @"⚠️ Ipet 操作审批";
        content.subtitle = @"请明确选择：批准此操作 或 拒绝此操作";
        NSString *body = [request[@"body"] isKindOfClass:NSString.class] ? request[@"body"] : @"是否批准这一步操作？";
        content.body = [body stringByAppendingString:@"\n\n请使用通知中的两个操作按钮作出决定。"];
        content.sound = UNNotificationSound.defaultSound;
        content.categoryIdentifier = @"IPET_HUMAN_OPS_APPROVAL";
        content.threadIdentifier = @"ipet-human-ops-approvals";
        if (@available(macOS 12.0, *)) content.interruptionLevel = UNNotificationInterruptionLevelTimeSensitive;
        content.userInfo = @{@"proposal_id": self.proposalID};
        NSString *identifier = [@"ipet-approval-" stringByAppendingString:self.proposalID];
        [center addNotificationRequest:[UNNotificationRequest requestWithIdentifier:identifier content:content trigger:nil] withCompletionHandler:^(NSError *addError) {
            if (addError) [self finish:NO reason:@"notification_delivery_failed"];
        }];
        NSTimeInterval timeout = MAX(30, MIN([request[@"timeout_sec"] doubleValue], 600));
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(timeout * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{ [self finish:NO reason:@"timed_out"]; });
    }];
}

- (void)userNotificationCenter:(UNUserNotificationCenter *)center willPresentNotification:(UNNotification *)notification withCompletionHandler:(void (^)(UNNotificationPresentationOptions))completionHandler {
    completionHandler(UNNotificationPresentationOptionBanner | UNNotificationPresentationOptionList | UNNotificationPresentationOptionSound);
}

- (void)userNotificationCenter:(UNUserNotificationCenter *)center didReceiveNotificationResponse:(UNNotificationResponse *)response withCompletionHandler:(void (^)(void))completionHandler {
    NSString *action = response.actionIdentifier;
    if ([action isEqualToString:@"IPET_APPROVE"]) [self finish:YES reason:@"approved"];
    else if ([action isEqualToString:@"IPET_REJECT"] || [action isEqualToString:UNNotificationDismissActionIdentifier]) [self finish:NO reason:@"rejected"];
    else [self finish:NO reason:@"opened_without_decision"];
    completionHandler();
}

- (void)finish:(BOOL)approved reason:(NSString *)reason {
    dispatch_async(dispatch_get_main_queue(), ^{
        if (self.finished) return;
        self.finished = YES;
        NSDictionary *payload = @{@"proposal_id": self.proposalID ?: @"", @"approved": @(approved), @"reason": reason ?: @"unknown"};
        NSData *data = [NSJSONSerialization dataWithJSONObject:payload options:0 error:nil];
        fwrite(data.bytes, 1, data.length, stdout); fwrite("\n", 1, 1, stdout); fflush(stdout);
        [NSApp terminate:nil];
    });
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *app = NSApplication.sharedApplication;
        IpetNotificationDelegate *delegate = [IpetNotificationDelegate new];
        app.delegate = delegate;
        [app run];
    }
    return 0;
}
