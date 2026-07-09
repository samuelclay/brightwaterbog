#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <objc/message.h>
#import <dlfcn.h>

typedef struct {
    const char *outPath;
    int dpi;
    int brightness;
    int contrast;
    int saturation;
    int unsharp;
    int descreen;
} ScanConfig;

static ScanConfig defaultScanConfig(void) {
    ScanConfig config;
    config.outPath = nil;
    config.dpi = 600;
    config.brightness = 0;
    config.contrast = 0;
    config.saturation = 0;
    config.unsharp = 0;
    config.descreen = 0;
    return config;
}

static void dumpClass(NSString *name) {
    Class cls = NSClassFromString(name);
    if (!cls) {
        printf("\n== %s: not found ==\n", name.UTF8String);
        return;
    }

    printf("\n== %s ==\n", name.UTF8String);
    unsigned int count = 0;
    Method *methods = class_copyMethodList(cls, &count);
    for (unsigned int i = 0; i < count; i++) {
        SEL sel = method_getName(methods[i]);
        const char *types = method_getTypeEncoding(methods[i]);
        printf("- %s %s\n", sel_getName(sel), types ? types : "");
    }
    free(methods);

    Class meta = object_getClass(cls);
    methods = class_copyMethodList(meta, &count);
    for (unsigned int i = 0; i < count; i++) {
        SEL sel = method_getName(methods[i]);
        const char *types = method_getTypeEncoding(methods[i]);
        printf("+ %s %s\n", sel_getName(sel), types ? types : "");
    }
    free(methods);
}

static id sendId(id target, SEL selector) {
    return ((id (*)(id, SEL))objc_msgSend)(target, selector);
}

static id sendIdChar(id target, SEL selector, char value) {
    return ((id (*)(id, SEL, char))objc_msgSend)(target, selector, value);
}

static short sendShort(id target, SEL selector) {
    return ((short (*)(id, SEL))objc_msgSend)(target, selector);
}

static short sendShortId(id target, SEL selector, id value) {
    return ((short (*)(id, SEL, id))objc_msgSend)(target, selector, value);
}

static int sendInt(id target, SEL selector) {
    return ((int (*)(id, SEL))objc_msgSend)(target, selector);
}

static id sendIdId(id target, SEL selector, id value) {
    return ((id (*)(id, SEL, id))objc_msgSend)(target, selector, value);
}

static BOOL sendBool(id target, SEL selector) {
    return ((BOOL (*)(id, SEL))objc_msgSend)(target, selector);
}

static BOOL sendBoolIdId(id target, SEL selector, id first, id second) {
    return ((BOOL (*)(id, SEL, id, id))objc_msgSend)(target, selector, first, second);
}

static unsigned int sendUInt(id target, SEL selector) {
    return ((unsigned int (*)(id, SEL))objc_msgSend)(target, selector);
}

static float sendFloat(id target, SEL selector) {
    return ((float (*)(id, SEL))objc_msgSend)(target, selector);
}

static void dryInit(BOOL shouldOpen) {
    @try {
        Class driverClass = NSClassFromString(@"SDIScannerDriver");
        id driver = sendIdChar(sendId(driverClass, @selector(alloc)), @selector(initWithMode:), 2);
        printf("driver=%s\n", driver ? NSStringFromClass([driver class]).UTF8String : "(nil)");
        printf("setup=%d\n", sendShort(driver, @selector(setup)));
        if (shouldOpen) {
            ((void (*)(id, SEL))objc_msgSend)(driver, @selector(hideUI));
            printf("open=%d\n", sendShort(driver, @selector(open)));
        }

        NSArray<NSString *> *keys = @[
            @"ESCurrentMode",
            @"ESPhotoMode",
            @"ESPhotoResolution",
            @"ESPhotoBrightness",
            @"ESPhotoContrast",
            @"ESPhotoSaturation",
            @"ESPhotoAlwaysAutoExposure",
            @"ESPhotoAutoExposureLevel",
            @"ESPhotoBacklightCorrection",
            @"ESPhotoColorRestoration",
            @"ESPhotoColorManagement",
            @"ESPhotoSourceProfilePath",
            @"ESPhotoTargetProfilePath",
            @"ESPhotoGamma",
            @"ESPhotoToneCorrectionPreset",
            @"ESPhotoToneCorrection",
            @"ESPhotoGrayBalance",
            @"ESPhotoColorBalanceCR",
            @"ESPhotoColorBalanceMG",
            @"ESPhotoColorBalanceYB",
            @"ESPhotoUnsharp",
            @"ESPhotoDescreening",
            @"ESPhotoHistogramParamTable",
            @"ESImageFileFormat",
            @"ESSaveFileLocation",
            @"ESFileNamePrefix"
        ];

        id dataManager = sendId(driver, @selector(dataManager));
        printf("driverDataManager=%s\n", dataManager ? NSStringFromClass([dataManager class]).UTF8String : "(nil)");

        Class dataManagerClass = NSClassFromString(@"EPSDataManager");
        id explicitDataManager = sendIdId(sendId(dataManagerClass, @selector(alloc)), @selector(initWithScannerID:), @"ES0282");
        printf("explicitDataManager=%s\n", explicitDataManager ? NSStringFromClass([explicitDataManager class]).UTF8String : "(nil)");
        printf("explicitSetup=%d\n", sendInt(explicitDataManager, @selector(setup)));
        if ([explicitDataManager respondsToSelector:@selector(restoreLastUsedPreset)]) {
            ((void (*)(id, SEL))objc_msgSend)(explicitDataManager, @selector(restoreLastUsedPreset));
        }

        for (NSString *key in keys) {
            id value = nil;
            @try {
                value = sendIdId(explicitDataManager ?: dataManager ?: driver, @selector(currentDataValueForKey:), key);
                if (!value || value == (id)[NSNull null]) {
                    value = sendIdId(explicitDataManager ?: dataManager ?: driver, @selector(currentUIDataForKey:), key);
                }
            } @catch (NSException *exception) {
                @try {
                    value = sendIdId(driver, @selector(valueForKey:), key);
                } @catch (NSException *inner) {
                    value = [NSString stringWithFormat:@"<%@>", inner.reason ?: inner.name];
                }
            }
            printf("%s=%s\n", key.UTF8String, [[value description] UTF8String]);
        }

        if (shouldOpen) {
            printf("close=%d\n", sendShort(driver, @selector(close)));
        } else {
            printf("close=%d\n", sendShort(driver, @selector(close)));
        }
    } @catch (NSException *exception) {
        fprintf(stderr, "exception: %s: %s\n", exception.name.UTF8String, exception.reason.UTF8String);
    }
}

static void runLoopUntilDone(id driver) {
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:180.0];
    while ([deadline timeIntervalSinceNow] > 0) {
        [[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.25]];
        if ([driver respondsToSelector:@selector(hasNextImageData)] && sendBool(driver, @selector(hasNextImageData))) {
            break;
        }
        id sequencer = [driver respondsToSelector:@selector(scanSequencer)] ? sendId(driver, @selector(scanSequencer)) : nil;
        if (sequencer && [sequencer respondsToSelector:@selector(hasNextImageData)] && sendBool(sequencer, @selector(hasNextImageData))) {
            break;
        }
        id manager = [driver respondsToSelector:@selector(imageManager)] ? sendId(driver, @selector(imageManager)) : nil;
        if (manager && [manager respondsToSelector:@selector(count)] && sendUInt(manager, @selector(count)) > 0) {
            break;
        }
        if (![driver respondsToSelector:@selector(isScanning)] || !sendBool(driver, @selector(isScanning))) {
            break;
        }
    }
}

static id firstUSBDevice(void) {
    Class finderClass = NSClassFromString(@"SDIDeviceFinder");
    id finder = sendId(finderClass, @selector(alloc));
    finder = sendId(finder, @selector(init));
    if ([finder respondsToSelector:@selector(setTimeout:)]) {
        ((void (*)(id, SEL, long long))objc_msgSend)(finder, @selector(setTimeout:), 5LL);
    }
    if ([finder respondsToSelector:@selector(discoverUSBDevices)]) {
        ((void (*)(id, SEL))objc_msgSend)(finder, @selector(discoverUSBDevices));
    }
    if ([finder respondsToSelector:@selector(startDiscovery)]) {
        sendShort(finder, @selector(startDiscovery));
    }
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:6.0];
    while ([deadline timeIntervalSinceNow] > 0) {
        [[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.25]];
        id devices = sendId(finder, @selector(devices));
        if ([devices respondsToSelector:@selector(count)] && [devices count] > 0) {
            if ([finder respondsToSelector:@selector(stopDiscovery)]) {
                sendShort(finder, @selector(stopDiscovery));
            }
            return [devices objectAtIndex:0];
        }
    }
    if ([finder respondsToSelector:@selector(stopDiscovery)]) {
        sendShort(finder, @selector(stopDiscovery));
    }
    return nil;
}

static id savedDataManager(void) {
    Class dataManagerClass = NSClassFromString(@"EPSDataManager");
    id manager = sendIdId(sendId(dataManagerClass, @selector(alloc)), @selector(initWithScannerID:), @"ES0282");
    sendInt(manager, @selector(setup));
    if ([manager respondsToSelector:@selector(restoreLastUsedPreset)]) {
        ((void (*)(id, SEL))objc_msgSend)(manager, @selector(restoreLastUsedPreset));
    }
    return manager;
}

static id dataValue(id manager, NSString *key) {
    id value = nil;
    @try {
        value = sendIdId(manager, @selector(currentDataValueForKey:), key);
        if (!value || value == (id)[NSNull null]) {
            value = sendIdId(manager, @selector(currentUIDataForKey:), key);
        }
    } @catch (NSException *exception) {
        value = nil;
    }
    return value;
}

static NSDictionary *lastUsedTicket(void) {
    NSString *path = [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Preferences/EPSON/Epson Scan 2/ES0282/EpsonScan2.plist"];
    NSDictionary *plist = [NSDictionary dictionaryWithContentsOfFile:path];
    NSDictionary *presets = [plist objectForKey:@"Preset"];
    NSDictionary *lastUsed = [presets objectForKey:@"1"];
    NSDictionary *ticket = [lastUsed objectForKey:@"Ticket"];
    return ticket;
}

static void applySavedProfile(id driver) {
    id source = savedDataManager();
    id target = sendId(driver, @selector(dataManager));
    NSArray<NSString *> *keys = @[
        @"ESCurrentMode",
        @"ESPhotoMode",
        @"ESPhotoResolution",
        @"ESPhotoBrightness",
        @"ESPhotoContrast",
        @"ESPhotoSaturation",
        @"ESPhotoAlwaysAutoExposure",
        @"ESPhotoAutoExposureLevel",
        @"ESPhotoBacklightCorrection",
        @"ESPhotoColorRestoration",
        @"ESPhotoColorManagement",
        @"ESPhotoSourceProfilePath",
        @"ESPhotoTargetProfilePath",
        @"ESPhotoGamma",
        @"ESPhotoToneCorrectionPreset",
        @"ESPhotoToneCorrection",
        @"ESPhotoGrayBalance",
        @"ESPhotoColorBalanceCR",
        @"ESPhotoColorBalanceMG",
        @"ESPhotoColorBalanceYB",
        @"ESPhotoUnsharp",
        @"ESPhotoDescreening",
        @"ESPhotoImageType",
        @"ESPhotoHistogramParamTable",
        @"ESPhotoShadowCurve",
        @"ESPhotoHighlightCurve",
        @"ESImageFileFormat",
        @"ESSaveFileLocation",
        @"ESFileNamePrefix",
        @"ESSkipPhotoAutoCropping"
    ];

    printf("profileTarget=%s\n", target ? NSStringFromClass([target class]).UTF8String : "(nil)");
    for (NSString *key in keys) {
        id managerValue = dataValue(source, key);
        id plistValue = [lastUsedTicket() objectForKey:key];
        BOOL plistValueIsScalar = plistValue
            && ![plistValue isKindOfClass:[NSDictionary class]]
            && ![plistValue isKindOfClass:[NSArray class]]
            && ![plistValue isKindOfClass:[NSData class]];
        id value = plistValueIsScalar ? plistValue : managerValue;
        if (value && target && [target respondsToSelector:@selector(setDataForKey:value:)]) {
            sendBoolIdId(target, @selector(setDataForKey:value:), key, value);
        } else if (value && [driver respondsToSelector:@selector(setValue:forKey:)]) {
            ((void (*)(id, SEL, id, id))objc_msgSend)(driver, @selector(setValue:forKey:), value, key);
        }
        id active = target ? dataValue(target, key) : nil;
        printf("profile %s saved=%s active=%s\n",
               key.UTF8String,
               [[value description] UTF8String],
               [[active description] UTF8String]);
        if (plistValue && managerValue && !plistValueIsScalar) {
            printf("profile %s plistObjectIgnored=%s manager=%s\n",
                   key.UTF8String,
                   [[plistValue description] UTF8String],
                   [[managerValue description] UTF8String]);
        } else if (plistValue && managerValue && ![[plistValue description] isEqualToString:[managerValue description]]) {
            printf("profile %s plist=%s manager=%s\n",
                   key.UTF8String,
                   [[plistValue description] UTF8String],
                   [[managerValue description] UTF8String]);
        }
    }
}

static void forceData(id manager, NSString *key, id value) {
    if (!manager || !value) {
        return;
    }
    if ([manager respondsToSelector:@selector(setDataForKey:value:)]) {
        sendBoolIdId(manager, @selector(setDataForKey:value:), key, value);
    }
    printf("force %s=%s active=%s\n", key.UTF8String, [[value description] UTF8String], [[dataValue(manager, key) description] UTF8String]);
}

static void forcePhotoColorProfile(id driver, const ScanConfig *config) {
    id manager = sendId(driver, @selector(dataManager));
    forceData(manager, @"ESCurrentMode", @2);
    forceData(manager, @"ESPhotoMode", @1);
    forceData(manager, @"ESPhotoImageType", @4);
    forceData(manager, @"ESPhotoResolution", @(config->dpi));
    forceData(manager, @"ESPhotoBrightness", @(config->brightness));
    forceData(manager, @"ESPhotoContrast", @(config->contrast));
    forceData(manager, @"ESPhotoSaturation", @(config->saturation));
    forceData(manager, @"ESPhotoUnsharp", @(config->unsharp));
    forceData(manager, @"ESPhotoDescreening", @(config->descreen));
}

static NSString *destinationPath(NSString *requestedPath, int index) {
    if (requestedPath.length) {
        if (index <= 1) {
            return requestedPath;
        }
        NSString *dir = [requestedPath stringByDeletingLastPathComponent];
        NSString *base = [[requestedPath lastPathComponent] stringByDeletingPathExtension];
        NSString *ext = [requestedPath pathExtension];
        NSString *name = ext.length
            ? [NSString stringWithFormat:@"%@_%03d.%@", base, index, ext]
            : [NSString stringWithFormat:@"%@_%03d", base, index];
        return [dir stringByAppendingPathComponent:name];
    }

    NSDateFormatter *formatter = [[NSDateFormatter alloc] init];
    formatter.dateFormat = @"yyyyMMdd_HHmmss";
    NSString *stamp = [formatter stringFromDate:[NSDate date]];
    return [NSString stringWithFormat:@"captures/epson2_no_ui_%@_%03d.jpg", stamp, index];
}

static NSString *captureCopyPathForImageData(id imageData, int index, const char *requestedPathArg) {
    if (!imageData || ![imageData respondsToSelector:@selector(path)]) {
        return nil;
    }
    NSString *path = sendId(imageData, @selector(path));
    if (!path.length) {
        return nil;
    }
    NSString *requestedPath = requestedPathArg ? [NSString stringWithUTF8String:requestedPathArg] : nil;
    NSString *dest = destinationPath(requestedPath, index);
    NSError *error = nil;
    NSString *dir = [dest stringByDeletingLastPathComponent];
    if (dir.length) {
        [[NSFileManager defaultManager] createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:nil];
    }
    [[NSFileManager defaultManager] removeItemAtPath:dest error:nil];
    if (![[NSFileManager defaultManager] copyItemAtPath:path toPath:dest error:&error]) {
        fprintf(stderr, "copy failed from %s to %s: %s\n", path.UTF8String, dest.UTF8String, error.localizedDescription.UTF8String);
        return path;
    }
    return dest;
}

static int scanNoUI(const ScanConfig *config) {
    @try {
        Class driverClass = NSClassFromString(@"SDIScannerDriver");
        id driver = sendIdChar(sendId(driverClass, @selector(alloc)), @selector(initWithMode:), 2);
        printf("driver=%s\n", driver ? NSStringFromClass([driver class]).UTF8String : "(nil)");
        printf("setup=%d\n", sendShort(driver, @selector(setup)));
        id device = firstUSBDevice();
        printf("device=%s\n", [[device description] UTF8String]);
        if (device) {
            printf("changeDevice=%d\n", sendShortId(driver, @selector(changeDevice:), device));
        }
        applySavedProfile(driver);
        forcePhotoColorProfile(driver, config);
        ((void (*)(id, SEL))objc_msgSend)(driver, @selector(hideUI));
        printf("open=%d\n", sendShort(driver, @selector(open)));
        printf("scan=%d\n", sendShort(driver, @selector(scan)));
        runLoopUntilDone(driver);

        int count = 0;
        while ([driver respondsToSelector:@selector(hasNextImageData)] && sendBool(driver, @selector(hasNextImageData))) {
            id imageData = sendId(driver, @selector(nextImageData));
            count++;
            printf("imageData[%d]=%s width=%u height=%u resolution=%u widthIn=%.3f heightIn=%.3f\n",
                   count,
                   imageData ? NSStringFromClass([imageData class]).UTF8String : "(nil)",
                   imageData ? sendUInt(imageData, @selector(width)) : 0,
                   imageData ? sendUInt(imageData, @selector(height)) : 0,
                   imageData ? sendUInt(imageData, @selector(resolution)) : 0,
                   imageData ? sendFloat(imageData, @selector(widthAsInch)) : 0.0,
                   imageData ? sendFloat(imageData, @selector(heightAsInch)) : 0.0);
            if (imageData && [imageData respondsToSelector:@selector(path)]) {
                NSString *path = sendId(imageData, @selector(path));
                printf("imageData[%d].path=%s\n", count, [[path description] UTF8String]);
                NSString *copyPath = captureCopyPathForImageData(imageData, count, config->outPath);
                printf("imageData[%d].copy=%s\n", count, [[copyPath description] UTF8String]);
            }
        }

        printf("saveAllImages=%d\n", sendShort(driver, @selector(saveAllImages)));
        printf("close=%d\n", sendShort(driver, @selector(close)));
        if (count == 0) {
            fprintf(stderr, "scan failed: no image data returned\n");
            return 2;
        }
        return 0;
    } @catch (NSException *exception) {
        fprintf(stderr, "exception: %s: %s\n", exception.name.UTF8String, exception.reason.UTF8String);
        return 1;
    }
}

static void listDevices(void) {
    @try {
        Class finderClass = NSClassFromString(@"SDIDeviceFinder");
        id finder = sendId(finderClass, @selector(alloc));
        finder = sendId(finder, @selector(init));
        if ([finder respondsToSelector:@selector(setTimeout:)]) {
            ((void (*)(id, SEL, long long))objc_msgSend)(finder, @selector(setTimeout:), 5LL);
        }
        if ([finder respondsToSelector:@selector(discoverUSBDevices)]) {
            ((void (*)(id, SEL))objc_msgSend)(finder, @selector(discoverUSBDevices));
        }
        if ([finder respondsToSelector:@selector(startDiscovery)]) {
            printf("startDiscovery=%d\n", sendShort(finder, @selector(startDiscovery)));
        }
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:8.0];
        while ([deadline timeIntervalSinceNow] > 0) {
            [[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.25]];
        }
        if ([finder respondsToSelector:@selector(stopDiscovery)]) {
            printf("stopDiscovery=%d\n", sendShort(finder, @selector(stopDiscovery)));
        }
        id devices = sendId(finder, @selector(devices));
        printf("devices=%s\n", [[devices description] UTF8String]);
        for (id device in devices) {
            printf("deviceClass=%s\n", NSStringFromClass([device class]).UTF8String);
            if ([device respondsToSelector:@selector(dictionary)]) {
                printf("dictionary=%s\n", [[sendId(device, @selector(dictionary)) description] UTF8String]);
            }
            if ([device respondsToSelector:@selector(GUID)]) {
                printf("GUID=%s\n", [[sendId(device, @selector(GUID)) description] UTF8String]);
            }
            if ([device respondsToSelector:@selector(modelID)]) {
                printf("modelID=%s\n", [[sendId(device, @selector(modelID)) description] UTF8String]);
            }
            if ([device respondsToSelector:@selector(label)]) {
                printf("label=%s\n", [[sendId(device, @selector(label)) description] UTF8String]);
            }
        }
    } @catch (NSException *exception) {
        fprintf(stderr, "exception: %s: %s\n", exception.name.UTF8String, exception.reason.UTF8String);
    }
}

static int parseIntArg(const char *value, const char *name) {
    if (!value) {
        fprintf(stderr, "missing value for %s\n", name);
        exit(2);
    }
    return atoi(value);
}

static ScanConfig parseScanConfig(int argc, const char **argv) {
    ScanConfig config = defaultScanConfig();
    for (int i = 2; i < argc; i++) {
        if (strcmp(argv[i], "--out") == 0) {
            if (i + 1 >= argc) {
                fprintf(stderr, "missing value for --out\n");
                exit(2);
            }
            config.outPath = argv[++i];
        } else if (strcmp(argv[i], "--dpi") == 0) {
            config.dpi = parseIntArg(argv[++i], "--dpi");
        } else if (strcmp(argv[i], "--brightness") == 0) {
            config.brightness = parseIntArg(argv[++i], "--brightness");
        } else if (strcmp(argv[i], "--contrast") == 0) {
            config.contrast = parseIntArg(argv[++i], "--contrast");
        } else if (strcmp(argv[i], "--saturation") == 0) {
            config.saturation = parseIntArg(argv[++i], "--saturation");
        } else if (strcmp(argv[i], "--unsharp") == 0) {
            config.unsharp = parseIntArg(argv[++i], "--unsharp");
        } else if (strcmp(argv[i], "--descreen") == 0) {
            config.descreen = parseIntArg(argv[++i], "--descreen");
        } else {
            fprintf(stderr, "unknown scan arg: %s\n", argv[i]);
            exit(2);
        }
    }
    return config;
}

int main(int argc, const char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    @autoreleasepool {
        const char *framework = "/Library/Image Capture/Support/EPSON/Epson Scan 2/Core/EpsonScan2.framework/EpsonScan2";
        void *handle = dlopen(framework, RTLD_NOW | RTLD_GLOBAL);
        if (!handle) {
            fprintf(stderr, "dlopen failed: %s\n", dlerror());
            return 1;
        }

        if (argc > 1 && strcmp(argv[1], "dry") == 0) {
            dryInit(NO);
            return 0;
        }

        if (argc > 1 && strcmp(argv[1], "open-dry") == 0) {
            dryInit(YES);
            return 0;
        }

        if (argc > 1 && strcmp(argv[1], "scan") == 0) {
            ScanConfig config = parseScanConfig(argc, argv);
            return scanNoUI(&config);
        }

        if (argc > 1 && strcmp(argv[1], "devices") == 0) {
            listDevices();
            return 0;
        }

        if (argc > 1) {
            for (int i = 1; i < argc; i++) {
                dumpClass([NSString stringWithUTF8String:argv[i]]);
            }
            return 0;
        }

        NSArray<NSString *> *classes = @[
            @"ESAStartScanningCommand",
            @"ESACreateScanSettingsCommand",
            @"ESACommand",
            @"ESAArguments",
            @"SDIScannerDriver",
            @"SDIScanController",
            @"SDIScanSequencer",
            @"SDIDataManager",
            @"EPSDataManager",
            @"SDIDeviceManager",
            @"SDIDeviceFinder",
            @"SDIUSBDeviceInfo",
            @"SDIDeviceInfo",
            @"SDEConcreteScannerDriverEngine",
            @"SDEScannerControlParam"
        ];
        for (NSString *name in classes) {
            dumpClass(name);
        }
    }
    return 0;
}
