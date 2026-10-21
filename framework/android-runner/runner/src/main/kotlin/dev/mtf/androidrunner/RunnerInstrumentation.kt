package dev.mtf.androidrunner

import android.app.Activity
import android.app.Instrumentation
import android.os.Bundle
import android.util.Log

/**
 * Entry point: `am instrument -w -e port <p> -e token <t> dev.mtf.androidrunner/.RunnerInstrumentation`.
 *
 * Starts a loopback command server and serves typed commands from the host orchestrator until it
 * receives `shutdown`. It never instruments the application under test; it drives it from outside
 * through UIAutomator, addressing elements only by resource ID (Compose test tags exposed with
 * `testTagsAsResourceId`).
 */
class RunnerInstrumentation : Instrumentation() {
    private var arguments: Bundle = Bundle()

    override fun onCreate(arguments: Bundle?) {
        super.onCreate(arguments)
        this.arguments = arguments ?: Bundle()
        start()
    }

    override fun onStart() {
        super.onStart()
        val port = arguments.getString("port")?.toIntOrNull() ?: DEFAULT_PORT
        val token = arguments.getString("token").orEmpty()
        val results = Bundle()
        if (token.length < MIN_TOKEN_LENGTH) {
            results.putString("error", "a token of at least $MIN_TOKEN_LENGTH characters is required")
            finish(Activity.RESULT_CANCELED, results)
            return
        }
        try {
            val commands = UiCommands(this)
            CommandServer(port, token, commands, ::reportStatus).serve()
            results.putString("result", "shutdown")
            finish(Activity.RESULT_OK, results)
        } catch (e: Exception) {
            Log.e(TAG, "runner failed", e)
            results.putString("error", "${e.javaClass.simpleName}: ${e.message}")
            finish(Activity.RESULT_CANCELED, results)
        }
    }

    /** Status lines appear in `am instrument -w` output on the host (INSTRUMENTATION_STATUS). */
    private fun reportStatus(message: String) {
        val status = Bundle()
        status.putString("mtf", message)
        sendStatus(STATUS_CODE, status)
    }

    companion object {
        const val TAG = "MtfRunner"
        const val RUNNER_VERSION = "0.1.2"
        private const val DEFAULT_PORT = 7912
        private const val MIN_TOKEN_LENGTH = 16
        private const val STATUS_CODE = 1
    }
}
