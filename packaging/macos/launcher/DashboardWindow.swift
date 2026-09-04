// The CODEC app window.
//
// WHY (2026-09-04): CODEC's UI is a FastAPI-served dashboard on :8090. The
// launcher used to `NSWorkspace.open` that URL, so "the Mac app" was a website
// in the user's default browser with an app icon in front of it. Reported, and
// fairly: "it running inside google chrome!!! what the whole point? aint we
// building a app???"
//
// So the dashboard now renders inside the app itself: a real NSWindow, real
// title bar, CODEC's icon in the Dock, no address bar, no tabs, no other
// browser's cookies or extensions. Same server, same page — but the app IS the
// product rather than a shortcut to one.
//
// It is a WKWebView pointed at localhost, NOT an embedded browser: navigation
// away from the local origin is refused and handed to the real browser, so a
// link in a chat reply opens in Safari/Chrome rather than turning this window
// into a rogue browser with no address bar.

import AppKit
import WebKit

final class DashboardWindowController: NSObject, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow?
    private var webView: WKWebView?
    private let url: URL
    /// Everything the app is allowed to render in-window. Anything else is a
    /// link out, and belongs in the user's browser.
    private let allowedHosts: Set<String> = ["127.0.0.1", "localhost", "::1"]

    init(url: URL) {
        self.url = url
        super.init()
    }

    /// Bring the window up, creating it on first use. Safe to call repeatedly —
    /// the menu item and the dock icon both route here.
    func show() {
        if let w = window {
            w.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }

        let config = WKWebViewConfiguration()
        // The dashboard stores its theme + prefs in localStorage; a non-persistent
        // store would silently reset them on every launch.
        config.websiteDataStore = .default()

        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = false
        web.setValue(false, forKey: "drawsBackground")   // no white flash before the dark page paints
        web.load(URLRequest(url: url))
        webView = web

        let w = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1180, height: 800),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered, defer: false
        )
        w.title = "CODEC"
        w.titlebarAppearsTransparent = true
        w.titleVisibility = .hidden
        w.isReleasedWhenClosed = false          // closing must not destroy the agent
        w.minSize = NSSize(width: 900, height: 600)
        w.delegate = self
        w.contentView = web
        w.center()
        w.setFrameAutosaveName("CODECDashboardWindow")   // remember size/position
        window = w

        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func reload() { webView?.reload() }

    /// Closing the window leaves the agent running in the menu bar — the fleet
    /// must not stop because someone closed a window. Reopened from the menu.
    func windowWillClose(_ notification: Notification) {
        window = nil
        webView = nil
    }

    // MARK: - Navigation policy

    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let target = navigationAction.request.url else { return decisionHandler(.allow) }
        if let host = target.host, allowedHosts.contains(host) {
            return decisionHandler(.allow)
        }
        // An outbound link (a source in a web_search reply, a Google Doc) belongs
        // in the real browser, with its address bar and the user's session.
        if target.scheme == "http" || target.scheme == "https" || target.scheme == "mailto" {
            NSWorkspace.shared.open(target)
        }
        decisionHandler(.cancel)
    }

    /// target="_blank" has no window to open into here; send it out too.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let target = navigationAction.request.url { NSWorkspace.shared.open(target) }
        return nil
    }

    /// The dashboard is served by a fleet that may still be binding its port when
    /// the window opens. Retry briefly rather than showing a dead page.
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        retryLoad()
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        retryLoad()
    }

    private var retries = 0
    private func retryLoad() {
        guard retries < 15 else { return }   // ~30s, then leave the error visible
        retries += 1
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            guard let self else { return }
            self.webView?.load(URLRequest(url: self.url))
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { retries = 0 }
}
