import AppKit

// MARK: - Types
struct AXNode: Codable {
    let role: String
    let title: String
    let value: String
    let label: String
    let enabled: Bool
    let frame: [String: CGFloat]
    var children: [AXNode]
}

// MARK: - Selector Parser
// Parses: "role:AXButton name:OK" or "role:AXTextField" or "label:Search"
struct AXSelector {
    var role: String?
    var name: String?
    var label: String?
    var value: String?

    static func parse(_ s: String) -> AXSelector {
        var sel = AXSelector()
        for part in s.components(separatedBy: " ") {
            let kv = part.components(separatedBy: ":")
            guard kv.count >= 2 else { continue }
            let key = kv[0].lowercased()
            let val = kv[1...].joined(separator: ":")
            switch key {
            case "role":  sel.role  = val
            case "name":  sel.name  = val
            case "label": sel.label = val
            case "value": sel.value = val
            default: break
            }
        }
        return sel
    }

    func matches(_ node: AXNode) -> Bool {
        if let r = role,  !node.role.contains(r)  { return false }
        if let n = name,  !node.title.lowercased().contains(n.lowercased()) &&
                          !node.label.lowercased().contains(n.lowercased()) { return false }
        if let l = label, !node.label.lowercased().contains(l.lowercased()) { return false }
        if let v = value, !node.value.lowercased().contains(v.lowercased()) { return false }
        return true
    }
}

// MARK: - AX Helpers
func getString(_ element: AXUIElement, _ attr: String) -> String {
    var value: AnyObject?
    AXUIElementCopyAttributeValue(element, attr as CFString, &value)
    if let s = value as? String { return s }
    if let v = value { return "\(v)" }
    return ""
}

func getBool(_ element: AXUIElement, _ attr: String) -> Bool {
    var value: AnyObject?
    AXUIElementCopyAttributeValue(element, attr as CFString, &value)
    if let b = value as? Bool { return b }
    if let n = value as? Int { return n != 0 }
    return true
}

func getFrame(_ element: AXUIElement) -> [String: CGFloat] {
    var posVal: AnyObject?
    var sizeVal: AnyObject?
    AXUIElementCopyAttributeValue(element, kAXPositionAttribute as CFString, &posVal)
    AXUIElementCopyAttributeValue(element, kAXSizeAttribute as CFString, &sizeVal)
    var pos = CGPoint.zero
    var size = CGSize.zero
    if let pv = posVal { AXValueGetValue(pv as! AXValue, .cgPoint, &pos) }
    if let sv = sizeVal { AXValueGetValue(sv as! AXValue, .cgSize, &size) }
    return ["x": pos.x, "y": pos.y, "w": size.width, "h": size.height]
}

func buildNode(_ element: AXUIElement, depth: Int) -> AXNode {
    let role  = getString(element, kAXRoleAttribute as String)
    let title = getString(element, kAXTitleAttribute as String)
    let value = getString(element, kAXValueAttribute as String)
    let label = getString(element, kAXDescriptionAttribute as String)
    let enabled = getBool(element, kAXEnabledAttribute as String)
    let frame = getFrame(element)

    var children: [AXNode] = []
    if depth > 0 {
        var childrenVal: AnyObject?
        AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &childrenVal)
        if let kids = childrenVal as? [AXUIElement] {
            for kid in kids.prefix(50) {
                children.append(buildNode(kid, depth: depth - 1))
            }
        }
    }
    return AXNode(role: role, title: title, value: value, label: label,
                  enabled: enabled, frame: frame, children: children)
}

func findElements(_ element: AXUIElement, selector: AXSelector, depth: Int, results: inout [AXUIElement]) {
    let node = buildNode(element, depth: 0)
    if selector.matches(node) {
        results.append(element)
    }
    if depth > 0 {
        var childrenVal: AnyObject?
        AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &childrenVal)
        if let kids = childrenVal as? [AXUIElement] {
            for kid in kids {
                findElements(kid, selector: selector, depth: depth - 1, results: &results)
            }
        }
    }
}

// MARK: - JSON Output
func toJSON<T: Encodable>(_ v: T) -> String {
    let enc = JSONEncoder()
    enc.outputFormatting = [.prettyPrinted]
    guard let d = try? enc.encode(v) else { return "{}" }
    return String(data: d, encoding: .utf8) ?? "{}"
}

// MARK: - Main
var pid: pid_t = 0
var action = "tree"
var selector = ""
var depth = 3
var args = CommandLine.arguments.dropFirst()

var it = args.makeIterator()
while let arg = it.next() {
    switch arg {
    case "--pid":      if let v = it.next() { pid = pid_t(v) ?? 0 }
    case "--action":   if let v = it.next() { action = v }
    case "--selector": if let v = it.next() { selector = v }
    case "--depth":    if let v = it.next() { depth = Int(v) ?? 3 }
    default: break
    }
}

// If no PID, use frontmost app
if pid == 0 {
    if let frontApp = NSWorkspace.shared.frontmostApplication {
        pid = frontApp.processIdentifier
    }
}

guard pid > 0 else {
    print(#"{"error":"No PID provided and no frontmost app found"}"#)
    exit(1)
}

let appElement = AXUIElementCreateApplication(pid)

struct ErrorResult: Codable { let error: String }
struct SuccessResult: Codable { let status: String; let message: String }
struct TreeResult: Codable { let pid: Int; let tree: AXNode }
struct FindResult: Codable { let count: Int; let elements: [AXNode] }

switch action {
case "tree":
    let root = buildNode(appElement, depth: depth)
    print(toJSON(TreeResult(pid: Int(pid), tree: root)))

case "find":
    guard !selector.isEmpty else {
        print(toJSON(ErrorResult(error: "No selector provided. Use --selector 'role:AXButton name:OK'")))
        exit(1)
    }
    let sel = AXSelector.parse(selector)
    var found: [AXUIElement] = []
    findElements(appElement, selector: sel, depth: depth, results: &found)
    let nodes = found.map { buildNode($0, depth: 0) }
    print(toJSON(FindResult(count: nodes.count, elements: nodes)))

case "click":
    let sel = selector.isEmpty ? AXSelector() : AXSelector.parse(selector)
    var found: [AXUIElement] = []
    if selector.isEmpty {
        // Click at first focusable element - just report error
        print(toJSON(ErrorResult(error: "Selector required for click. Use --selector 'role:AXButton name:OK'")))
        exit(1)
    }
    findElements(appElement, selector: sel, depth: depth, results: &found)
    if let first = found.first {
        let result = AXUIElementPerformAction(first, kAXPressAction as CFString)
        if result == .success {
            print(toJSON(SuccessResult(status: "ok", message: "Clicked \(selector)")))
        } else {
            print(toJSON(ErrorResult(error: "Click failed: AXError \(result.rawValue)")))
        }
    } else {
        print(toJSON(ErrorResult(error: "Element not found: \(selector)")))
    }

case "read":
    let sel = selector.isEmpty ? AXSelector() : AXSelector.parse(selector)
    var found: [AXUIElement] = []
    findElements(appElement, selector: sel, depth: depth, results: &found)
    if let first = found.first {
        let node = buildNode(first, depth: 0)
        let reading = node.value.isEmpty ? node.title : node.value
        struct ReadResult: Codable { let value: String; let title: String }
        print(toJSON(ReadResult(value: node.value, title: node.title)))
    } else {
        print(toJSON(ErrorResult(error: "Element not found: \(selector)")))
    }

default:
    print(toJSON(ErrorResult(error: "Unknown action: \(action). Use tree|find|click|read")))
}
