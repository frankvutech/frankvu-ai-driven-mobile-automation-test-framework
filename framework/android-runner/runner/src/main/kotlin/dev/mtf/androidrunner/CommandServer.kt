package dev.mtf.androidrunner

import android.util.Log
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.security.MessageDigest

/**
 * A minimal newline-delimited JSON server: one request per connection.
 *
 * Request:  {"id": "...", "token": "...", "command": "tap", "args": {...}}
 * Response: {"id": "...", "ok": true, "result": {...}} or
 *           {"id": "...", "ok": false, "error": {"category": "...", "message": "..."}}
 *
 * The socket is bound to the device loopback only and every request must carry the per-run token,
 * so nothing else on the device or network can drive it. The command set is closed: there is no
 * command that evaluates code, runs a shell command or accepts a selector other than a resource ID.
 */
class CommandServer(
    private val port: Int,
    private val token: String,
    private val commands: UiCommands,
    private val status: (String) -> Unit,
) {
    @Volatile private var running = true

    fun serve() {
        ServerSocket(port, BACKLOG, InetAddress.getByName("127.0.0.1")).use { server ->
            server.soTimeout = ACCEPT_TIMEOUT_MS
            status("listening on 127.0.0.1:$port")
            Log.i(LOG_TAG, "listening on $port")
            while (running) {
                val socket =
                    try {
                        server.accept()
                    } catch (_: SocketTimeoutException) {
                        continue
                    }
                socket.use { handle(it) }
            }
        }
    }

    private fun handle(socket: Socket) {
        socket.soTimeout = READ_TIMEOUT_MS
        val reader = BufferedReader(InputStreamReader(socket.getInputStream(), Charsets.UTF_8))
        val writer = OutputStreamWriter(socket.getOutputStream(), Charsets.UTF_8)
        val line = reader.readLine() ?: return
        val response = respond(line)
        writer.write(response.toString())
        writer.write("\n")
        writer.flush()
    }

    private fun respond(line: String): JSONObject {
        val request =
            try {
                JSONObject(line)
            } catch (e: Exception) {
                return error(null, "invalid_request", "not JSON: ${e.message}")
            }
        val id = request.optString("id")
        if (!constantTimeEquals(request.optString("token"), token)) {
            return error(id, "unauthorized", "invalid token")
        }
        val command = request.optString("command")
        val args = request.optJSONObject("args") ?: JSONObject()
        if (command == "shutdown") {
            running = false
            return JSONObject().put("id", id).put("ok", true).put("result", JSONObject())
        }
        return try {
            val result = commands.execute(command, args)
            JSONObject().put("id", id).put("ok", true).put("result", result)
        } catch (e: CommandException) {
            error(id, e.category, e.message ?: e.category)
        } catch (e: Exception) {
            Log.e(LOG_TAG, "command $command failed", e)
            error(id, "runner_error", "${e.javaClass.simpleName}: ${e.message}")
        }
    }

    private fun error(
        id: String?,
        category: String,
        message: String,
    ): JSONObject =
        JSONObject()
            .put("id", id ?: JSONObject.NULL)
            .put("ok", false)
            .put("error", JSONObject().put("category", category).put("message", message))

    private fun constantTimeEquals(
        a: String,
        b: String,
    ): Boolean = MessageDigest.isEqual(a.toByteArray(Charsets.UTF_8), b.toByteArray(Charsets.UTF_8))

    companion object {
        private const val LOG_TAG = RunnerInstrumentation.TAG
        private const val BACKLOG = 4
        private const val ACCEPT_TIMEOUT_MS = 1_000
        private const val READ_TIMEOUT_MS = 10_000
    }
}

/** A failure with a category the host maps to its failure taxonomy. */
class CommandException(
    val category: String,
    message: String,
) : Exception(message)
