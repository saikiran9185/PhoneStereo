// Reads what's written on each sticky note with Apple's Vision text recognition.
// Usage: ocr <comma-separated words to prefer> <image> [<image> ...]
// Prints one line per image: "<path>\t<candidate>|<confidence>\t<candidate>|<confidence>..."
import AppKit
import Foundation
import Vision

let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write("usage: ocr <words> <image>...\n".data(using: .utf8)!)
    exit(2)
}
let words = args[1].split(separator: ",").map(String.init)

for path in args[2...] {
    var out = [path]
    if let image = NSImage(contentsOfFile: path),
       let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) {
        // Words read best with language correction; lone letters (chords like "G") only without it.
        for (level, correct) in [(VNRequestTextRecognitionLevel.accurate, true), (.accurate, false), (.fast, false)] {
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = level
            request.usesLanguageCorrection = correct
            if correct { request.customWords = words }
            request.minimumTextHeight = 0.1
            try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
            for observation in request.results ?? [] {
                for candidate in observation.topCandidates(3) {
                    let text = candidate.string.replacingOccurrences(of: "\t", with: " ")
                    out.append("\(text)|\(String(format: "%.2f", candidate.confidence))")
                }
            }
        }
    }
    print(out.joined(separator: "\t"))
}
