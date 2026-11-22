// OCR a screenshot with the macOS Vision framework and print JSON.
// Usage: ocr_image.swift <image.png>
// Output: {"ok": true, "lines": [{"text": "...", "confidence": 0.98, "y": 0.42}, ...]}
// y is the normalised vertical position (0 = top), which the parser uses to
// associate a number with the label printed above it.
import Foundation
import Vision
import AppKit

struct Line: Codable { let text: String; let confidence: Float; let y: Double }

guard CommandLine.arguments.count > 1 else {
    print(#"{"ok":false,"error":"usage: ocr_image.swift <image>"}"#)
    exit(2)
}
let path = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: path),
      let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    print(#"{"ok":false,"error":"cannot load image"}"#)
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = false

let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do { try handler.perform([request]) } catch {
    print("{\"ok\":false,\"error\":\"\(error.localizedDescription)\"}")
    exit(1)
}

var lines: [Line] = []
for observation in request.results ?? [] {
    guard let candidate = observation.topCandidates(1).first else { continue }
    lines.append(Line(text: candidate.string,
                      confidence: candidate.confidence,
                      y: 1.0 - Double(observation.boundingBox.midY)))
}
lines.sort { $0.y < $1.y }

// A typed result rather than a mixed dictionary literal: Swift cannot infer a
// generic Value for ["ok": Bool, "lines": [Line]].
struct OCRResult: Codable { let ok: Bool; let lines: [Line] }

let encoder = JSONEncoder()
encoder.outputFormatting = [.withoutEscapingSlashes]
let payload = try encoder.encode(OCRResult(ok: true, lines: lines))
FileHandle.standardOutput.write(payload)
