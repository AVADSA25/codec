// Entry point. Swift permits top-level expressions ONLY in a file named
// main.swift, and the launcher became multi-file when the app gained a real
// window (DashboardWindow.swift), so the bootstrap moved here.

import AppKit

let app = NSApplication.shared
let delegate = Launcher()
app.delegate = delegate
// .regular now: CODEC owns a real window, so it belongs in the Dock and the
// app switcher like any Mac app. The menu-bar item stays as the always-there
// handle for an agent that keeps running after the window is closed.
app.setActivationPolicy(.regular)
app.run()
