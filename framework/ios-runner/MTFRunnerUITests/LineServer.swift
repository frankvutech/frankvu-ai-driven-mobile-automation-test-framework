import Darwin
import Foundation

/// A minimal blocking TCP server for newline-delimited JSON, one request per connection.
///
/// It runs on the XCTest main thread (XCUITest APIs must be called there) and binds to the loopback
/// interface only. The simulator shares the Mac's network stack, so the host reaches it at
/// 127.0.0.1:<port> without any forwarding.
final class LineServer {
    enum ServerError: Error { case socket(String) }

    private let port: UInt16
    private var listener: Int32 = -1
    private static let maxRequestBytes = 1 << 20

    init(port: UInt16) { self.port = port }

    deinit { if listener >= 0 { close(listener) } }

    func start() throws {
        listener = socket(AF_INET, SOCK_STREAM, 0)
        guard listener >= 0 else { throw ServerError.socket("socket() failed: \(errno)") }
        var reuse: Int32 = 1
        setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = port.bigEndian
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(listener, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        guard bound == 0 else { throw ServerError.socket("bind(127.0.0.1:\(port)) failed: \(errno)") }
        guard listen(listener, 4) == 0 else { throw ServerError.socket("listen() failed: \(errno)") }
    }

    /// Waits up to `timeoutMs` for a connection.
    func accept(timeoutMs: Int32) -> Int32? {
        var descriptor = pollfd(fd: listener, events: Int16(POLLIN), revents: 0)
        guard poll(&descriptor, 1, timeoutMs) > 0 else { return nil }
        let client = Darwin.accept(listener, nil, nil)
        return client >= 0 ? client : nil
    }

    func readLine(_ client: Int32) -> String? {
        var data = Data()
        var byte: UInt8 = 0
        var timeout = timeval(tv_sec: 10, tv_usec: 0)
        setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        while data.count < Self.maxRequestBytes {
            let count = read(client, &byte, 1)
            if count <= 0 { break }
            if byte == UInt8(ascii: "\n") { return String(data: data, encoding: .utf8) }
            data.append(byte)
        }
        return data.isEmpty ? nil : String(data: data, encoding: .utf8)
    }

    func write(_ client: Int32, _ text: String) {
        let bytes = Array((text + "\n").utf8)
        var offset = 0
        while offset < bytes.count {
            let written = bytes[offset...].withUnsafeBufferPointer { Darwin.write(client, $0.baseAddress, $0.count) }
            if written <= 0 { return }
            offset += written
        }
    }
}
