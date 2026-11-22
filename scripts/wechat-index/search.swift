// Drive the 微信指数 mini-program: click at (x, y), type text, or both.
//
// Usage:
//   search.swift click <x> <y> [--pid <pid>]
//   search.swift type <text>  [--pid <pid>]
//   search.swift windows                  (list WeChat windows as JSON)
//
// Why --pid matters: the default tap (.cghidEventTap) delivers to whatever app
// is frontmost.  On a busy desktop WeChat is regularly buried under other
// windows and another app steals focus between "activate" and the click, so the
// click lands in the wrong place or nowhere.  Posting the event straight to
// WeChat's pid skips hit-testing against the frontmost window and works while
// it is in the background.
//
// Synthetic events need Accessibility permission for the calling app; a missing
// grant shows up as "nothing happened" rather than an error.
import CoreGraphics
import Foundation

func click(_ x: Double, _ y: Double, pid: pid_t?) {
    let p = CGPoint(x: x, y: y)
    let src = CGEventSource(stateID: .hidSystemState)
    func post(_ type: CGEventType) {
        guard let e = CGEvent(mouseEventSource: src, mouseType: type,
                              mouseCursorPosition: p, mouseButton: .left) else { return }
        if let pid { e.postToPid(pid) } else { e.post(tap: .cghidEventTap) }
    }
    post(.mouseMoved)
    usleep(120_000)
    post(.leftMouseDown)
    usleep(60_000)
    post(.leftMouseUp)
}

func typeText(pid: pid_t?) {
    let src = CGEventSource(stateID: .hidSystemState)
    func key(_ code: CGKeyCode, _ flags: CGEventFlags = []) {
        let down = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: true)
        down?.flags = flags
        if let pid { down?.postToPid(pid) } else { down?.post(tap: .cghidEventTap) }
        usleep(40_000)
        let up = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: false)
        up?.flags = flags
        if let pid { up?.postToPid(pid) } else { up?.post(tap: .cghidEventTap) }
    }
    key(9, .maskCommand)   // ⌘V — the caller owns the clipboard
    usleep(300_000)
    key(36)                // Return
}

func wechatWindows() {
    let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let raw = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] else {
        print("[]"); return
    }
    var out: [[String: Any]] = []
    for w in raw {
        let owner = w[kCGWindowOwnerName as String] as? String ?? ""
        guard owner == "WeChat" || owner == "Weixin" else { continue }
        let b = w[kCGWindowBounds as String] as? [String: Any] ?? [:]
        out.append([
            "name": w[kCGWindowName as String] as? String ?? "",
            "id": w[kCGWindowNumber as String] as? Int ?? 0,
            "pid": w[kCGWindowOwnerPID as String] as? Int ?? 0,
            "x": (b["X"] as? NSNumber)?.doubleValue ?? 0,
            "y": (b["Y"] as? NSNumber)?.doubleValue ?? 0,
            "w": (b["Width"] as? NSNumber)?.doubleValue ?? 0,
            "h": (b["Height"] as? NSNumber)?.doubleValue ?? 0,
        ])
    }
    let d = try? JSONSerialization.data(withJSONObject: out)
    print(String(data: d ?? Data("[]".utf8), encoding: .utf8) ?? "[]")
}

let a = CommandLine.arguments
guard a.count >= 2 else {
    FileHandle.standardError.write("usage: search.swift click <x> <y> | type <text> | windows\n".data(using: .utf8)!)
    exit(2)
}

// --pid may appear anywhere after the subcommand.
var args = Array(a.dropFirst())
var pid: pid_t?
if let i = args.firstIndex(of: "--pid"), i + 1 < args.count, let v = Int32(args[i + 1]) {
    pid = v
    args.removeSubrange(i...(i + 1))
}

switch args.first {
case "click":
    guard args.count >= 3, let x = Double(args[1]), let y = Double(args[2]) else { exit(2) }
    click(x, y, pid: pid)
case "type":
    typeText(pid: pid)
case "windows":
    wechatWindows()
case "key":
    // key <keycode> [--cmd] — arbitrary shortcut, useful because keyboard
    // events DO reach a specific pid while mouse events still hit-test.
    guard args.count >= 2, let code = UInt16(args[1]) else { exit(2) }
    let src = CGEventSource(stateID: .hidSystemState)
    let flags: CGEventFlags = args.contains("--cmd") ? .maskCommand : []
    let down = CGEvent(keyboardEventSource: src, virtualKey: CGKeyCode(code), keyDown: true)
    down?.flags = flags
    if let pid { down?.postToPid(pid) } else { down?.post(tap: .cghidEventTap) }
    usleep(40_000)
    let up = CGEvent(keyboardEventSource: src, virtualKey: CGKeyCode(code), keyDown: false)
    up?.flags = flags
    if let pid { up?.postToPid(pid) } else { up?.post(tap: .cghidEventTap) }
default:
    exit(2)
}
