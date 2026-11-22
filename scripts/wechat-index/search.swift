// Drive the 微信指数 mini-program: click at (x, y) and optionally type/paste.
// Usage:
//   search.swift click <x> <y>
//   search.swift type <text>      (paste via clipboard, then Return)
//
// Synthetic events need Accessibility permission for the calling app; a
// missing grant shows up as "nothing happened" rather than an error.
import CoreGraphics
import Foundation

func click(_ x: Double, _ y: Double) {
    let p = CGPoint(x: x, y: y)
    CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: p, mouseButton: .left)?.post(tap: .cghidEventTap)
    usleep(120_000)
    CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: p, mouseButton: .left)?.post(tap: .cghidEventTap)
    usleep(60_000)
    CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: p, mouseButton: .left)?.post(tap: .cghidEventTap)
}

let a = CommandLine.arguments
guard a.count >= 2 else {
    FileHandle.standardError.write("usage: search.swift click <x> <y> | type <text>\n".data(using: .utf8)!)
    exit(2)
}

switch a[1] {
case "click":
    guard a.count >= 4, let x = Double(a[2]), let y = Double(a[3]) else { exit(2) }
    click(x, y)
case "type":
    guard a.count >= 3 else { exit(2) }
    // The caller sets the clipboard; ⌘V + Return is what a person does.
    let src = CGEventSource(stateID: .hidSystemState)
    func key(_ code: CGKeyCode, _ flags: CGEventFlags = []) {
        let down = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: true)
        down?.flags = flags
        down?.post(tap: .cghidEventTap)
        usleep(40_000)
        let up = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: false)
        up?.flags = flags
        up?.post(tap: .cghidEventTap)
    }
    key(9, .maskCommand)   // ⌘V
    usleep(300_000)
    key(36)                // Return
default:
    exit(2)
}
