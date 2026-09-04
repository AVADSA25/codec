// CODEC bundle launcher — a real Mach-O executable.
//
// WHY THIS EXISTS AT ALL (2026-09-03)
//
// CFBundleExecutable used to be a /bin/sh script. macOS attaches entitlements
// only to Mach-O binaries, so `codesign --entitlements` on a bundle whose main
// executable is a script is ACCEPTED AND SILENTLY IGNORED. The result was an
// app with hardened runtime enforced and zero entitlements granted:
//
//     CodeDirectory v=20200 size=211 flags=0x10000(runtime)
//     entitlements: (none)
//
// For a voice-first product that means the microphone can never be granted —
// no prompt, no entry in System Settings, no error. No edit to
// codec.entitlements could ever fix it while the executable was a script.
//
// It also fixes the second half of the same report: "when I click it nothing
// happened". The old script started the fleet and exited with no window, no
// menu-bar item and no notification, so a buyer clicking the icon saw nothing
// at all. This launcher stays alive as a menu-bar item and says what it did.
//
// It deliberately does NOT reimplement the fleet logic. Resources/codec_app_main.py
// remains the single source of truth for what starting CODEC means; this binary
// runs it with the bundled interpreter and reports the outcome.

import AppKit
import Foundation

// MARK: - Bundle layout

let bundleURL = Bundle.main.bundleURL
let contents = bundleURL.appendingPathComponent("Contents")
let resources = contents.appendingPathComponent("Resources")
let bundledPython = resources.appendingPathComponent("python/bin/python3")
let entryPoint = resources.appendingPathComponent("codec_app_main.py")

func log(_ message: String) {
    let stamp = ISO8601DateFormatter().string(from: Date())
    FileHandle.standardError.write("\(stamp)  \(message)\n".data(using: .utf8)!)
}

// MARK: - Starting the fleet

struct StartResult {
    let ok: Bool
    let summary: String
    let detail: String
}

/// Run codec_app_main.py with the bundled interpreter and capture its outcome.
///
/// Falls back to `/usr/bin/python3` only when the bundle carries no interpreter,
/// which is a packaging error rather than a supported configuration — but a
/// degraded start beats a silent no-op.
func startFleet() -> StartResult {
    let fm = FileManager.default
    guard fm.fileExists(atPath: entryPoint.path) else {
        return StartResult(ok: false,
                           summary: "CODEC is incomplete",
                           detail: "Resources/codec_app_main.py is missing from the app bundle. Reinstall CODEC.")
    }

    let interpreter: URL
    if fm.isExecutableFile(atPath: bundledPython.path) {
        interpreter = bundledPython
    } else {
        log("bundled interpreter missing at \(bundledPython.path) — falling back to /usr/bin/python3")
        interpreter = URL(fileURLWithPath: "/usr/bin/python3")
    }

    let process = Process()
    process.executableURL = interpreter
    process.arguments = [entryPoint.path]
    process.currentDirectoryURL = resources
    // Never let the bundled interpreter write .pyc files back into the app.
    // Doing so invalidates the code signature ("a sealed resource is missing or
    // invalid") and Gatekeeper then refuses to launch it. The bundle is signed
    // and immutable by contract; caches belong outside it.
    var env = ProcessInfo.processInfo.environment
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    process.environment = env

    let out = Pipe()
    let err = Pipe()
    process.standardOutput = out
    process.standardError = err

    do {
        try process.run()
    } catch {
        return StartResult(ok: false,
                           summary: "CODEC could not start",
                           detail: "Failed to launch \(interpreter.lastPathComponent): \(error.localizedDescription)")
    }

    // Read before waiting: a full pipe buffer would deadlock the child.
    let outData = out.fileHandleForReading.readDataToEndOfFile()
    let errData = err.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()

    let stdout = String(data: outData, encoding: .utf8) ?? ""
    let stderr = String(data: errData, encoding: .utf8) ?? ""
    let combined = stdout + stderr
    combined.split(separator: "\n").forEach { log(String($0)) }

    if process.terminationStatus != 0 {
        // Surface the real reason, not "something went wrong". The first line of
        // a Python traceback's final frame is what actually helps.
        // Case-insensitive: codec_app_main prints "FLEET ERROR: ... REFUSING: ..."
        // and the old match missed it, so the alert read "exit code 1" — true
        // and useless. Prefer the REFUSING/Error line, else the last non-empty one.
        let lines = combined.split(separator: "\n").map(String.init).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
        let reason = lines.last(where: { $0.range(of: "refus", options: .caseInsensitive) != nil })
            ?? lines.last(where: { $0.range(of: "error", options: .caseInsensitive) != nil })
            ?? lines.last
            ?? "exit code \(process.terminationStatus)"
        return StartResult(ok: false, summary: "CODEC could not start", detail: reason)
    }

    let services = combined
        .split(separator: "\n")
        .first(where: { $0.contains("service") })
        .map(String.init) ?? "CODEC is running in the background."
    return StartResult(ok: true, summary: "CODEC is running", detail: services)
}

// MARK: - Menu-bar presence

/// The app has no window by design — it is a background agent. But an icon that
/// visibly does nothing when clicked is indistinguishable from a broken one, so
/// it holds a menu-bar item: proof it started, and somewhere to go next.
final class Launcher: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem?
    private var result: StartResult?
    // The dashboard renders INSIDE the app now. Handing the URL to the user's
    // browser made "the Mac app" a website with an app icon in front of it.
    private lazy var dashboard = DashboardWindowController(
        url: URL(string: "http://127.0.0.1:8090/")!)

    func applicationDidFinishLaunching(_ notification: Notification) {
        log("CODEC launched from \(bundleURL.path)")

        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = item.button {
            button.image = NSImage(systemSymbolName: "waveform", accessibilityDescription: "CODEC")
            button.image?.isTemplate = true
        }
        statusItem = item
        if item.button == nil {
            log("status bar refused a status item — the Dock icon remains the way back in")
        }
        rebuildMenu(status: "Starting…", enabled: false)

        DispatchQueue.global().async { [weak self] in
            let outcome = startFleet()
            DispatchQueue.main.async {
                self?.result = outcome
                self?.rebuildMenu(status: outcome.summary, enabled: true)
                self?.announce(outcome)
            }
        }
    }

    private func rebuildMenu(status: String, enabled: Bool) {
        let menu = NSMenu()
        menu.addItem(withTitle: status, action: nil, keyEquivalent: "")
        menu.addItem(.separator())
        let dashboard = menu.addItem(withTitle: "Open CODEC Dashboard",
                                     action: #selector(openDashboard), keyEquivalent: "")
        dashboard.target = self
        dashboard.isEnabled = enabled
        let reload = menu.addItem(withTitle: "Reload", action: #selector(reloadDashboard), keyEquivalent: "r")
        reload.target = self
        let logs = menu.addItem(withTitle: "Show Logs", action: #selector(openLogs), keyEquivalent: "")
        logs.target = self
        menu.addItem(.separator())
        let quit = menu.addItem(withTitle: "Quit CODEC", action: #selector(quit), keyEquivalent: "q")
        quit.target = self
        statusItem?.menu = menu
    }

    /// A failure must be impossible to miss; a success must not nag. So an error
    /// gets a modal the user has to dismiss, and a success only updates the menu.
    private func announce(_ outcome: StartResult) {
        guard !outcome.ok else {
            // A successful start that shows nothing is indistinguishable from a
            // broken one. The window retries its own load while the fleet binds
            // its port, so it can open immediately.
            DispatchQueue.main.async { [weak self] in self?.openDashboard() }
            return
        }
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = outcome.summary
        alert.informativeText = outcome.detail
        alert.addButton(withTitle: "Show Logs")
        alert.addButton(withTitle: "OK")
        if alert.runModal() == .alertFirstButtonReturn { openLogs() }
    }

    @objc private func openDashboard() { dashboard.show() }

    /// Clicking the Dock icon with no window open must bring CODEC back, or the
    /// app looks dead after the window is closed.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { dashboard.show() }
        return true
    }

    @objc private func reloadDashboard() { dashboard.reload() }

    @objc private func openLogs() {
        let logs = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/CODEC")
        NSWorkspace.shared.open(logs)
    }

    @objc private func quit() { NSApp.terminate(nil) }
}
