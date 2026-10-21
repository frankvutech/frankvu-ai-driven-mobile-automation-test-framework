import XCTest

/// The single "test" the host starts with `xcodebuild test-without-building -only-testing:` it serves
/// typed commands from the host orchestrator until it receives `shutdown`.
///
/// Configuration comes from environment variables the host writes into the .xctestrun:
/// `MTF_RUNNER_PORT`, `MTF_RUNNER_TOKEN` (per run, ≥ 16 characters) and `MTF_TARGET_BUNDLE_ID`.
final class RunnerTests: XCTestCase {
    private var issues: [String] = []

    /// XCUITest reports problems (for example tapping an element that vanished) as test issues.
    /// The runner converts them into command errors for the host instead of failing the serve loop.
    override func record(_ issue: XCTIssue) {
        issues.append(issue.compactDescription)
    }

    func testServe() throws {
        let environment = ProcessInfo.processInfo.environment
        guard let portText = environment["MTF_RUNNER_PORT"], let port = UInt16(portText),
              let token = environment["MTF_RUNNER_TOKEN"], token.count >= 16,
              let bundleId = environment["MTF_TARGET_BUNDLE_ID"], !bundleId.isEmpty
        else {
            issues.append("MTF_RUNNER_PORT, MTF_RUNNER_TOKEN and MTF_TARGET_BUNDLE_ID must be set")
            super.record(XCTIssue(type: .system, compactDescription: issues.joined()))
            return
        }
        continueAfterFailure = true
        let server = LineServer(port: port)
        try server.start()
        let commands = Commands(bundleId: bundleId)
        NSLog("MTF_RUNNER_READY port=%d", Int(port))
        while !commands.shouldStop {
            guard let client = server.accept(timeoutMs: 1000) else { continue }
            defer { close(client) }
            guard let line = server.readLine(client) else { continue }
            server.write(client, respond(to: line, token: token, commands: commands))
        }
    }

    private func respond(to line: String, token: String, commands: Commands) -> String {
        guard let data = line.data(using: .utf8),
              let request = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else { return encode(["id": NSNull(), "ok": false, "error": ["category": "invalid_request", "message": "not JSON"]]) }
        let id = request["id"] ?? NSNull()
        guard let given = request["token"] as? String, constantTimeEquals(given, token) else {
            return encode(["id": id, "ok": false, "error": ["category": "unauthorized", "message": "invalid token"]])
        }
        let name = request["command"] as? String ?? ""
        let args = request["args"] as? [String: Any] ?? [:]
        issues.removeAll()
        do {
            let result = try commands.execute(name, args)
            if !issues.isEmpty {
                return encode(["id": id, "ok": false, "error": ["category": "runner_error", "message": issues.joined(separator: "; ")]])
            }
            return encode(["id": id, "ok": true, "result": result])
        } catch let error as CommandError {
            return encode(["id": id, "ok": false, "error": ["category": error.category, "message": error.message]])
        } catch {
            return encode(["id": id, "ok": false, "error": ["category": "runner_error", "message": "\(error)"]])
        }
    }

    private func encode(_ object: [String: Any]) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: object), let text = String(data: data, encoding: .utf8) else {
            return #"{"ok":false,"error":{"category":"runner_error","message":"cannot encode response"}}"#
        }
        return text
    }

    private func constantTimeEquals(_ lhs: String, _ rhs: String) -> Bool {
        let left = Array(lhs.utf8), right = Array(rhs.utf8)
        guard left.count == right.count else { return false }
        return zip(left, right).reduce(UInt8(0)) { $0 | ($1.0 ^ $1.1) } == 0
    }
}
