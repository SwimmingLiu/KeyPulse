// wxdrive — drive WeChat without racing other apps for focus.
//
// The problem this exists for: on a desktop running several agents, WeChat
// loses focus between "activate" and the synthetic click.  Calling the Swift
// helper through `swift file.swift` compiles it on every invocation (~1–2 s),
// which is more than enough time for another window to take focus back, so the
// click lands elsewhere or nowhere.  This binary is compiled once and does
// activate → wait-for-frontmost → action inside one process, so the gap is
// milliseconds.
//
// Usage:
//   wxdrive activate
//   wxdrive click <x> <y>          (global screen points)
//   wxdrive clickrel <fx> <fy>     (fraction of WeChat's window; computed at
//                                   click time, because the window moves)
//   wxdrive clickoff <dx> <dy>     (fixed offset from the window's top-left;
//                                   the icon rail is a fixed size, so this is
//                                   what survives a window resize)
//   wxdrive type <text>            (via clipboard: ⌘V then Return)
//   wxdrive key <keycode> [--cmd]
//   wxdrive pastequit              (⌘A then ⌘Q inside the app: no focus needed)
//   wxdrive windows                (JSON: WeChat windows)
//   wxdrive frontmost              (bundle id of the frontmost app)
//
// Exit codes: 0 done, 3 could not become frontmost, 2 bad usage.
import AppKit
import CoreGraphics
import Foundation

let WECHAT_BUNDLE_IDS = ["com.tencent.xinWeChat", "com.tencent.xinWeChat.mac"]

func wechatApp() -> NSRunningApplication? {
    for app in NSWorkspace.shared.runningApplications {
        if let id = app.bundleIdentifier, WECHAT_BUNDLE_IDS.contains(id) {
            return app
        }
    }
    // Fall back to matching the localized name.
    return NSWorkspace.shared.runningApplications.first {
        ($0.localizedName ?? "") == "WeChat" || ($0.localizedName ?? "") == "Weixin"
             || ($0.localizedName ?? "") == "微信"
    }
}

@discardableResult
func activateWeChat(timeout: TimeInterval = 5.0, attempts: Int = 3) -> Bool {
    guard let app = wechatApp() else { return false }
    let target = app.processIdentifier
    func isFront() -> Bool {
        NSWorkspace.shared.frontmostApplication?.processIdentifier == target
    }
    if isFront() { return true }
    for _ in 0..<attempts {
        // Another window can take focus back, so re-assert it and re-check.
        app.activate(options: [.activateAllWindows])
        // NSRunningApplication.activate alone loses to apps that grab focus
        // aggressively (other agents on this desktop do).  Asking System
        // Events in-process is the second voice, and it is what actually wins.
        if !isFront() { _ = applescript("tell application \"System Events\" to tell process \"WeChat\" to set frontmost to true") }
        let deadline = Date().addingTimeInterval(timeout / Double(attempts))
        while Date() < deadline {
            if isFront() {
                // Let the compositor settle so the click lands on the raised window.
                usleep(80_000)
                return true
            }
            usleep(20_000)
        }
    }
    return isFront()
}

/// Run AppleScript in-process (no fork), used as a second way to take focus.
@discardableResult
func applescript(_ source: String) -> Bool {
    guard let script = NSAppleScript(source: source) else { return false }
    var error: NSDictionary?
    script.executeAndReturnError(&error)
    return error == nil
}

func wechatPid() -> pid_t? {
    wechatApp()?.processIdentifier
}

func click(_ x: Double, _ y: Double) {
    let p = CGPoint(x: x, y: y)
    guard let src = CGEventSource(stateID: .hidSystemState) else { return }
    let moves: [(CGEventType, useconds_t)] = [(.mouseMoved, 60_000), (.leftMouseDown, 50_000), (.leftMouseUp, 0)]
    for (type, delay) in moves {
        CGEvent(mouseEventSource: src, mouseType: type, mouseCursorPosition: p, mouseButton: .left)?
            .post(tap: .cghidEventTap)
        if delay > 0 { usleep(delay) }
    }
}

func typeText() {
    guard let src = CGEventSource(stateID: .hidSystemState) else { return }
    func key(_ code: CGKeyCode, _ flags: CGEventFlags = []) {
        let down = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: true)
        down?.flags = flags
        down?.post(tap: .cghidEventTap)
        usleep(30_000)
        let up = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: false)
        up?.flags = flags
        up?.post(tap: .cghidEventTap)
    }
    key(9, .maskCommand)   // ⌘V
    usleep(250_000)
    key(36)                // Return
}

/// Bounds of WeChat's main window, read fresh at call time.
func wechatWindowBounds() -> CGRect? {
    let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let raw = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] else {
        return nil
    }
    var best: CGRect?
    for w in raw {
        let owner = w[kCGWindowOwnerName as String] as? String ?? ""
        guard owner == "WeChat" || owner == "Weixin" || owner == "微信" else { continue }
        let b = w[kCGWindowBounds as String] as? [String: Any] ?? [:]
        let r = CGRect(x: (b["X"] as? NSNumber)?.doubleValue ?? 0,
                       y: (b["Y"] as? NSNumber)?.doubleValue ?? 0,
                       width: (b["Width"] as? NSNumber)?.doubleValue ?? 0,
                       height: (b["Height"] as? NSNumber)?.doubleValue ?? 0)
        if r.width < 200 || r.height < 200 { continue }
        if best == nil || r.width * r.height > best!.width * best!.height { best = r }
    }
    return best
}

func wechatWindows() {
    let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let raw = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] else {
        print("[]"); return
    }
    var out: [[String: Any]] = []
    for w in raw {
        let owner = w[kCGWindowOwnerName as String] as? String ?? ""
        guard owner == "WeChat" || owner == "Weixin" || owner == "微信" else { continue }
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

var args = Array(CommandLine.arguments.dropFirst())
guard let cmd = args.first else { exit(2) }
args.removeFirst()

switch cmd {
case "activate":
    exit(activateWeChat() ? 0 : 3)
case "frontmost":
    print(NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? "none")
case "windows":
    wechatWindows()
case "click":
    guard args.count >= 2, let x = Double(args[0]), let y = Double(args[1]) else { exit(2) }
    // Re-activate immediately before clicking: that is the whole point.
    guard activateWeChat() else {
        FileHandle.standardError.write("could not take focus\n".data(using: .utf8)!)
        exit(3)
    }
    click(x, y)
case "clickrel":
    // Resolve the window bounds in the same process as the click.  A separate
    // read-then-click leaves a gap in which the window can move (a stray click
    // on the title bar drags it), and the click then lands somewhere else.
    guard args.count >= 2, let fx = Double(args[0]), let fy = Double(args[1]) else { exit(2) }
    guard activateWeChat() else {
        FileHandle.standardError.write("could not take focus\n".data(using: .utf8)!)
        exit(3)
    }
    guard let bounds = wechatWindowBounds() else {
        FileHandle.standardError.write("no WeChat window\n".data(using: .utf8)!)
        exit(4)
    }
    click(bounds.origin.x + fx * bounds.width, bounds.origin.y + fy * bounds.height)
case "clickoff":
    guard args.count >= 2, let dx = Double(args[0]), let dy = Double(args[1]) else { exit(2) }
    guard activateWeChat() else {
        FileHandle.standardError.write("could not take focus\n".data(using: .utf8)!)
        exit(3)
    }
    guard let bounds = wechatWindowBounds() else {
        FileHandle.standardError.write("no WeChat window\n".data(using: .utf8)!)
        exit(4)
    }
    click(bounds.origin.x + dx, bounds.origin.y + dy)
case "type":
    guard activateWeChat() else { exit(3) }
    typeText()
case "pastequit":
    // WeChat is a Mac app with a normal Quit command, and AppKit launches that
    // still run on Relaunch.  ⌘A then ⌘Q is enough to relaunch the whole client
    // without ever clicking a window — which is the only reliable way to make
    // the mini-program run its login flow again.
    guard activateWeChat() else {
        FileHandle.standardError.write("could not take focus\n".data(using: .utf8)!)
        exit(3)
    }
    guard let src = CGEventSource(stateID: .hidSystemState) else { exit(1) }
    func key(_ code: CGKeyCode, _ flags: CGEventFlags) {
        let down = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: true)
        down?.flags = flags
        down?.post(tap: .cghidEventTap)
        usleep(30_000)
        let up = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: false)
        up?.flags = flags
        up?.post(tap: .cghidEventTap)
    }
    key(0, .maskCommand)   // ⌘A
    usleep(60_000)
    key(12, .maskCommand)  // ⌘Q
case "key":
    guard let code = UInt16(args[0]) else { exit(2) }
    guard activateWeChat() else { exit(3) }
    guard let src = CGEventSource(stateID: .hidSystemState) else { exit(1) }
    let flags: CGEventFlags = args.contains("--cmd") ? .maskCommand : []
    let down = CGEvent(keyboardEventSource: src, virtualKey: CGKeyCode(code), keyDown: true)
    down?.flags = flags
    down?.post(tap: .cghidEventTap)
    usleep(30_000)
    let up = CGEvent(keyboardEventSource: src, virtualKey: CGKeyCode(code), keyDown: false)
    up?.flags = flags
    up?.post(tap: .cghidEventTap)
default:
    exit(2)
}
