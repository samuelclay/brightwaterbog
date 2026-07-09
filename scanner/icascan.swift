// icascan — headless CLI for the Epson Perfection V19II (ES0282) via Apple ImageCaptureCore.
//
// SANE's epsonds backend can't handshake this 2023 model, so we drive the
// macOS ICA driver directly. Two modes:
//   icascan list                         -> discover scanners, print names, exit
//   icascan inspect                      -> print scanner feature capabilities, exit
//   icascan scan --out FILE [opts]       -> scan the flatbed to FILE (TIFF)
//
// Build: swiftc -O scanner/icascan.swift -o scanner/icascan -framework ImageCaptureCore
//
// Scan options:
//   --out PATH        output file (default ./scan.tiff). Extension picks format.
//   --dpi N           resolution, default 300
//   --color MODE      color|gray, default color
//   --timeout SEC     overall timeout, default 120
//   --device NAME     substring match if multiple scanners; default first found.
//   --backlight LEVEL Epson backlight correction: off|low|middle|high
//   --color-restoration on|off
//   --unsharp LEVEL   Epson unsharp mask: off|low|middle|high
//   --descreen LEVEL  Epson descreen: off|low|middle|high
//   --dust-removal LEVEL Epson dust removal: off|low|middle|high
//   --area-inches X,Y,W,H scan only a physical flatbed rectangle

import Foundation
import ImageCaptureCore

// ---- tiny arg parser -------------------------------------------------------
func argValue(_ name: String, _ def: String? = nil) -> String? {
    let a = CommandLine.arguments
    if let i = a.firstIndex(of: name), i + 1 < a.count { return a[i + 1] }
    return def
}
func log(_ s: String) { FileHandle.standardError.write((s + "\n").data(using: .utf8)!) }

let mode = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "list"
let wantDevice = argValue("--device")
let outPath = argValue("--out", "scan.tiff")!
let dpi = Double(argValue("--dpi", "300")!) ?? 300
let colorMode = argValue("--color", "color")!
let overallTimeout = Double(argValue("--timeout", "120")!) ?? 120
let backlightCorrection = argValue("--backlight")
let colorRestoration = argValue("--color-restoration")
let unsharpMask = argValue("--unsharp")
let descreen = argValue("--descreen")
let dustRemoval = argValue("--dust-removal")
let areaInches = argValue("--area-inches")

// ---- delegate that drives browse -> open -> scan ---------------------------
final class Driver: NSObject, ICDeviceBrowserDelegate, ICScannerDeviceDelegate {
    let browser = ICDeviceBrowser()
    var scanner: ICScannerDevice?
    var done = false
    var exitCode: Int32 = 0

    override init() {
        super.init()
        browser.delegate = self
        // Local + shared scanners.
        browser.browsedDeviceTypeMask = ICDeviceTypeMask(rawValue:
            ICDeviceTypeMask.scanner.rawValue |
            ICDeviceLocationTypeMask.local.rawValue |
            ICDeviceLocationTypeMask.shared.rawValue)!
    }

    func start() { browser.start() }

    func finish(_ code: Int32) {
        if done { return }
        exitCode = code
        done = true
        // Always close any open session so we never leave the device held,
        // then stop the MAIN run loop (watchdog fires on a background queue,
        // where CFRunLoopGetCurrent() would be the wrong loop — the bug that
        // orphaned timed-out scans and made the device perpetually "busy").
        scanner?.requestCloseSession()
        CFRunLoopStop(CFRunLoopGetMain())
    }

    // --- discovery ---
    func deviceBrowser(_ b: ICDeviceBrowser, didAdd device: ICDevice, moreComing: Bool) {
        guard let s = device as? ICScannerDevice else { return }
        log("found scanner: \(s.name ?? "?")")
        if mode == "list" { return }
        if scanner != nil { return }
        if let want = wantDevice, !(s.name ?? "").localizedCaseInsensitiveContains(want) { return }
        scanner = s
        s.delegate = self
        openSession()
    }

    var openAttempts = 0
    func openSession() {
        guard let s = scanner else { finish(2); return }
        openAttempts += 1
        log("requestOpenSession (attempt \(openAttempts))")
        s.requestOpenSession()
    }
    func deviceBrowser(_ b: ICDeviceBrowser, didRemove d: ICDevice, moreGoing: Bool) {}

    // --- session lifecycle ---
    func device(_ device: ICDevice, didOpenSessionWithError error: Error?) {
        if let e = error {
            let code = (e as NSError).code
            log("open session error: \(e) (code \(code))")
            // -47 = device busy (Epson agents or a prior session). Back off and retry.
            if openAttempts < 12 {
                let delay = 3.0
                log("device busy; retrying in \(delay)s")
                DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in self?.openSession() }
            } else {
                finish(2)
            }
            return
        }
        guard let s = scanner else { log("no scanner ref"); finish(2); return }
        log("session open; transferMode=fileBased")
        s.transferMode = .fileBased
        // Flatbed is already the default selected unit, so requestSelect is a
        // harmless no-op that won't fire didSelect. Capabilities (physical size,
        // resolutions) populate asynchronously after open — poll for them.
        log("ensuring flatbed; polling for capabilities")
        s.requestSelect(.flatbed)
        waitForCapabilitiesThenScan(attempt: 0)
    }
    func device(_ device: ICScannerDevice, didSelect unit: ICScannerFunctionalUnit, error: Error?) {
        log("didSelect fired; type=\(unit.type.rawValue) err=\(String(describing: error))")
        if let e = error { log("select unit error: \(e)"); finish(2); return }
        waitForCapabilitiesThenScan(attempt: 0)
    }

    // Poll until the flatbed reports a real physical size, then configure + scan.
    func waitForCapabilitiesThenScan(attempt: Int) {
        guard let s = scanner else { finish(2); return }
        let u = s.selectedFunctionalUnit
        u.measurementUnit = .inches
        let size = u.physicalSize
        log("cap check #\(attempt): size=\(size.width)x\(size.height) res=\(u.supportedResolutions.count)")
        if size.width > 0.1 && size.height > 0.1 {
            if mode == "inspect" {
                describeScanner()
                finish(0)
                return
            }
            configureAndScan()
            return
        }
        if attempt >= 40 { log("capabilities never populated"); finish(6); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
            self?.waitForCapabilitiesThenScan(attempt: attempt + 1)
        }
    }

    func describeIndexSet(_ set: NSIndexSet) -> String {
        var values: [Int] = []
        set.enumerate { idx, _ in values.append(idx) }
        if values.isEmpty { return "" }

        var ranges: [String] = []
        var start = values[0]
        var previous = values[0]
        for value in values.dropFirst() {
            if value == previous + 1 {
                previous = value
                continue
            }
            ranges.append(start == previous ? "\(start)" : "\(start)-\(previous)")
            start = value
            previous = value
        }
        ranges.append(start == previous ? "\(start)" : "\(start)-\(previous)")

        let visible = ranges.prefix(24).joined(separator: ", ")
        if ranges.count > 24 {
            return "\(visible), ... (\(values.count) values)"
        }
        return visible
    }

    func describeFeature(_ feature: ICScannerFeature, prefix: String = "feature") {
        let internalName = feature.internalName ?? "?"
        let readableName = feature.humanReadableName ?? "?"
        log("\(prefix): type=\(feature.type.rawValue) internal=\(internalName) label=\(readableName)")
        if let tooltip = feature.tooltip, !tooltip.isEmpty {
            log("  tooltip=\(tooltip)")
        }
        switch feature {
        case let enumFeature as ICScannerFeatureEnumeration:
            log("  current=\(enumFeature.currentValue) default=\(enumFeature.defaultValue)")
            log("  values=\(enumFeature.values)")
            log("  labels=\(enumFeature.menuItemLabels)")
        case let rangeFeature as ICScannerFeatureRange:
            log(String(format: "  current=%.3f default=%.3f min=%.3f max=%.3f step=%.3f",
                       rangeFeature.currentValue,
                       rangeFeature.defaultValue,
                       rangeFeature.minValue,
                       rangeFeature.maxValue,
                       rangeFeature.stepSize))
        case let boolFeature as ICScannerFeatureBoolean:
            log("  value=\(boolFeature.value)")
        default:
            break
        }
    }

    func describeScanner() {
        guard let s = scanner else { return }
        let u = s.selectedFunctionalUnit
        log("scanner: \(s.name ?? "?")")
        log("functionalUnit.type=\(u.type.rawValue)")
        log("physicalSize=\(u.physicalSize.width)x\(u.physicalSize.height)")
        log("supportedResolutions=\(describeIndexSet(u.supportedResolutions as NSIndexSet))")
        log("preferredResolutions=\(describeIndexSet(u.preferredResolutions as NSIndexSet))")
        log("supportedBitDepths=\(describeIndexSet(u.supportedBitDepths as NSIndexSet))")
        log("supportedScaleFactors=\(describeIndexSet(u.supportedScaleFactors as NSIndexSet))")
        log("preferredScaleFactors=\(describeIndexSet(u.preferredScaleFactors as NSIndexSet))")
        log("nativeResolution=\(u.nativeXResolution)x\(u.nativeYResolution)")
        log("current: resolution=\(u.resolution) bitDepth=\(u.bitDepth.rawValue) pixelDataType=\(u.pixelDataType.rawValue) scale=\(u.scaleFactor)")
        if let flatbed = u as? ICScannerFunctionalUnitFlatbed {
            log("flatbed.supportedDocumentTypes=\(describeIndexSet(flatbed.supportedDocumentTypes as NSIndexSet))")
            log("flatbed.documentType=\(flatbed.documentType.rawValue)")
        }
        log("templates.count=\(u.templates.count)")
        for template in u.templates {
            describeFeature(template, prefix: "template")
        }
        let vendor = u.vendorFeatures ?? []
        log("vendorFeatures.count=\(vendor.count)")
        for feature in vendor {
            describeFeature(feature, prefix: "vendor")
        }
    }

    var scanStarted = false
    func configureAndScan() {
        if scanStarted { return }
        scanStarted = true
        log("configureAndScan entered")
        guard let s = scanner else { log("configureAndScan: no scanner"); finish(2); return }
        let u = s.selectedFunctionalUnit
        log("selectedFunctionalUnit type=\(u.type.rawValue) state=\(u.scanInProgress)")
        // Resolution: pick closest supported to requested dpi.
        if let res = u.supportedResolutions as IndexSet?, !res.isEmpty {
            let target = Int(dpi)
            let chosen = res.contains(target) ? target : (res.first(where: { $0 >= target }) ?? res.max() ?? target)
            u.resolution = chosen
            log("resolution: \(chosen) dpi")
        }
        u.pixelDataType = (colorMode == "gray") ? .gray : .RGB
        u.bitDepth = .depth8Bits
        applyVendorFeatureOverrides(u)
        // Full bed.
        u.measurementUnit = .inches
        let max = u.physicalSize
        let area = parsedScanArea(maxSize: max) ?? NSRect(x: 0, y: 0, width: max.width, height: max.height)
        u.scanArea = area
        log(String(format: "scan area: x=%.2f y=%.2f %.2f x %.2f in",
                   area.origin.x, area.origin.y, area.width, area.height))

        let url = URL(fileURLWithPath: outPath)
        let dir = url.deletingLastPathComponent()
        s.downloadsDirectory = dir
        s.documentName = url.deletingPathExtension().lastPathComponent
        let ext = url.pathExtension.lowercased()
        s.documentUTI = (ext == "jpg" || ext == "jpeg") ? (kUTTypeJPEG as String)
                      : (ext == "png") ? (kUTTypePNG as String)
                      : (kUTTypeTIFF as String)
        log("scanning -> \(outPath)")
        s.requestScan()
    }

    func parsedScanArea(maxSize: NSSize) -> NSRect? {
        guard let areaInches else { return nil }
        let parts = areaInches.split(separator: ",").map { Double($0.trimmingCharacters(in: .whitespaces)) }
        guard parts.count == 4, let x = parts[0], let y = parts[1], let w = parts[2], let h = parts[3] else {
            log("WARN: ignoring invalid --area-inches '\(areaInches)', expected X,Y,W,H")
            return nil
        }
        let clampedX = min(max(0.0, x), maxSize.width)
        let clampedY = min(max(0.0, y), maxSize.height)
        let clampedW = min(max(0.1, w), maxSize.width - clampedX)
        let clampedH = min(max(0.1, h), maxSize.height - clampedY)
        return NSRect(x: clampedX, y: clampedY, width: clampedW, height: clampedH)
    }

    func normalized(_ value: String) -> String {
        value.trimmingCharacters(in: .whitespacesAndNewlines)
             .lowercased()
             .replacingOccurrences(of: " ", with: "")
             .replacingOccurrences(of: "-", with: "")
             .replacingOccurrences(of: "_", with: "")
    }

    func requestedEnumValue(_ requested: String, feature: ICScannerFeatureEnumeration) -> NSNumber? {
        let wanted = normalized(requested)
        let levelAliases: [String: String] = [
            "0": "off", "none": "off", "false": "off", "no": "off",
            "1": "low",
            "2": "middle", "mid": "middle", "medium": "middle",
            "3": "high"
        ]
        let canonical = levelAliases[wanted] ?? wanted

        for (index, label) in feature.menuItemLabels.enumerated() {
            if normalized(label) == canonical, index < feature.values.count {
                return feature.values[index]
            }
        }

        let numeric: [String: Int] = ["off": 0, "low": 1, "middle": 2, "high": 3]
        if let target = numeric[canonical] {
            let targetNumber = NSNumber(value: target)
            if feature.values.contains(targetNumber) {
                return targetNumber
            }
        }
        return nil
    }

    func requestedBoolValue(_ requested: String) -> Bool? {
        switch normalized(requested) {
        case "on", "true", "yes", "1", "enabled", "enable":
            return true
        case "off", "false", "no", "0", "disabled", "disable":
            return false
        default:
            return nil
        }
    }

    func applyVendorFeatureOverrides(_ unit: ICScannerFunctionalUnit) {
        let enumOverrides: [String: String?] = [
            "VF_BACKLIGHTCORRECTION": backlightCorrection,
            "VF_UNSHARPMASK": unsharpMask,
            "VF_DESCREEN": descreen,
            "VF_DUSTREMOVAL": dustRemoval
        ]
        let boolOverrides: [String: String?] = [
            "VF_COLORRESTORATION": colorRestoration
        ]

        for feature in unit.vendorFeatures ?? [] {
            guard let internalName = feature.internalName else { continue }
            if let requested = enumOverrides[internalName] ?? nil {
                guard let enumFeature = feature as? ICScannerFeatureEnumeration else {
                    log("WARN: \(internalName) is not an enumeration feature")
                    continue
                }
                guard let value = requestedEnumValue(requested, feature: enumFeature) else {
                    log("WARN: unsupported value '\(requested)' for \(internalName); labels=\(enumFeature.menuItemLabels)")
                    continue
                }
                enumFeature.currentValue = value
                log("vendor \(internalName)=\(requested) -> \(value)")
            }
            if let requested = boolOverrides[internalName] ?? nil {
                guard let boolFeature = feature as? ICScannerFeatureBoolean else {
                    log("WARN: \(internalName) is not a boolean feature")
                    continue
                }
                guard let value = requestedBoolValue(requested) else {
                    log("WARN: unsupported boolean value '\(requested)' for \(internalName)")
                    continue
                }
                boolFeature.value = value
                log("vendor \(internalName)=\(value)")
            }
        }
    }

    // --- scan results ---
    func device(_ scanner: ICScannerDevice, didCompleteScanWithError error: Error?) {
        if let e = error { log("scan error: \(e)"); finish(3); return }
        log("scan complete")
        scanner.requestCloseSession()
    }
    func scannerDevice(_ scanner: ICScannerDevice, didScanTo url: URL) {
        log("wrote: \(url.path)")
        print(url.path)  // stdout = machine-readable result path
    }
    func device(_ device: ICDevice, didCloseSessionWithError error: Error?) { finish(0) }

    // required no-ops
    func didRemove(_ device: ICDevice) {}
    func device(_ device: ICDevice, didReceiveStatusInformation status: [ICDeviceStatus : Any]) {}
    func device(_ device: ICDevice, didEncounterError error: Error?) {
        if let e = error { log("device error: \(e)"); finish(4) }
    }
}

let driver = Driver()
// Clean exit on termination so the ICA session is released, not orphaned.
signal(SIGTERM) { _ in exit(143) }
signal(SIGINT)  { _ in exit(130) }
driver.start()

// Overall watchdog.
DispatchQueue.global().asyncAfter(deadline: .now() + overallTimeout) {
    if !driver.done { log("timeout after \(overallTimeout)s"); driver.finish(5) }
}
// For `list`, give discovery a few seconds then exit cleanly.
if mode == "list" {
    DispatchQueue.global().asyncAfter(deadline: .now() + 4) {
        if !driver.done { driver.finish(0) }
    }
}

CFRunLoopRun()
exit(driver.exitCode)
