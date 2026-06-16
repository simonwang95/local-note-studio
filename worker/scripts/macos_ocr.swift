#!/usr/bin/env swift

import AppKit
import Foundation
import PDFKit
import Vision

struct PageResult: Codable {
    let page: Int
    let text: String
}

struct OCRResult: Codable {
    let source: String
    let pages: [PageResult]
}

enum OCRScriptError: Error, LocalizedError {
    case invalidArguments
    case unreadableSource(String)
    case unsupportedSource(String)
    case imageDecodeFailed(String)
    case pdfDecodeFailed(String)
    case visionFailed(String)

    var errorDescription: String? {
        switch self {
        case .invalidArguments:
            return "usage: macos_ocr.swift --source /path/to/file [--max-pages N]"
        case let .unreadableSource(path):
            return "source not found: \(path)"
        case let .unsupportedSource(path):
            return "unsupported OCR source: \(path)"
        case let .imageDecodeFailed(path):
            return "failed to decode image: \(path)"
        case let .pdfDecodeFailed(path):
            return "failed to decode pdf: \(path)"
        case let .visionFailed(message):
            return message
        }
    }
}

func parseArguments() throws -> (String, Int?) {
    let args = CommandLine.arguments.dropFirst()
    var source = ""
    var maxPages: Int?
    var index = args.startIndex
    while index < args.endIndex {
        let value = args[index]
        if value == "--source" {
            let next = args.index(after: index)
            guard next < args.endIndex else { throw OCRScriptError.invalidArguments }
            source = String(args[next])
            index = args.index(after: next)
            continue
        }
        if value == "--max-pages" {
            let next = args.index(after: index)
            guard next < args.endIndex, let parsed = Int(args[next]) else {
                throw OCRScriptError.invalidArguments
            }
            maxPages = max(1, parsed)
            index = args.index(after: next)
            continue
        }
        index = args.index(after: index)
    }
    guard !source.isEmpty else { throw OCRScriptError.invalidArguments }
    return (source, maxPages)
}

func cgImage(from image: NSImage) -> CGImage? {
    var rect = CGRect(origin: .zero, size: image.size)
    return image.cgImage(forProposedRect: &rect, context: nil, hints: nil)
}

func recognizeText(from cgImage: CGImage) throws -> String {
    let request = VNRecognizeTextRequest()
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    do {
        try handler.perform([request])
    } catch {
        throw OCRScriptError.visionFailed("Vision OCR failed: \(error.localizedDescription)")
    }

    let observations = request.results ?? []
    let lines = observations.compactMap { observation in
        observation.topCandidates(1).first?.string.trimmingCharacters(in: .whitespacesAndNewlines)
    }.filter { !$0.isEmpty }
    return lines.joined(separator: "\n")
}

func renderPDFPage(_ page: PDFPage, scale: CGFloat = 2.0) -> CGImage? {
    let bounds = page.bounds(for: .mediaBox)
    let targetSize = CGSize(width: max(bounds.width * scale, 1), height: max(bounds.height * scale, 1))
    let image = NSImage(size: targetSize)
    image.lockFocus()
    NSColor.white.set()
    NSBezierPath(rect: CGRect(origin: .zero, size: targetSize)).fill()
    guard let context = NSGraphicsContext.current?.cgContext else {
        image.unlockFocus()
        return nil
    }
    context.saveGState()
    context.scaleBy(x: scale, y: scale)
    page.draw(with: .mediaBox, to: context)
    context.restoreGState()
    image.unlockFocus()
    return cgImage(from: image)
}

func ocrImage(at url: URL) throws -> OCRResult {
    guard let image = NSImage(contentsOf: url), let cg = cgImage(from: image) else {
        throw OCRScriptError.imageDecodeFailed(url.path)
    }
    let text = try recognizeText(from: cg)
    return OCRResult(source: url.path, pages: [PageResult(page: 1, text: text)])
}

func ocrPDF(at url: URL, maxPages: Int?) throws -> OCRResult {
    guard let document = PDFDocument(url: url) else {
        throw OCRScriptError.pdfDecodeFailed(url.path)
    }
    let pageCount = maxPages.map { min($0, document.pageCount) } ?? document.pageCount
    var pages: [PageResult] = []
    for pageIndex in 0..<pageCount {
        guard let page = document.page(at: pageIndex) else { continue }
        if let directText = page.string?.trimmingCharacters(in: .whitespacesAndNewlines), !directText.isEmpty {
            pages.append(PageResult(page: pageIndex + 1, text: directText))
            continue
        }
        guard let rendered = renderPDFPage(page) else {
            pages.append(PageResult(page: pageIndex + 1, text: ""))
            continue
        }
        let text = try recognizeText(from: rendered)
        pages.append(PageResult(page: pageIndex + 1, text: text))
    }
    return OCRResult(source: url.path, pages: pages)
}

do {
    let (source, maxPages) = try parseArguments()
    let url = URL(fileURLWithPath: source)
    guard FileManager.default.fileExists(atPath: url.path) else {
        throw OCRScriptError.unreadableSource(url.path)
    }
    let ext = url.pathExtension.lowercased()
    let result: OCRResult
    if ext == "pdf" {
        result = try ocrPDF(at: url, maxPages: maxPages)
    } else if ["png", "jpg", "jpeg", "webp", "heic", "bmp", "gif", "tif", "tiff"].contains(ext) {
        result = try ocrImage(at: url)
    } else {
        throw OCRScriptError.unsupportedSource(url.path)
    }

    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .withoutEscapingSlashes]
    let data = try encoder.encode(result)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}
