package dev.mtf.androidrunner

import android.app.Instrumentation
import android.graphics.Point
import android.graphics.Rect
import android.os.Build
import android.os.SystemClock
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import androidx.test.uiautomator.By
import androidx.test.uiautomator.BySelector
import androidx.test.uiautomator.Direction
import androidx.test.uiautomator.StaleObjectException
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.UiObject2
import androidx.test.uiautomator.Until
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream

/**
 * The closed set of UI commands. Elements are addressed only by resource ID, which for a Compose
 * app with `testTagsAsResourceId = true` is the test tag. There is deliberately no lookup by visible
 * text, content description or coordinates for app elements.
 */
class UiCommands(
    private val instrumentation: Instrumentation,
) {
    private val device: UiDevice = UiDevice.getInstance(instrumentation)

    fun execute(
        command: String,
        args: JSONObject,
    ): JSONObject =
        when (command) {
            "hello" -> hello()
            "tap" -> tap(args)
            "double_tap" -> doubleTap(args)
            "long_press" -> longPress(args)
            "type_text" -> typeText(args)
            "clear_text" -> clearText(args)
            "scroll_to" -> scrollTo(args)
            "swipe" -> swipe(args)
            "drag_to" -> dragTo(args)
            "back" -> back()
            "dismiss_keyboard" -> dismissKeyboard()
            "dismiss_system_dialogs" -> dismissSystemDialogs()
            "wait_for" -> waitFor(args)
            "read" -> read(args)
            "first_visible" -> firstVisible(args)
            "hierarchy" -> hierarchy()
            "wait_for_idle" -> waitForIdle(args)
            else -> throw CommandException("invalid_request", "unknown command '$command'")
        }

    // --- helpers -----------------------------------------------------------------------------------
    private fun id(
        args: JSONObject,
        key: String = "id",
    ): String {
        val value = args.optString(key)
        if (!ID_PATTERN.matches(value)) throw CommandException("invalid_request", "'$key' must be a test ID, got '$value'")
        return value
    }

    private fun timeout(args: JSONObject): Long = args.optLong("timeout_ms", DEFAULT_TIMEOUT_MS).coerceIn(0, MAX_TIMEOUT_MS)

    private fun selector(testId: String): BySelector = By.res(testId)

    /** Finds the element once the UI has settled (see the app team's notes on UIAutomator timing). */
    private fun find(
        testId: String,
        timeoutMs: Long,
    ): UiObject2 {
        val first =
            device.wait(Until.findObject(selector(testId)), timeoutMs)
                ?: if (dismissAnrDialogs().isNotEmpty()) device.wait(Until.findObject(selector(testId)), timeoutMs) else null
        if (first == null) throw CommandException("element_not_found", "'$testId' did not appear within $timeoutMs ms")
        device.waitForIdle(IDLE_TIMEOUT_MS)
        return device.wait(Until.findObject(selector(testId)), timeoutMs)
            ?: throw CommandException("element_not_found", "'$testId' disappeared while the UI settled")
    }

    /** Runs an action on a freshly found element, retrying when the node is replaced mid-action. */
    private fun <T> withElement(
        testId: String,
        timeoutMs: Long,
        action: (UiObject2) -> T,
    ): T {
        var last: StaleObjectException? = null
        repeat(STALE_RETRIES) {
            try {
                return action(find(testId, timeoutMs))
            } catch (e: StaleObjectException) {
                last = e
                device.waitForIdle(IDLE_TIMEOUT_MS)
            }
        }
        throw CommandException("element_not_found", "'$testId' kept changing while being used: ${last?.message}")
    }

    private fun ok(vararg pairs: Pair<String, Any?>): JSONObject {
        val result = JSONObject()
        for ((k, v) in pairs) result.put(k, v ?: JSONObject.NULL)
        return result
    }

    // --- commands --------------------------------------------------------------------------------------
    private fun hello(): JSONObject =
        ok(
            "runner_version" to RunnerInstrumentation.RUNNER_VERSION,
            "sdk_int" to Build.VERSION.SDK_INT,
            "release" to Build.VERSION.RELEASE,
            "manufacturer" to Build.MANUFACTURER,
            "model" to Build.MODEL,
            "display_width" to device.displayWidth,
            "display_height" to device.displayHeight,
            "uiautomator" to "androidx.test.uiautomator 2.3.0",
        )

    private fun tap(args: JSONObject): JSONObject {
        val testId = id(args)
        withElement(testId, timeout(args)) { it.click() }
        return ok("tapped" to testId)
    }

    private fun doubleTap(args: JSONObject): JSONObject {
        val testId = id(args)
        withElement(testId, timeout(args)) {
            val center = it.visibleCenter
            device.click(center.x, center.y)
            SystemClock.sleep(DOUBLE_TAP_GAP_MS)
            device.click(center.x, center.y)
        }
        return ok("double_tapped" to testId)
    }

    private fun longPress(args: JSONObject): JSONObject {
        val testId = id(args)
        val duration = args.optLong("duration_ms", DEFAULT_LONG_PRESS_MS).coerceIn(MIN_LONG_PRESS_MS, MAX_LONG_PRESS_MS)
        withElement(testId, timeout(args)) { it.click(duration) }
        return ok("long_pressed" to testId)
    }

    private fun typeText(args: JSONObject): JSONObject {
        val testId = id(args)
        val text = args.optString("text")
        val clearFirst = args.optBoolean("clear_first", true)
        withElement(testId, timeout(args)) { field ->
            field.click()
            device.waitForIdle(IDLE_TIMEOUT_MS)
            val current = if (clearFirst) "" else field.text.orEmpty()
            field.text = current + text
        }
        // The value is never echoed back: it may be a secret.
        return ok("typed_into" to testId, "length" to text.length)
    }

    private fun clearText(args: JSONObject): JSONObject {
        val testId = id(args)
        withElement(testId, timeout(args)) { it.clear() }
        return ok("cleared" to testId)
    }

    private fun scrollTo(args: JSONObject): JSONObject {
        val testId = id(args)
        val container = if (args.has("container") && !args.isNull("container")) id(args, "container") else null
        val forward =
            when (args.optString("direction", "down")) {
                "down", "right" -> true
                "up", "left" -> false
                else -> throw CommandException("invalid_request", "direction must be up, down, left or right")
            }
        val maxSwipes = args.optInt("max_swipes", DEFAULT_MAX_SCROLLS).coerceIn(1, MAX_SCROLLS)
        device.waitForIdle(IDLE_TIMEOUT_MS)
        if (isOnScreen(testId)) return ok("scrolled" to 0)
        var steps = 0
        for (direction in listOf(forward, !forward)) {
            for (attempt in 0 until maxSwipes) {
                val before = settledSignature(container)
                scrollOnce(container, direction)
                steps++
                // Compose animates accessibility scrolls: wait until the content stops moving before
                // looking for the target or comparing positions. An action can also be dropped (for
                // example right after a screen opened), so "nothing moved" is confirmed with a swipe
                // gesture inside the container before it is taken as the end of the content.
                var after = settledSignature(container)
                if (after == before) {
                    swipeWithin(container, direction)
                    after = settledSignature(container)
                }
                if (isOnScreen(testId)) return ok("scrolled" to steps)
                if (after == before) break
            }
        }
        throw CommandException("element_not_found", "'$testId' not found after $steps scroll steps")
    }

    private fun isOnScreen(testId: String): Boolean {
        val obj = device.findObject(selector(testId)) ?: return false
        return try {
            val bounds = obj.visibleBounds
            bounds.width() > 0 && bounds.height() > 0
        } catch (_: StaleObjectException) {
            false
        }
    }

    /**
     * One scroll step through the accessibility layer (ACTION_SCROLL_FORWARD/BACKWARD on the
     * container, or the first scrollable node), as TalkBack does. Falls back to a swipe only when no
     * scrollable node is exposed. Returns false when the container could not move.
     */
    private fun scrollOnce(
        container: String?,
        forward: Boolean,
    ): Boolean {
        val node = scrollableNode(container)
        if (node != null) {
            val action = if (forward) AccessibilityNodeInfo.ACTION_SCROLL_FORWARD else AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
            if (node.performAction(action)) return true
        }
        val x = device.displayWidth / 2
        val (from, to) =
            if (forward) {
                device.displayHeight * 3 / 4 to device.displayHeight / 4
            } else {
                device.displayHeight / 4 to
                    device.displayHeight * 3 / 4
            }
        return device.swipe(x, from, x, to, SWIPE_STEPS)
    }

    /** A swipe gesture inside the scrollable's bounds (or the screen) that moves content [forward]. */
    private fun swipeWithin(
        container: String?,
        forward: Boolean,
    ) {
        val area = Rect(0, 0, device.displayWidth, device.displayHeight)
        scrollableNode(container)?.getBoundsInScreen(area)
        val x = area.centerX()
        val high = area.top + area.height() / 4
        val low = area.bottom - area.height() / 4
        val (from, to) = if (forward) low to high else high to low
        device.swipe(x, from, x, to, SWIPE_STEPS)
    }

    /** Waits until two snapshots taken [SETTLE_POLL_MS] apart are identical, then returns the snapshot. */
    private fun settledSignature(container: String?): String {
        device.waitForIdle(IDLE_TIMEOUT_MS)
        var previous = contentSignature(container)
        repeat(SETTLE_ROUNDS) {
            SystemClock.sleep(SETTLE_POLL_MS)
            val current = contentSignature(container)
            if (current == previous) return current
            previous = current
        }
        return previous
    }

    /** IDs and positions of what the scrollable shows: equal before and after a step = it did not move. */
    private fun contentSignature(container: String?): String {
        val root = scrollableNode(container) ?: instrumentation.uiAutomation.rootInActiveWindow ?: return ""
        val parts = mutableListOf<String>()
        val bounds = Rect()
        val queue = ArrayDeque(listOf(root))
        while (queue.isNotEmpty() && parts.size < MAX_SIGNATURE_NODES) {
            val node = queue.removeFirst()
            node.getBoundsInScreen(bounds)
            parts.add("${node.viewIdResourceName}@${bounds.top},${bounds.left}")
            for (i in 0 until node.childCount) node.getChild(i)?.let(queue::addLast)
        }
        return parts.joinToString("|")
    }

    private fun scrollableNode(container: String?): AccessibilityNodeInfo? {
        val roots =
            buildList {
                instrumentation.uiAutomation.rootInActiveWindow?.let(::add)
                instrumentation.uiAutomation.windows
                    .mapNotNull { it.root }
                    .forEach(::add)
            }
        for (root in roots) {
            val queue = ArrayDeque(listOf(root))
            var firstScrollable: AccessibilityNodeInfo? = null
            while (queue.isNotEmpty()) {
                val node = queue.removeFirst()
                if (container != null && node.viewIdResourceName == container) {
                    return if (node.isScrollable) node else firstScrollableIn(node)
                }
                if (container == null && node.isScrollable && firstScrollable == null) firstScrollable = node
                for (i in 0 until node.childCount) node.getChild(i)?.let(queue::addLast)
            }
            if (container == null && firstScrollable != null) return firstScrollable
        }
        return null
    }

    private fun firstScrollableIn(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val queue = ArrayDeque(listOf(root))
        while (queue.isNotEmpty()) {
            val node = queue.removeFirst()
            if (node.isScrollable) return node
            for (i in 0 until node.childCount) node.getChild(i)?.let(queue::addLast)
        }
        return null
    }

    private fun swipe(args: JSONObject): JSONObject {
        val direction =
            when (args.optString("direction")) {
                "up" -> Direction.UP
                "down" -> Direction.DOWN
                "left" -> Direction.LEFT
                "right" -> Direction.RIGHT
                else -> throw CommandException("invalid_request", "direction must be up, down, left or right")
            }
        if (args.has("id") && !args.isNull("id")) {
            val testId = id(args)
            withElement(testId, timeout(args)) { it.swipe(direction, SWIPE_PERCENT) }
        } else {
            val w = device.displayWidth
            val h = device.displayHeight
            val (sx, sy, ex, ey) =
                when (direction) {
                    Direction.UP -> listOf(w / 2, h * 3 / 4, w / 2, h / 4)
                    Direction.DOWN -> listOf(w / 2, h / 4, w / 2, h * 3 / 4)
                    Direction.LEFT -> listOf(w * 3 / 4, h / 2, w / 4, h / 2)
                    Direction.RIGHT -> listOf(w / 4, h / 2, w * 3 / 4, h / 2)
                }
            device.swipe(sx, sy, ex, ey, SWIPE_STEPS)
        }
        return ok("swiped" to args.optString("direction"))
    }

    private fun dragTo(args: JSONObject): JSONObject {
        val source = id(args)
        val target = id(args, "to")
        val destination: Point = withElement(target, timeout(args)) { it.visibleCenter }
        withElement(source, timeout(args)) { it.drag(destination, DRAG_SPEED) }
        return ok("dragged" to source, "onto" to target)
    }

    private fun back(): JSONObject = ok("pressed" to device.pressBack())

    private fun keyboardShown(): Boolean = instrumentation.uiAutomation.windows.any { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD }

    private fun dismissKeyboard(): JSONObject {
        if (!keyboardShown()) return ok("dismissed" to false)
        device.pressBack()
        device.waitForIdle(IDLE_TIMEOUT_MS)
        return ok("dismissed" to true)
    }

    /** Taps "Wait" on "<app> isn't responding" dialogs (system resource ID, not visible text). */
    private fun dismissAnrDialogs(): List<String> {
        val dismissed = mutableListOf<String>()
        repeat(ANR_ROUNDS) {
            val wait = device.findObject(By.res("android", "aerr_wait")) ?: return dismissed
            wait.click()
            device.wait(Until.gone(By.res("android", "aerr_wait")), DEFAULT_TIMEOUT_MS)
            dismissed.add("anr_wait")
        }
        return dismissed
    }

    private fun dismissSystemDialogs(): JSONObject = ok("dismissed" to JSONArray(dismissAnrDialogs()))

    private fun waitFor(args: JSONObject): JSONObject {
        val testId = id(args)
        val state = args.optString("state", "visible")
        val timeoutMs = timeout(args)
        val deadline = SystemClock.uptimeMillis() + timeoutMs
        while (true) {
            val info = describe(testId)
            val satisfied =
                when (state) {
                    "visible" -> info.getBoolean("visible")
                    "gone" -> !info.getBoolean("visible")
                    "enabled" -> info.getBoolean("exists") && info.getBoolean("enabled")
                    "disabled" -> info.getBoolean("exists") && !info.getBoolean("enabled")
                    "selected" -> info.getBoolean("exists") && info.getBoolean("selected")
                    "not_selected" -> info.getBoolean("exists") && !info.getBoolean("selected")
                    else -> throw CommandException("invalid_request", "unknown state '$state'")
                }
            if (satisfied) return info
            if (SystemClock.uptimeMillis() >= deadline) {
                val category = if (state == "visible" && !info.getBoolean("exists")) "element_not_found" else "timeout"
                throw CommandException(category, "'$testId' did not become $state within $timeoutMs ms")
            }
            if (state == "visible") dismissAnrDialogs()
            SystemClock.sleep(POLL_MS)
        }
    }

    private fun read(args: JSONObject): JSONObject = describe(id(args))

    private fun firstVisible(args: JSONObject): JSONObject {
        val ids = args.optJSONArray("ids") ?: throw CommandException("invalid_request", "'ids' is required")
        for (i in 0 until ids.length()) {
            val testId = ids.getString(i)
            if (!ID_PATTERN.matches(testId)) throw CommandException("invalid_request", "invalid test ID '$testId'")
            if (isOnScreen(testId)) return ok("id" to testId)
        }
        return ok("id" to null)
    }

    private fun describe(testId: String): JSONObject {
        repeat(STALE_RETRIES) {
            val obj = device.findObject(selector(testId)) ?: return ok("id" to testId, "exists" to false, "visible" to false)
            try {
                val bounds: Rect = obj.visibleBounds
                val text = obj.text
                val ownText = if (!text.isNullOrEmpty()) text else obj.contentDescription
                val childText = if (ownText.isNullOrEmpty()) descendantText(obj) else ""
                return ok(
                    "id" to testId,
                    "exists" to true,
                    "visible" to (bounds.width() > 0 && bounds.height() > 0),
                    "text" to (ownText ?: childText),
                    "value" to (text ?: ""),
                    "enabled" to obj.isEnabled,
                    "selected" to (obj.isChecked || obj.isSelected),
                    "checkable" to obj.isCheckable,
                    "class_name" to obj.className,
                    "bounds" to JSONArray(listOf(bounds.left, bounds.top, bounds.right, bounds.bottom)),
                )
            } catch (_: StaleObjectException) {
                device.waitForIdle(IDLE_TIMEOUT_MS)
            }
        }
        throw CommandException("element_not_found", "'$testId' kept changing while being read")
    }

    private fun descendantText(obj: UiObject2): String {
        val parts = mutableListOf<String>()

        fun walk(
            node: UiObject2,
            depth: Int,
        ) {
            if (depth > MAX_TEXT_DEPTH) return
            for (child in node.children) {
                val t = child.text ?: child.contentDescription
                if (!t.isNullOrEmpty()) parts.add(t)
                walk(child, depth + 1)
            }
        }
        walk(obj, 0)
        return parts.joinToString(" ")
    }

    private fun hierarchy(): JSONObject {
        val out = ByteArrayOutputStream()
        device.dumpWindowHierarchy(out)
        return ok("xml" to out.toString(Charsets.UTF_8.name()))
    }

    private fun waitForIdle(args: JSONObject): JSONObject {
        device.waitForIdle(args.optLong("timeout_ms", IDLE_TIMEOUT_MS))
        return ok("idle" to true)
    }

    companion object {
        private val ID_PATTERN = Regex("^[a-z0-9]+(_[a-z0-9]+)*$")
        private const val DEFAULT_TIMEOUT_MS = 10_000L
        private const val MAX_TIMEOUT_MS = 120_000L
        private const val IDLE_TIMEOUT_MS = 1_500L
        private const val POLL_MS = 150L
        private const val STALE_RETRIES = 3
        private const val DOUBLE_TAP_GAP_MS = 80L
        private const val DEFAULT_LONG_PRESS_MS = 1_000L
        private const val MIN_LONG_PRESS_MS = 200L
        private const val MAX_LONG_PRESS_MS = 10_000L
        private const val DEFAULT_MAX_SCROLLS = 10
        private const val MAX_SCROLLS = 30
        private const val SWIPE_STEPS = 30
        private const val SWIPE_PERCENT = 0.7f
        private const val DRAG_SPEED = 1_500
        private const val ANR_ROUNDS = 3
        private const val MAX_TEXT_DEPTH = 4
        private const val MAX_SIGNATURE_NODES = 400
        private const val SETTLE_POLL_MS = 200L
        private const val SETTLE_ROUNDS = 15
    }
}
