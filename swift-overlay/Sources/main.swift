import AppKit
import SwiftUI

// MARK: - Event Poller (reads /tmp/codec_overlay_events.jsonl)
final class EventPoller: NSObject {
    private var timer: Timer?
    private var lastOffset: Int = 0
    weak var appDelegate: AppDelegate?
    private let eventFile = "/tmp/codec_overlay_events.jsonl"

    func start() {
        // Create file if missing
        if !FileManager.default.fileExists(atPath: eventFile) {
            FileManager.default.createFile(atPath: eventFile, contents: nil)
        }
        timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in
            self?.poll()
        }
    }

    private func poll() {
        guard let data = FileManager.default.contents(atPath: eventFile),
              let text = String(data: data, encoding: .utf8) else { return }
        let lines = text.components(separatedBy: "\n").filter { !$0.isEmpty }
        guard lines.count > lastOffset else { return }
        let newLines = lines[lastOffset...]
        lastOffset = lines.count
        for line in newLines {
            guard let d = line.data(using: .utf8),
                  let json = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
                  let type = json["type"] as? String else { continue }
            DispatchQueue.main.async { [weak self] in
                self?.appDelegate?.handleEvent(type: type, json: json)
            }
        }
    }
}

// MARK: - Overlay Panel
final class OverlayPanel: NSPanel {
    private let label = NSTextField(labelWithString: "")
    private let dot = NSTextField(labelWithString: "🔴")
    private var pulseTimer: Timer?

    override init(contentRect: NSRect, styleMask style: NSWindow.StyleMask,
                  backing backingStoreType: NSWindow.BackingStoreType, defer flag: Bool) {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 340, height: 64),
                   styleMask: [.nonactivatingPanel, .fullSizeContentView],
                   backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .floating
        backgroundColor = NSColor(red: 0.082, green: 0.082, blue: 0.137, alpha: 0.96)
        isOpaque = false
        hasShadow = true
        isMovableByWindowBackground = true
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]

        // Orange border via container view
        let container = NSView(frame: contentView!.bounds)
        container.wantsLayer = true
        container.layer?.cornerRadius = 12
        container.layer?.borderWidth = 2
        container.layer?.borderColor = NSColor(red: 0.910, green: 0.443, blue: 0.102, alpha: 1).cgColor
        container.layer?.masksToBounds = true
        contentView?.addSubview(container)
        contentView?.layer?.cornerRadius = 12
        contentView?.layer?.masksToBounds = true

        // Layout
        dot.font = NSFont.systemFont(ofSize: 18)
        dot.frame = NSRect(x: 16, y: 20, width: 28, height: 28)
        container.addSubview(dot)

        label.font = NSFont.systemFont(ofSize: 15, weight: .semibold)
        label.textColor = NSColor(red: 0.9, green: 0.9, blue: 0.9, alpha: 1)
        label.frame = NSRect(x: 52, y: 22, width: 270, height: 24)
        label.stringValue = "🔴 Listening — release to send"
        container.addSubview(label)
    }

    func show(text: String = "🔴 Listening — release to send") {
        label.stringValue = text
        // Centre-bottom of main screen
        if let screen = NSScreen.main {
            let sr = screen.visibleFrame
            let x = sr.midX - frame.width / 2
            let y = sr.minY + 80
            setFrameOrigin(NSPoint(x: x, y: y))
        }
        makeKeyAndOrderFront(nil)
        startPulse()
    }

    func hide() {
        stopPulse()
        orderOut(nil)
    }

    func update(text: String) {
        label.stringValue = text
    }

    private func startPulse() {
        stopPulse()
        var visible = true
        pulseTimer = Timer.scheduledTimer(withTimeInterval: 0.6, repeats: true) { [weak self] _ in
            visible.toggle()
            self?.dot.alphaValue = visible ? 1.0 : 0.2
        }
    }

    private func stopPulse() {
        pulseTimer?.invalidate()
        pulseTimer = nil
        dot.alphaValue = 1.0
    }
}

// MARK: - App Delegate
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var overlay: OverlayPanel!
    private let poller = EventPoller()
    private var lastSkill = "none"
    private var isOn = true
    private var recentSkills: [String] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)

        overlay = OverlayPanel(contentRect: .zero, styleMask: [], backing: .buffered, defer: false)

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let btn = statusItem.button {
            btn.image = NSImage(systemSymbolName: "bolt.fill", accessibilityDescription: "CODEC")
            btn.image?.isTemplate = true
        }
        buildMenu()
        poller.appDelegate = self
        poller.start()
    }

    func buildMenu() {
        let menu = NSMenu()

        let header = NSMenuItem(title: "⚡ CODEC", action: nil, keyEquivalent: "")
        header.isEnabled = false
        menu.addItem(header)
        menu.addItem(.separator())

        let statusTitle = isOn ? "● Status: ON" : "● Status: OFF"
        let statusItem = NSMenuItem(title: statusTitle, action: nil, keyEquivalent: "")
        statusItem.isEnabled = false
        menu.addItem(statusItem)

        let skillItem = NSMenuItem(title: "Last: \(lastSkill)", action: nil, keyEquivalent: "")
        skillItem.isEnabled = false
        menu.addItem(skillItem)
        menu.addItem(.separator())

        menu.addItem(NSMenuItem(title: "🌐 Open Dashboard", action: #selector(openDashboard), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "💬 Open Chat", action: #selector(openChat), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "🎨 Open Vibe", action: #selector(openVibe), keyEquivalent: ""))
        menu.addItem(.separator())

        if !recentSkills.isEmpty {
            let recHeader = NSMenuItem(title: "Recent Skills:", action: nil, keyEquivalent: "")
            recHeader.isEnabled = false
            menu.addItem(recHeader)
            for skill in recentSkills.suffix(5) {
                let item = NSMenuItem(title: "  \(skill)", action: nil, keyEquivalent: "")
                item.isEnabled = false
                menu.addItem(item)
            }
            menu.addItem(.separator())
        }

        menu.addItem(NSMenuItem(title: "Quit CODEC Overlay", action: #selector(quitApp), keyEquivalent: "q"))

        self.statusItem.menu = menu
    }

    func handleEvent(type: String, json: [String: Any]) {
        DispatchQueue.main.async { [weak self] in
        guard let self = self else { return }
            switch type {
            case "recording_start":
                overlay.show(text: "🔴 Listening — release to send")
            case "recording_stop":
                overlay.hide()
            case "ptt_locked":
                overlay.show(text: "🔴 REC LOCKED — tap F18 to stop")
            case "transcribing":
                overlay.update(text: "⚙️ Transcribing...")
            case "skill_fired":
                let name = (json["name"] as? String) ?? "unknown"
                lastSkill = name
                recentSkills.append(name)
                if recentSkills.count > 10 { recentSkills.removeFirst() }
                buildMenu()
                overlay.update(text: "✅ \(name)")
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
                    self?.overlay.hide()
                }
            case "toggle_on":
                isOn = true
                buildMenu()
                if let btn = self.statusItem.button {
                    btn.image = NSImage(systemSymbolName: "bolt.fill", accessibilityDescription: "CODEC")
                    btn.image?.isTemplate = true
                }
            case "toggle_off":
                isOn = false
                buildMenu()
                if let btn = self.statusItem.button {
                    btn.image = NSImage(systemSymbolName: "bolt.slash.fill", accessibilityDescription: "CODEC")
                    btn.image?.isTemplate = true
                }
            default:
                break
            }
        }
    }

    @objc private func openDashboard() {
        NSWorkspace.shared.open(URL(string: "http://localhost:8090")!)
    }
    @objc private func openChat() {
        NSWorkspace.shared.open(URL(string: "http://localhost:8090/chat")!)
    }
    @objc private func openVibe() {
        NSWorkspace.shared.open(URL(string: "http://localhost:8090/vibe")!)
    }
    @objc private func quitApp() {
        NSApplication.shared.terminate(nil)
    }
}

// MARK: - Entry Point
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
