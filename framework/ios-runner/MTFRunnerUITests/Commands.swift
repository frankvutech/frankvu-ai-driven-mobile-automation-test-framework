import XCTest

struct CommandError: Error {
    let category: String
    let message: String
}

/// The closed set of UI commands, implemented with XCUIApplication / XCUIElement.
///
/// App elements are addressed only by accessibility identifier. The two system-UI exceptions are
/// documented: the navigation bar's back button (system chrome without an app identifier) and the
/// AutoFill "Save Password?" sheet (system UI, dismissed with its "Not Now" button).
final class Commands {
    static let runnerVersion = "0.1.1"

    private let bundleId: String
    private(set) var app: XCUIApplication
    private(set) var shouldStop = false

    init(bundleId: String) {
        self.bundleId = bundleId
        app = XCUIApplication(bundleIdentifier: bundleId)
    }

    private typealias Handler = (Commands, [String: Any]) throws -> [String: Any]

    /// The closed command table. Anything not listed is rejected.
    private static let handlers: [String: Handler] = [
        "hello": { commands, _ in commands.hello() },
        "shutdown": { commands, _ in commands.shouldStop = true; return [:] },
        "launch": { commands, args in try commands.launch(args) },
        "terminate": { commands, _ in commands.app.terminate(); return ["terminated": true] },
        "activate": { commands, _ in commands.app.activate(); return ["activated": true] },
        "background": { commands, args in commands.background(args) },
        "open_url": { commands, args in try commands.openURL(args) },
        "app_state": { commands, _ in ["state": commands.stateName(commands.app.state)] },
        "tap": { commands, args in try commands.tap(args) },
        "double_tap": { commands, args in try commands.gesture(args) { $0.doubleTap() } },
        "long_press": { commands, args in try commands.longPress(args) },
        "type_text": { commands, args in try commands.typeText(args) },
        "clear_text": { commands, args in try commands.clearText(args) },
        "scroll_to": { commands, args in try commands.scrollTo(args) },
        "swipe": { commands, args in try commands.swipe(args) },
        "drag_to": { commands, args in try commands.dragTo(args) },
        "back": { commands, _ in try commands.back() },
        "dismiss_keyboard": { commands, _ in commands.dismissKeyboard() },
        "dismiss_system_dialogs": { commands, _ in commands.dismissSystemDialogs() },
        "wait_for": { commands, args in try commands.waitFor(args) },
        "read": { commands, args in try commands.describe(try commands.identifier(args)) },
        "first_visible": { commands, args in try commands.firstVisible(args) },
        "hierarchy": { commands, _ in ["text": commands.app.debugDescription] },
        "wait_for_idle": { _, _ in ["idle": true] }
    ]

    func execute(_ name: String, _ args: [String: Any]) throws -> [String: Any] {
        guard let handler = Self.handlers[name] else {
            throw CommandError(category: "invalid_request", message: "unknown command '\(name)'")
        }
        return try handler(self, args)
    }

    // MARK: - helpers

    private func identifier(_ args: [String: Any], _ key: String = "id") throws -> String {
        guard let value = args[key] as? String, Self.isTestId(value) else {
            throw CommandError(category: "invalid_request", message: "'\(key)' must be a test ID")
        }
        return value
    }

    /// Lowercase snake_case: `^[a-z0-9]+(_[a-z0-9]+)*$`.
    static func isTestId(_ value: String) -> Bool {
        let parts = value.split(separator: "_", omittingEmptySubsequences: false)
        return !value.isEmpty && parts.allSatisfy { part in
            !part.isEmpty && part.allSatisfy { ("a"..."z").contains($0) || ("0"..."9").contains($0) }
        }
    }

    private func timeout(_ args: [String: Any]) -> TimeInterval {
        let millis = (args["timeout_ms"] as? NSNumber)?.doubleValue ?? 10_000
        return min(max(millis, 0), 120_000) / 1000
    }

    /// Any element type with the identifier. SwiftUI can expose an identifier on a container and its
    /// control; the first match is used, and taps prefer a button with that identifier.
    private func element(_ id: String) -> XCUIElement {
        app.descendants(matching: .any).matching(identifier: id).firstMatch
    }

    private func screenFrame() -> CGRect {
        let window = app.windows.firstMatch
        return window.exists ? window.frame : CGRect(x: 0, y: 0, width: 10_000, height: 10_000)
    }

    private func isVisible(_ element: XCUIElement) -> Bool {
        guard element.exists else { return false }
        let frame = element.frame
        return !frame.isEmpty && frame.intersects(screenFrame())
    }

    private func find(_ id: String, timeout: TimeInterval, hittable: Bool = true) throws -> XCUIElement {
        let target = element(id)
        guard target.waitForExistence(timeout: timeout) else {
            throw CommandError(category: "element_not_found", message: "'\(id)' did not appear within \(Int(timeout * 1000)) ms")
        }
        if hittable {
            let deadline = Date().addingTimeInterval(max(timeout, 1))
            while !target.isHittable && Date() < deadline { usleep(100_000) }
            guard target.isHittable else {
                throw CommandError(category: "element_not_found", message: "'\(id)' exists but is not hittable (off screen or covered)")
            }
        }
        let button = app.buttons.matching(identifier: id).firstMatch
        return button.exists && button.isHittable ? button : target
    }

    private func stateName(_ state: XCUIApplication.State) -> String {
        switch state {
        case .notRunning: return "not_running"
        case .runningBackground, .runningBackgroundSuspended: return "background"
        case .runningForeground: return "foreground"
        default: return "unknown"
        }
    }

    // MARK: - lifecycle

    private func hello() -> [String: Any] {
        let device = UIDevice.current
        return [
            "runner_version": Self.runnerVersion,
            "system_name": device.systemName,
            "system_version": device.systemVersion,
            "model": device.model,
            "simulator_name": ProcessInfo.processInfo.environment["SIMULATOR_DEVICE_NAME"] ?? "",
            "target_bundle_id": bundleId
        ]
    }

    private func launch(_ args: [String: Any]) throws -> [String: Any] {
        app = XCUIApplication(bundleIdentifier: bundleId)
        app.launchArguments = args["arguments"] as? [String] ?? []
        app.launchEnvironment = args["environment"] as? [String: String] ?? [:]
        app.launch()
        guard app.wait(for: .runningForeground, timeout: 30) else {
            throw CommandError(category: "runner_error", message: "the app did not reach the foreground")
        }
        return ["state": stateName(app.state)]
    }

    private func background(_ args: [String: Any]) -> [String: Any] {
        XCUIDevice.shared.press(.home)
        let seconds = ((args["duration_ms"] as? NSNumber)?.doubleValue ?? 2000) / 1000
        Thread.sleep(forTimeInterval: seconds)
        app.activate()
        return ["state": stateName(app.state)]
    }

    private func openURL(_ args: [String: Any]) throws -> [String: Any] {
        guard let text = args["url"] as? String, let url = URL(string: text), url.scheme != nil else {
            throw CommandError(category: "invalid_request", message: "'url' must be an absolute URL")
        }
        app.open(url)
        _ = app.wait(for: .runningForeground, timeout: 10)
        return ["opened": text]
    }

    // MARK: - interactions

    private func tap(_ args: [String: Any]) throws -> [String: Any] {
        let id = try identifier(args)
        let target = try find(id, timeout: timeout(args))
        // SwiftUI Toggle: the identified element is the whole row; a tap on its centre hits the label
        // and does not toggle. Tap the switch control inside the identified element instead.
        let control = target.switches.firstMatch
        if target.elementType == .switch, control.exists, control.isHittable {
            control.tap()
        } else {
            target.tap()
        }
        return ["tapped": id]
    }

    private func gesture(_ args: [String: Any], _ action: (XCUIElement) -> Void) throws -> [String: Any] {
        let id = try identifier(args)
        action(try find(id, timeout: timeout(args)))
        return ["done": id]
    }

    private func longPress(_ args: [String: Any]) throws -> [String: Any] {
        let id = try identifier(args)
        let duration = min(max(((args["duration_ms"] as? NSNumber)?.doubleValue ?? 1000) / 1000, 0.2), 10)
        try find(id, timeout: timeout(args)).press(forDuration: duration)
        return ["long_pressed": id]
    }

    private func typeText(_ args: [String: Any]) throws -> [String: Any] {
        let id = try identifier(args)
        let text = args["text"] as? String ?? ""
        let field = try find(id, timeout: timeout(args))
        field.tap()
        if args["clear_first"] as? Bool ?? true { clear(field) }
        field.typeText(text)
        return ["typed_into": id, "length": text.count]  // never echo the value: it may be a secret
    }

    private func clear(_ field: XCUIElement) {
        guard let value = field.value as? String, !value.isEmpty, value != field.placeholderValue else { return }
        field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: value.count))
    }

    private func clearText(_ args: [String: Any]) throws -> [String: Any] {
        let id = try identifier(args)
        let field = try find(id, timeout: timeout(args))
        field.tap()
        clear(field)
        return ["cleared": id]
    }

    private func scrollTo(_ args: [String: Any]) throws -> [String: Any] {
        let id = try identifier(args)
        let maxSwipes = min(max((args["max_swipes"] as? NSNumber)?.intValue ?? 10, 1), 30)
        let direction = args["direction"] as? String ?? "down"
        var scroller: XCUIElement = app
        if let containerId = args["container"] as? String, !containerId.isEmpty {
            let container = element(containerId)
            if container.exists && container.isHittable { scroller = container }
        }
        let target = element(id)
        var swipes = 0
        // Down first (or the requested direction), then back the other way.
        for pass in [direction, opposite(direction)] {
            for _ in 0..<maxSwipes {
                if target.exists && target.isHittable { return ["scrolled": swipes] }
                // Slow, short swipes: a fast one moves a whole page and lazy rows that pass are removed.
                swipeContent(scroller, pass)
                swipes += 1
            }
        }
        if target.exists && target.isHittable { return ["scrolled": swipes] }
        throw CommandError(category: "element_not_found", message: "'\(id)' not reachable after \(swipes) swipes")
    }

    private func opposite(_ direction: String) -> String {
        ["down": "up", "up": "down", "left": "right", "right": "left"][direction] ?? "up"
    }

    /// Moves content so that items further in `direction` come into view.
    private func swipeContent(_ element: XCUIElement, _ direction: String) {
        switch direction {
        case "down": element.swipeUp(velocity: .slow)
        case "up": element.swipeDown(velocity: .slow)
        case "right": element.swipeLeft(velocity: .slow)
        default: element.swipeRight(velocity: .slow)
        }
    }

    private func swipe(_ args: [String: Any]) throws -> [String: Any] {
        let direction = args["direction"] as? String ?? ""
        let target: XCUIElement = args["id"] is String ? try find(try identifier(args), timeout: timeout(args)) : app
        switch direction {
        case "up": target.swipeUp()
        case "down": target.swipeDown()
        case "left": target.swipeLeft()
        case "right": target.swipeRight()
        default: throw CommandError(category: "invalid_request", message: "direction must be up, down, left or right")
        }
        return ["swiped": direction]
    }

    private func dragTo(_ args: [String: Any]) throws -> [String: Any] {
        let source = try find(try identifier(args), timeout: timeout(args))
        let destination = try find(try identifier(args, "to"), timeout: timeout(args), hittable: false)
        source.press(forDuration: 0.6, thenDragTo: destination)
        return ["dragged": true]
    }

    private func back() throws -> [String: Any] {
        // System navigation bar back button: system chrome without an app identifier (documented).
        let button = app.navigationBars.buttons.element(boundBy: 0)
        guard button.waitForExistence(timeout: 3), button.isHittable else {
            throw CommandError(category: "element_not_found", message: "no navigation bar back button on this screen")
        }
        button.tap()
        return ["pressed": true]
    }

    private func dismissKeyboard() -> [String: Any] {
        guard app.keyboards.count > 0 else { return ["dismissed": false] }
        let bar = app.navigationBars.firstMatch
        if bar.exists { bar.tap() }
        return ["dismissed": app.keyboards.count == 0]
    }

    /// Declines the AutoFill "Save Password?" sheet, which covers the app after a login (system UI).
    private func dismissSystemDialogs() -> [String: Any] {
        var dismissed: [String] = []
        let notNow = app.buttons["Not Now"].firstMatch
        if notNow.waitForExistence(timeout: 2) {
            notNow.tap()
            dismissed.append("save_password_not_now")
        }
        return ["dismissed": dismissed]
    }

    // MARK: - queries

    /// (exists, visible, enabled, selected) -> state reached
    private static let waitStates: [String: (Bool, Bool, Bool, Bool) -> Bool] = [
        "visible": { _, visible, _, _ in visible },
        "gone": { _, visible, _, _ in !visible },
        "enabled": { exists, _, enabled, _ in exists && enabled },
        "disabled": { exists, _, enabled, _ in exists && !enabled },
        "selected": { exists, _, _, selected in exists && selected },
        "not_selected": { exists, _, _, selected in exists && !selected }
    ]

    private func waitFor(_ args: [String: Any]) throws -> [String: Any] {
        let id = try identifier(args)
        let state = args["state"] as? String ?? "visible"
        let limit = timeout(args)
        let deadline = Date().addingTimeInterval(limit)
        let target = element(id)
        if state == "visible" { _ = target.waitForExistence(timeout: limit) }
        while true {
            let info = try describe(id)
            let exists = info["exists"] as? Bool ?? false
            guard let predicate = Self.waitStates[state] else {
                throw CommandError(category: "invalid_request", message: "unknown state '\(state)'")
            }
            let satisfied = predicate(
                exists, info["visible"] as? Bool ?? false, info["enabled"] as? Bool ?? false, info["selected"] as? Bool ?? false
            )
            if satisfied { return info }
            if Date() >= deadline {
                let category = state == "visible" && !exists ? "element_not_found" : "timeout"
                throw CommandError(category: category, message: "'\(id)' did not become \(state) within \(Int(limit * 1000)) ms")
            }
            usleep(150_000)
        }
    }

    private func describe(_ id: String) throws -> [String: Any] {
        let target = element(id)
        guard target.exists else { return ["id": id, "exists": false, "visible": false] }
        let frame = target.frame
        let isField = [.textField, .secureTextField, .searchField, .textView].contains(target.elementType)
        let value = target.value.map { "\($0)" } ?? ""
        let label = target.label
        let text = isField ? (value == target.placeholderValue ? "" : value) : (label.isEmpty ? value : label)
        let selected = target.isSelected || ([.switch, .toggle, .checkBox].contains(target.elementType) && value == "1")
        return [
            "id": id,
            "exists": true,
            "visible": isVisible(target),
            "text": text,
            "value": value,
            "label": label,
            "enabled": target.isEnabled,
            "selected": selected,
            "element_type": target.elementType.rawValue,
            "bounds": [Int(frame.minX), Int(frame.minY), Int(frame.maxX), Int(frame.maxY)]
        ]
    }

    /// One snapshot of the app, then the first requested identifier that is on screen.
    private func firstVisible(_ args: [String: Any]) throws -> [String: Any] {
        guard let ids = args["ids"] as? [String] else { throw CommandError(category: "invalid_request", message: "'ids' is required") }
        let snapshot = try app.snapshot()
        let screen = screenFrame()
        var visible = Set<String>()
        var queue: [XCUIElementSnapshot] = [snapshot]
        while let node = queue.popLast() {
            if !node.identifier.isEmpty, !node.frame.isEmpty, node.frame.intersects(screen) { visible.insert(node.identifier) }
            queue.append(contentsOf: node.children)
        }
        return ["id": ids.first(where: visible.contains) ?? NSNull()]
    }
}
