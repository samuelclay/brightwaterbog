// icascan — headless CLI for the Epson Perfection V19II (ES0282) via Apple ImageCaptureCore.
//
// SANE's epsonds backend can't handshake this 2023 model, so we drive the
// macOS ICA driver directly. Two modes:
//   icascan list                         -> discover scanners, print names, exit
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
            configureAndScan()
            return
        }
        if attempt >= 40 { log("capabilities never populated"); finish(6); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
            self?.waitForCapabilitiesThenScan(attempt: attempt + 1)
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
        // Full bed.
        u.measurementUnit = .inches
        let max = u.physicalSize
        u.scanArea = NSRect(x: 0, y: 0, width: max.width, height: max.height)
        log(String(format: "scan area: %.2f x %.2f in", max.width, max.height))

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
