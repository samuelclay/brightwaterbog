import Foundation
import Vision
import AppKit

struct Box: Codable {
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

struct Line: Codable {
    let text: String
    let confidence: Float
    let box: Box
}

struct Result: Codable {
    let image: String
    let width: Int
    let height: Int
    let lines: [Line]
    let fullText: String
}

func recognize(_ path: String) throws -> Result {
    guard let image = NSImage(contentsOfFile: path),
          let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        throw NSError(domain: "PurpleCarrotOCR", code: 1, userInfo: [NSLocalizedDescriptionKey: "Could not load image: \(path)"])
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["en-US"]
    request.minimumTextHeight = 0.006

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try handler.perform([request])

    let observations = request.results ?? []
    let lines = observations.compactMap { observation -> Line? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let rect = observation.boundingBox
        return Line(
            text: candidate.string,
            confidence: candidate.confidence,
            box: Box(
                x: rect.origin.x,
                y: rect.origin.y,
                width: rect.width,
                height: rect.height
            )
        )
    }.sorted { lhs, rhs in
        let yDifference = abs(lhs.box.y - rhs.box.y)
        if yDifference > 0.012 { return lhs.box.y > rhs.box.y }
        return lhs.box.x < rhs.box.x
    }

    return Result(
        image: path,
        width: cgImage.width,
        height: cgImage.height,
        lines: lines,
        fullText: lines.map(\.text).joined(separator: "\n")
    )
}

let arguments = Array(CommandLine.arguments.dropFirst())
guard !arguments.isEmpty else {
    fputs("Usage: vision_ocr IMAGE [IMAGE ...]\n", stderr)
    exit(2)
}

do {
    let results = try arguments.map(recognize)
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    let output = try encoder.encode(results)
    FileHandle.standardOutput.write(output)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    fputs("OCR failed: \(error.localizedDescription)\n", stderr)
    exit(1)
}
