import Foundation
import Vision

// Local OCR: only recognized text and confidence leave this process.
guard CommandLine.arguments.count == 2 else { exit(2) }
do {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    request.automaticallyDetectsLanguage = true
    let handler = VNImageRequestHandler(url: URL(fileURLWithPath: CommandLine.arguments[1]))
    try handler.perform([request])
    let lines = (request.results ?? []).compactMap { observation -> [String: Any]? in
        guard let text = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return ["text": text.string, "confidence": text.confidence,
                "bbox": [box.minX, 1 - box.maxY, box.maxX, 1 - box.minY]]
    }
    let data = try JSONSerialization.data(withJSONObject: lines)
    print(String(decoding: data, as: UTF8.self))
} catch {
    FileHandle.standardError.write(Data("Local OCR failed: \(error)\n".utf8))
    exit(1)
}
