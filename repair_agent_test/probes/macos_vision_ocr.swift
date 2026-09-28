// macOS Vision OCR probe -- an independent transcription engine for pages that have no
// text layer, so a scanned page can still be *measured* rather than guessed at.
//
// Why this exists: a scanned paper carries no text objects, so `pdftohtml` (the engine used
// as ground truth for digital papers) has nothing to report. Vision is Apple's built-in
// transcriber: it needs no download, no model directory and no network, and it is
// independent of both PP-DocLayout and MinerU.
//
// This is a *measurement* tool. It transcribes letters (shape -> code point); it is not a
// rule in the pipeline, and its output is never used to decide what a question says.
//
// Build: probes/build_macos_vision_ocr.sh   (needs Xcode command line tools)
// Use:   macOS_vision_ocr IMAGE.png   ->  x0 \t y0 \t x1 \t y1 \t text   (pixels, top-left origin)

import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write("usage: macOS_vision_ocr <image>\n".data(using: .utf8)!)
    exit(1)
}
guard let image = NSImage(contentsOfFile: args[1]),
      let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("cannot load \(args[1])\n".data(using: .utf8)!)
    exit(1)
}

let width = Double(cg.width), height = Double(cg.height)
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hant", "en-US"]
request.usesLanguageCorrection = false

let handler = VNImageRequestHandler(cgImage: cg, options: [:])
try handler.perform([request])

for observation in (request.results ?? []) {
    guard let candidate = observation.topCandidates(1).first else { continue }
    let b = observation.boundingBox          // normalised, origin bottom-left
    let x0 = b.minX * width, x1 = b.maxX * width
    let y0 = (1 - b.maxY) * height, y1 = (1 - b.minY) * height
    print(String(format: "%.1f\t%.1f\t%.1f\t%.1f\t%@", x0, y0, x1, y1, candidate.string))
}
