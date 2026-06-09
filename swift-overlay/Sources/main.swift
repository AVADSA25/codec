import AppKit

// ============================================================================
// CODEC Overlay — native AppKit HUD that floats above everything (incl.
// fullscreen apps) via `collectionBehavior = [.canJoinAllSpaces,
// .fullScreenAuxiliary]`. Fed by appending JSON lines to
// ~/.codec/overlay_events.jsonl (the codec_overlays.py / codec_dictate.py
// emitters write here). Redesigned 2026-06: glass vibrancy, 18px pill,
// tinted CODEC hexagon mark, shortcut chips, per-state accents.
// ============================================================================

// MARK: - Brand tokens
enum Brand {
    static let orange     = NSColor(srgbRed: 0.910, green: 0.443, blue: 0.102, alpha: 1) // #E8711A
    static let red        = NSColor(srgbRed: 1.000, green: 0.271, blue: 0.227, alpha: 1) // #ff453a
    static let blue       = NSColor(srgbRed: 0.039, green: 0.518, blue: 1.000, alpha: 1) // #0A84FF
    static let green      = NSColor(srgbRed: 0.188, green: 0.820, blue: 0.345, alpha: 1) // #30D158
    static let textPrimary = NSColor(white: 0.93, alpha: 1)
    static let textMuted   = NSColor(srgbRed: 0.74, green: 0.74, blue: 0.78, alpha: 1)
    static let hairline    = NSColor(white: 1.0, alpha: 0.08)

    static func from(hex: String) -> NSColor {
        let h = hex.lowercased()
        if h.contains("ff33") || h.contains("ff45") || h.contains("ff3b") || h.contains("ef44") { return red }
        if h.contains("00aa") || h.contains("0a84") || h.contains("448a") || h.contains("3b82") { return blue }
        if h.contains("30d1") || h.contains("22c5") { return green }
        return orange
    }
}

// MARK: - Overlay state
enum OverlayState {
    case toggleOn, toggleOff, recording, processing, live, refining
    case notify(NSColor)

    var accent: NSColor {
        switch self {
        case .toggleOn, .recording:    return Brand.orange
        case .toggleOff, .live:        return Brand.red
        case .processing, .refining:   return Brand.blue
        case .notify(let c):           return c
        }
    }
    var pulses: Bool {   // pulsing dot badge on the mark
        switch self { case .recording, .live: return true; default: return false }
    }
    var breathes: Bool { // gentle mark opacity breathe
        switch self { case .processing, .refining: return true; default: return false }
    }
    var wordmark: Bool { // letter-spaced uppercase title (CODEC / SIGNING OUT)
        switch self { case .toggleOn, .toggleOff: return true; default: return false }
    }
}

// MARK: - Shared mark image (~/.codec/overlay_mark.png, template-tinted per state)
let markImage: NSImage? = {
    let p = FileManager.default.homeDirectoryForCurrentUser.path + "/.codec/overlay_mark.png"
    guard let img = NSImage(contentsOfFile: p) else { return nil }
    img.isTemplate = true   // render as a silhouette so contentTintColor recolors the hexagon+bars
    return img
}()

// SOLID, bright text — no stroke. (A dark outline around glyphs on a dark panel
// was making everything thin and muddy.) On the dark-tinted glass, clean solid
// fills read perfectly.
func strokedText(_ s: String, font: NSFont, fill: NSColor, kern: CGFloat = 0, center: Bool = false) -> NSAttributedString {
    var attrs: [NSAttributedString.Key: Any] = [
        .font: font,
        .foregroundColor: fill,
    ]
    if kern != 0 { attrs[.kern] = kern }
    if center {
        let p = NSMutableParagraphStyle(); p.alignment = .center
        attrs[.paragraphStyle] = p
    }
    return NSAttributedString(string: s, attributes: attrs)
}

// MARK: - Shortcut chip  (e.g.  F18 · voice)
final class ChipView: NSView {
    init(key: String, label: String, accent: NSColor) {
        super.init(frame: .zero)
        wantsLayer = true
        layer?.cornerRadius = 10
        // Subtle orange capsule on the now-dark panel — white text reads cleanly.
        layer?.backgroundColor = accent.withAlphaComponent(0.22).cgColor
        layer?.borderWidth = 1
        layer?.borderColor = accent.withAlphaComponent(0.5).cgColor

        // Bright WHITE + dark outline ("line around the white") so chips read on glass.
        let keyL = NSTextField(labelWithString: key)
        keyL.attributedStringValue = strokedText(
            key, font: NSFont.monospacedSystemFont(ofSize: 14.5, weight: .bold), fill: .white)

        let labL = NSTextField(labelWithString: label)
        labL.attributedStringValue = strokedText(
            label, font: NSFont.systemFont(ofSize: 14.5, weight: .semibold), fill: .white)

        let s = NSStackView(views: [keyL, labL])
        s.orientation = .horizontal
        s.spacing = 5
        s.alignment = .firstBaseline
        s.translatesAutoresizingMaskIntoConstraints = false
        addSubview(s)
        NSLayoutConstraint.activate([
            s.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 11),
            s.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -11),
            s.topAnchor.constraint(equalTo: topAnchor, constant: 5),
            s.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -5),
        ])
    }
    required init?(coder: NSCoder) { fatalError() }
}

// MARK: - Overlay Panel (status HUD)
final class OverlayPanel: NSPanel {
    private let vfx = NSVisualEffectView()
    private let markView = NSImageView()
    private let dotLayer = CAShapeLayer()
    private let titleField = NSTextField(labelWithString: "")
    private let subtitleField = NSTextField(labelWithString: "")
    private let chips = NSStackView()
    private let textStack = NSStackView()
    private let hStack = NSStackView()

    private var pulseTimer: Timer?
    private var hideTimer: Timer?
    private let hInset: CGFloat = 30, vInset: CGFloat = 22
    private let radius: CGFloat = 24

    init() {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 360, height: 60),
                   styleMask: [.nonactivatingPanel, .fullSizeContentView],
                   backing: .buffered, defer: false)
        isFloatingPanel = true
        level = NSWindow.Level(rawValue: 25)        // screen-saver level — beats fullscreen
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        ignoresMouseEvents = true
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        alphaValue = 0
        setupUI()
    }

    private func setupUI() {
        vfx.material = .hudWindow
        vfx.blendingMode = .behindWindow
        vfx.state = .active
        // Keep the glass TRANSPARENT (no forced dark) — readability comes from
        // the stroked text outlines, not from darkening the panel.
        vfx.wantsLayer = true
        vfx.layer?.cornerRadius = radius
        vfx.layer?.masksToBounds = true
        vfx.layer?.borderWidth = 1.5
        vfx.layer?.borderColor = Brand.orange.withAlphaComponent(0.6).cgColor
        // maskImage clips the live BLUR + the window shadow to the rounded shape —
        // without it the square window corners show an opaque/bright fill.
        vfx.maskImage = OverlayPanel.roundedMask(radius: radius)
        contentView = vfx

        // Dark tint OVER the blur. A fully-transparent glass leaves text sitting on
        // whatever's behind the panel (unreadable on busy/light backgrounds). A
        // dark-tinted glass keeps the blur but guarantees white + gold text reads —
        // exactly like Spotlight / macOS notification HUDs.
        let tint = NSView()
        tint.wantsLayer = true
        tint.layer?.backgroundColor = NSColor.black.withAlphaComponent(0.66).cgColor
        tint.translatesAutoresizingMaskIntoConstraints = false
        vfx.addSubview(tint)
        NSLayoutConstraint.activate([
            tint.leadingAnchor.constraint(equalTo: vfx.leadingAnchor),
            tint.trailingAnchor.constraint(equalTo: vfx.trailingAnchor),
            tint.topAnchor.constraint(equalTo: vfx.topAnchor),
            tint.bottomAnchor.constraint(equalTo: vfx.bottomAnchor),
        ])

        // CODEC mark (52pt) with a pulsing dot badge layer
        markView.image = markImage
        markView.imageScaling = .scaleProportionallyUpOrDown
        markView.contentTintColor = Brand.orange
        markView.wantsLayer = true
        markView.translatesAutoresizingMaskIntoConstraints = false
        markView.widthAnchor.constraint(equalToConstant: 84).isActive = true
        markView.heightAnchor.constraint(equalToConstant: 84).isActive = true

        dotLayer.fillColor = Brand.red.cgColor
        dotLayer.path = CGPath(ellipseIn: CGRect(x: 61, y: 58, width: 20, height: 20), transform: nil)
        dotLayer.isHidden = true
        markView.layer?.addSublayer(dotLayer)

        titleField.alignment = .center
        titleField.maximumNumberOfLines = 1
        titleField.lineBreakMode = .byTruncatingTail

        subtitleField.font = NSFont.systemFont(ofSize: 17, weight: .regular)
        subtitleField.textColor = Brand.textMuted
        subtitleField.alignment = .center
        subtitleField.maximumNumberOfLines = 1

        chips.orientation = .horizontal
        chips.spacing = 9
        chips.alignment = .centerY

        textStack.orientation = .vertical
        textStack.spacing = 8
        textStack.alignment = .centerX
        textStack.setViews([titleField], in: .center)
        textStack.translatesAutoresizingMaskIntoConstraints = false

        vfx.addSubview(markView)
        vfx.addSubview(textStack)
        NSLayoutConstraint.activate([
            // CODEC mark: always pinned left, vertically centered
            markView.leadingAnchor.constraint(equalTo: vfx.leadingAnchor, constant: 26),
            markView.centerYAnchor.constraint(equalTo: vfx.centerYAnchor),
            // Title/content: perfectly centered in the FULL pill (clears the mark via >=)
            textStack.centerXAnchor.constraint(equalTo: vfx.centerXAnchor),
            textStack.centerYAnchor.constraint(equalTo: vfx.centerYAnchor),
            textStack.leadingAnchor.constraint(greaterThanOrEqualTo: markView.trailingAnchor, constant: 14),
            textStack.trailingAnchor.constraint(lessThanOrEqualTo: vfx.trailingAnchor, constant: -22),
        ])
    }

    // Rounded-rect mask (resizable via capInsets) — clips the vibrancy + shadow.
    static func roundedMask(radius: CGFloat) -> NSImage {
        let d = radius * 2 + 2
        let img = NSImage(size: NSSize(width: d, height: d), flipped: false) { rect in
            NSColor.black.setFill()
            NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius).fill()
            return true
        }
        img.capInsets = NSEdgeInsets(top: radius, left: radius, bottom: radius, right: radius)
        img.resizingMode = .stretch
        return img
    }

    // White title needs a soft dark halo to read over translucent glass.
    static func styledTitle(_ s: String, wordmark: Bool, color: NSColor) -> NSAttributedString {
        // Gold/accent fill + dark outline ("line around the orange"), centered.
        return strokedText(wordmark ? s.uppercased() : s,
                           font: NSFont.systemFont(ofSize: 27, weight: .bold),
                           fill: color, kern: wordmark ? 6.0 : 0, center: true)
    }

    // Parse "F18=voice  F16=text  **=screen  ++=doc  --=chat" → chip views
    private func buildChips(_ shortcuts: String, accent: NSColor) {
        chips.arrangedSubviews.forEach { $0.removeFromSuperview() }
        let tokens = shortcuts.split(whereSeparator: { $0 == " " }).map(String.init).filter { !$0.isEmpty }
        for tok in tokens {
            let parts = tok.split(separator: "=", maxSplits: 1).map(String.init)
            let key = parts.first ?? tok
            let label = parts.count > 1 ? parts[1] : ""
            chips.addArrangedSubview(ChipView(key: key, label: label, accent: accent))
        }
    }

    func configure(state: OverlayState, title: String, subtitle: String = "",
                   shortcuts: String = "", duration: Double = 0) {
        let accent = state.accent

        // Mark + ring + glow
        markView.contentTintColor = accent
        vfx.layer?.borderColor = accent.withAlphaComponent(0.55).cgColor
        vfx.layer?.shadowColor = accent.cgColor
        vfx.layer?.shadowOpacity = 0.0   // glow handled by panel shadow; keep subtle

        // Title — centered, accent-colored (orange / red / blue — never blank white), soft halo for legibility
        titleField.attributedStringValue = OverlayPanel.styledTitle(title, wordmark: state.wordmark, color: state.accent)

        // Subtitle row: chips (toggle-on) OR a muted subtitle line OR nothing
        textStack.arrangedSubviews.filter { $0 != titleField }.forEach { $0.removeFromSuperview() }
        if !shortcuts.isEmpty {
            buildChips(shortcuts, accent: accent)
            textStack.addArrangedSubview(chips)
        } else if !subtitle.isEmpty {
            subtitleField.stringValue = subtitle
            textStack.addArrangedSubview(subtitleField)
        }

        // Pulse / breathe animations
        stopAnimations()
        dotLayer.isHidden = !state.pulses
        if state.pulses { dotLayer.fillColor = accent.cgColor; startPulse() }
        if state.breathes { startBreathe() }

        // Fixed, uniform pill size for EVERY state, positioned bottom-center
        let w: CGFloat = 700, h: CGFloat = 140
        setContentSize(NSSize(width: w, height: h))
        vfx.layoutSubtreeIfNeeded()
        if let screen = NSScreen.main {
            let sr = screen.visibleFrame
            setFrameOrigin(NSPoint(x: sr.midX - w / 2, y: sr.minY + 80))
        }

        showFade()
        hideTimer?.invalidate()
        if duration > 0 {
            hideTimer = Timer.scheduledTimer(withTimeInterval: duration, repeats: false) { [weak self] _ in
                self?.hide()
            }
        }
    }

    func hide() {
        hideTimer?.invalidate()
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.16
            animator().alphaValue = 0
        }, completionHandler: { [weak self] in
            self?.orderOut(nil)
            self?.stopAnimations()
        })
    }

    private func showFade() {
        orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.12
            animator().alphaValue = 1
        }
    }

    private func startPulse() {
        var on = true
        pulseTimer = Timer.scheduledTimer(withTimeInterval: 0.55, repeats: true) { [weak self] _ in
            on.toggle()
            self?.dotLayer.opacity = on ? 1.0 : 0.25
        }
    }
    private func startBreathe() {
        var on = true
        pulseTimer = Timer.scheduledTimer(withTimeInterval: 0.7, repeats: true) { [weak self] _ in
            on.toggle()
            NSAnimationContext.runAnimationGroup { c in
                c.duration = 0.7
                self?.markView.animator().alphaValue = on ? 1.0 : 0.45
            }
        }
    }
    private func stopAnimations() {
        pulseTimer?.invalidate(); pulseTimer = nil
        dotLayer.opacity = 1.0
        markView.alphaValue = 1.0
    }
}

// MARK: - Input Panel (F16 branded "Enter task" box — focusable glass)
final class InputPanel: NSPanel {
    private let vfx = NSVisualEffectView()
    private let field = NSTextField()
    private let sendBtn = NSButton()
    private var replyPath = ""
    private let radius: CGFloat = 22

    init() {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 640, height: 96),
                   styleMask: [.borderless, .fullSizeContentView],
                   backing: .buffered, defer: false)
        level = NSWindow.Level(rawValue: 25)
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        isOpaque = false
        backgroundColor = .clear
        appearance = NSAppearance(named: .darkAqua)
        hasShadow = true
        isMovableByWindowBackground = true
        setupUI()
    }
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }

    private func setupUI() {
        vfx.material = .hudWindow
        vfx.blendingMode = .behindWindow
        vfx.state = .active
        vfx.appearance = NSAppearance(named: .darkAqua)
        vfx.wantsLayer = true
        vfx.layer?.cornerRadius = radius
        vfx.layer?.masksToBounds = true
        vfx.layer?.borderWidth = 1.5
        vfx.layer?.borderColor = Brand.orange.withAlphaComponent(0.65).cgColor
        vfx.maskImage = OverlayPanel.roundedMask(radius: radius)
        contentView = vfx

        let mark = NSImageView()
        mark.image = markImage
        mark.contentTintColor = Brand.orange
        mark.imageScaling = .scaleProportionallyUpOrDown
        mark.translatesAutoresizingMaskIntoConstraints = false
        mark.widthAnchor.constraint(equalToConstant: 44).isActive = true
        mark.heightAnchor.constraint(equalToConstant: 44).isActive = true

        field.placeholderString = "What can I do for you?"
        field.font = NSFont.systemFont(ofSize: 20, weight: .regular)
        field.textColor = Brand.textPrimary
        field.isBezeled = false
        field.drawsBackground = false
        field.focusRingType = .none
        field.translatesAutoresizingMaskIntoConstraints = false
        field.target = self
        field.action = #selector(submit)   // Enter submits

        sendBtn.title = "Send"
        sendBtn.bezelStyle = .rounded
        sendBtn.controlSize = .large
        sendBtn.contentTintColor = Brand.orange
        sendBtn.translatesAutoresizingMaskIntoConstraints = false
        sendBtn.target = self
        sendBtn.action = #selector(submit)

        let stack = NSStackView(views: [mark, field, sendBtn])
        stack.orientation = .horizontal
        stack.spacing = 16
        stack.alignment = .centerY
        stack.translatesAutoresizingMaskIntoConstraints = false
        vfx.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: vfx.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: vfx.trailingAnchor, constant: -22),
            stack.centerYAnchor.constraint(equalTo: vfx.centerYAnchor),
        ])
        field.setContentHuggingPriority(.defaultLow, for: .horizontal)  // field grows
    }

    func present(id: String, promptText: String) {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        replyPath = home + "/.codec/overlay_input_\(id).json"
        // Ack immediately so codec_core knows the panel actually appeared
        // (else it fast-falls-back to the native osascript dialog).
        try? "1".write(toFile: home + "/.codec/overlay_input_\(id).ack",
                       atomically: true, encoding: .utf8)
        if !promptText.isEmpty { field.placeholderString = promptText }
        field.stringValue = ""
        if let screen = NSScreen.main {
            let sr = screen.visibleFrame
            setFrameOrigin(NSPoint(x: sr.midX - frame.width / 2, y: sr.midY - frame.height / 2 + 140))
        }
        NSApp.setActivationPolicy(.regular)   // accessory apps need this to take keyboard focus
        NSApp.activate(ignoringOtherApps: true)
        makeKeyAndOrderFront(nil)
        field.becomeFirstResponder()
        if let editor = field.currentEditor() as? NSTextView {
            editor.insertionPointColor = Brand.orange
        }
    }

    @objc private func submit() { finish(field.stringValue) }
    override func cancelOperation(_ sender: Any?) { finish("") }   // Esc

    private func finish(_ text: String) {
        if !replyPath.isEmpty {
            let data = try? JSONSerialization.data(withJSONObject: ["text": text])
            try? data?.write(to: URL(fileURLWithPath: replyPath))
            replyPath = ""
        }
        orderOut(nil)
        NSApp.setActivationPolicy(.accessory)
    }
}

// MARK: - App Delegate
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private var overlay: OverlayPanel!
    private var inputPanel: InputPanel!
    private let poller = EventPoller()
    private var lastSkill = "none"
    private var isOn = true
    private var recentSkills: [String] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        overlay = OverlayPanel()
        inputPanel = InputPanel()

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
        let st = NSMenuItem(title: isOn ? "● Status: ON" : "● Status: OFF", action: nil, keyEquivalent: "")
        st.isEnabled = false
        menu.addItem(st)
        let sk = NSMenuItem(title: "Last: \(lastSkill)", action: nil, keyEquivalent: "")
        sk.isEnabled = false
        menu.addItem(sk)
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "🌐 Open Dashboard", action: #selector(openDashboard), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "💬 Open Chat", action: #selector(openChat), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "🎨 Open Vibe", action: #selector(openVibe), keyEquivalent: ""))
        menu.addItem(.separator())
        if !recentSkills.isEmpty {
            let rh = NSMenuItem(title: "Recent Skills:", action: nil, keyEquivalent: "")
            rh.isEnabled = false
            menu.addItem(rh)
            for skill in recentSkills.suffix(5) {
                let it = NSMenuItem(title: "  \(skill)", action: nil, keyEquivalent: "")
                it.isEnabled = false
                menu.addItem(it)
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
                self.overlay.configure(state: .recording,
                                       title: (json["title"] as? String) ?? "Listening",
                                       subtitle: (json["subtitle"] as? String) ?? "release to send")
            case "ptt_locked":
                self.overlay.configure(state: .recording, title: "REC LOCKED",
                                       subtitle: "tap F18 to stop")
            case "recording_stop", "live_stop", "hide":
                self.overlay.hide()
            case "transcribing":
                self.overlay.configure(state: .processing,
                                       title: (json["text"] as? String) ?? "Transcribing…",
                                       duration: (json["duration"] as? Double) ?? 0)
            case "refining":
                self.overlay.configure(state: .refining, title: "Refining…")
            case "live":
                self.overlay.configure(state: .live, title: "LIVE",
                                       subtitle: "press F5 to stop")
            case "toggle_on":
                self.isOn = true; self.buildMenu()
                if let btn = self.statusItem.button {
                    btn.image = NSImage(systemSymbolName: "bolt.fill", accessibilityDescription: "CODEC")
                    btn.image?.isTemplate = true
                }
                self.overlay.configure(state: .toggleOn, title: "CODEC",
                                       shortcuts: (json["shortcuts"] as? String) ?? "",
                                       duration: (json["duration"] as? Double) ?? 2.6)
            case "toggle_off":
                self.isOn = false; self.buildMenu()
                if let btn = self.statusItem.button {
                    btn.image = NSImage(systemSymbolName: "bolt.slash.fill", accessibilityDescription: "CODEC")
                    btn.image?.isTemplate = true
                }
                self.overlay.configure(state: .toggleOff, title: "SIGNING OUT",
                                       duration: (json["duration"] as? Double) ?? 1.8)
            case "skill_fired":
                let name = (json["name"] as? String) ?? "unknown"
                self.lastSkill = name
                self.recentSkills.append(name)
                if self.recentSkills.count > 10 { self.recentSkills.removeFirst() }
                self.buildMenu()
                self.overlay.configure(state: .notify(Brand.orange), title: name,
                                       subtitle: "skill", duration: (json["duration"] as? Double) ?? 2.0)
            case "notify":
                let text = (json["text"] as? String) ?? "CODEC"
                let dur = (json["duration"] as? Double) ?? 2.5
                let accent = (json["color"] as? String).map { Brand.from(hex: $0) } ?? Brand.orange
                self.overlay.configure(state: .notify(accent), title: text, duration: dur)
            case "input_request":
                let id = (json["id"] as? String) ?? "default"
                let promptText = (json["prompt"] as? String) ?? ""
                self.inputPanel.present(id: id, promptText: promptText)
            default:
                break
            }
        }
    }

    @objc private func openDashboard() { NSWorkspace.shared.open(URL(string: "http://localhost:8090")!) }
    @objc private func openChat()      { NSWorkspace.shared.open(URL(string: "http://localhost:8090/chat")!) }
    @objc private func openVibe()      { NSWorkspace.shared.open(URL(string: "http://localhost:8090/vibe")!) }
    @objc private func quitApp()       { NSApplication.shared.terminate(nil) }
}

// MARK: - Event Poller (reads ~/.codec/overlay_events.jsonl)
final class EventPoller: NSObject {
    private var timer: Timer?
    private var lastOffset: Int = 0
    weak var appDelegate: AppDelegate?
    private let eventFile: String = {
        FileManager.default.homeDirectoryForCurrentUser.path + "/.codec/overlay_events.jsonl"
    }()

    func start() {
        let dir = (eventFile as NSString).deletingLastPathComponent
        if !FileManager.default.fileExists(atPath: dir) {
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        }
        if !FileManager.default.fileExists(atPath: eventFile) {
            FileManager.default.createFile(atPath: eventFile, contents: nil,
                                           attributes: [.posixPermissions: 0o600])
        }
        // Start from the current end so we don't replay history on launch
        if let data = FileManager.default.contents(atPath: eventFile),
           let text = String(data: data, encoding: .utf8) {
            lastOffset = text.components(separatedBy: "\n").filter { !$0.isEmpty }.count
        }
        timer = Timer.scheduledTimer(withTimeInterval: 0.15, repeats: true) { [weak self] _ in
            self?.poll()
        }
    }

    private func poll() {
        guard let data = FileManager.default.contents(atPath: eventFile),
              let text = String(data: data, encoding: .utf8) else { return }
        let lines = text.components(separatedBy: "\n").filter { !$0.isEmpty }
        guard lines.count > lastOffset else {
            if lines.count < lastOffset { lastOffset = lines.count }  // file was rotated/truncated
            return
        }
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

// MARK: - Entry Point
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
