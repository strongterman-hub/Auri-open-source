package com.auri.chat

import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

data class StreamResult(
    val sessionId: String?,
    val resetNotice: String?,
)

data class FileUpload(
    val name: String,
    val mime: String,
    val data: String,
    val size: Long,
)

class AuriApi(
    private val baseUrl: String = ApiConfig.BASE_URL,
) {
    fun createSession(userId: String, token: String): String {
        val body = JSONObject().put("user_id", userId).toString()
        val response = request("POST", "/sessions", body, token)
        return response.getString("id")
    }

    fun ensureSession(userId: String, token: String): String {
        val body = JSONObject().put("user_id", userId).toString()
        val response = request("POST", "/sessions/ensure", body, token)
        return response.getString("id")
    }

    fun getSession(sessionId: String, token: String): JSONObject =
        request("GET", "/sessions/$sessionId", null, token)

    fun getMessages(
        sessionId: String,
        token: String,
        before: String?,
        limit: Int,
    ): JSONObject {
        val query = buildString {
            append("/sessions/").append(sessionId).append("/messages?limit=").append(limit)
            if (before != null) {
                append("&before=").append(URLEncoder.encode(before, Charsets.UTF_8.name()))
            }
        }
        return request("GET", query, null, token)
    }

    fun deleteSession(sessionId: String, token: String) {
        val connection = URL(baseUrl + "/sessions/$sessionId").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "DELETE"
            connection.connectTimeout = 30_000
            connection.readTimeout = 30_000
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.responseCode
        } finally {
            runCatching { connection.disconnect() }
        }
    }

    fun sendMessage(
        sessionId: String,
        content: String,
        token: String,
        images: List<String> = emptyList(),
        files: List<FileUpload> = emptyList(),
    ): String {
        val body = buildMessageBody(content, images, files).toString()
        val response = request("POST", "/sessions/$sessionId/messages", body, token)
        return response.getJSONObject("message").getString("content")
    }

    fun sendAsyncMessage(
        sessionId: String,
        clientMessageId: String,
        content: String,
        token: String,
        images: List<String> = emptyList(),
        files: List<FileUpload> = emptyList(),
    ): JSONObject {
        val body = buildMessageBody(content, images, files)
            .put("client_message_id", clientMessageId)
            .toString()
        return request("POST", "/sessions/$sessionId/messages/async", body, token)
    }

    fun getChatUpdates(
        sessionId: String,
        token: String,
        after: String?,
    ): JSONObject {
        val path = buildString {
            append("/sessions/").append(sessionId).append("/updates")
            if (after != null) {
                append("?after=").append(URLEncoder.encode(after, Charsets.UTF_8.name()))
            }
        }
        return request("GET", path, null, token)
    }

    fun heartbeat(token: String, timezone: String): JSONObject {
        val body = JSONObject().put("timezone", timezone).toString()
        return request("POST", "/presence/heartbeat", body, token)
    }

    fun reportLocation(
        latitude: Double,
        longitude: Double,
        token: String,
        timezone: String,
        requestId: String? = null,
    ): JSONObject {
        val body = JSONObject()
            .put("latitude", latitude)
            .put("longitude", longitude)
            .put("timezone", timezone)
        if (requestId != null) {
            body.put("request_id", requestId)
        }
        return request("POST", "/presence/location", body.toString(), token)
    }

    fun getProactiveInbox(token: String): JSONObject =
        request("GET", "/proactive/inbox", null, token)

    fun acknowledgeProactive(ids: List<String>, token: String): JSONObject {
        val body = JSONObject().put("ids", org.json.JSONArray(ids)).toString()
        return request("POST", "/proactive/inbox/ack", body, token)
    }

    fun reportGuideActionCompleted(type: String, token: String): JSONObject {
        val body = JSONObject().put("type", type).toString()
        return request("POST", "/proactive/guide-action", body, token)
    }

    fun getProactiveSettings(token: String): JSONObject =
        request("GET", "/proactive/settings", null, token)

    fun updateProactiveSettings(enabled: Boolean, token: String): JSONObject {
        val body = JSONObject().put("enabled", enabled).toString()
        return request("PUT", "/proactive/settings", body, token)
    }

    fun registerDevice(registrationId: String, token: String): JSONObject {
        val body = JSONObject().put("token", registrationId).toString()
        return request("POST", "/devices", body, token)
    }

    fun unregisterDevice(registrationId: String, token: String) {
        val encoded = URLEncoder.encode(registrationId, Charsets.UTF_8.name())
        val connection =
            URL(baseUrl + "/devices/$encoded").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "DELETE"
            connection.connectTimeout = 30_000
            connection.readTimeout = 30_000
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.responseCode
        } finally {
            runCatching { connection.disconnect() }
        }
    }

    fun streamMessage(
        sessionId: String,
        content: String,
        images: List<String>,
        files: List<FileUpload>,
        token: String,
        shouldStop: () -> Boolean = { false },
        onOpened: (HttpURLConnection, InputStream) -> Unit = { _, _ -> },
        onLocationRequest: (String) -> Unit = {},
        onChunk: (String) -> Unit,
    ): StreamResult {
        val body = buildMessageBody(content, images, files).toString()
        val connection = URL(baseUrl + "/sessions/$sessionId/messages/stream")
            .openConnection() as HttpURLConnection
        var resultSessionId: String? = null
        var resultNotice: String? = null
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 60_000
            connection.readTimeout = 180_000
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("Accept", "text/event-stream")
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }

            val code = connection.responseCode
            if (code !in 200..299) {
                val errorText = connection.errorStream
                    ?.bufferedReader()
                    ?.use { it.readText() }
                    .orEmpty()
                    .take(300)
                throw IllegalStateException(parseApiError(errorText, code))
            }

            val inputStream = connection.inputStream
            onOpened(connection, inputStream)
            val reader = inputStream.bufferedReader(Charsets.UTF_8)
            var line: String? = null
            while (!shouldStop()) {
                line = reader.readLine() ?: break
                val trimmed = line.trim()
                if (trimmed.startsWith("data:")) {
                    val payload = trimmed.removePrefix("data:").trim()
                    if (payload == "[DONE]") break
                    runCatching {
                        val obj = JSONObject(payload)
                        val chunk = obj.optString("content")
                        if (chunk.isNotEmpty()) onChunk(chunk)
                        if (!obj.isNull("session_id")) {
                            obj.optString("session_id").takeIf { it.isNotBlank() }
                                ?.let { resultSessionId = it }
                        }
                        if (!obj.isNull("reset_notice")) {
                            obj.optString("reset_notice").takeIf { it.isNotBlank() }
                                ?.let { resultNotice = it }
                        }
                        if (obj.optBoolean("request_location", false)) {
                            obj.optString("request_id").takeIf { it.isNotBlank() }
                                ?.let { onLocationRequest(it) }
                        }
                    }
                }
            }
        } finally {
            connection.disconnect()
        }
        return StreamResult(resultSessionId, resultNotice)
    }

    private fun buildMessageBody(
        content: String,
        images: List<String>,
        files: List<FileUpload>,
    ): JSONObject {
        val body = JSONObject().put("content", content)
        if (images.isNotEmpty()) {
            body.put("images", org.json.JSONArray(images))
        }
        if (files.isNotEmpty()) {
            val fileArray = org.json.JSONArray()
            files.forEach { file ->
                fileArray.put(
                    JSONObject()
                        .put("name", file.name)
                        .put("mime", file.mime)
                        .put("data", file.data),
                )
            }
            body.put("files", fileArray)
        }
        return body
    }

    private fun request(
        method: String,
        path: String,
        body: String?,
        token: String? = null,
    ): JSONObject {
        val connection = URL(baseUrl + path).openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = method
            connection.connectTimeout = 60_000
            connection.readTimeout = 120_000
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("Accept", "application/json")
            if (token != null) {
                connection.setRequestProperty("Authorization", "Bearer $token")
            }

            if (body != null) {
                connection.doOutput = true
                connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }

            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader()?.use(BufferedReader::readText).orEmpty()
            if (code !in 200..299) {
                throw IllegalStateException(parseApiError(text, code))
            }
            JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }

    private fun parseApiError(text: String, code: Int): String = runCatching {
        JSONObject(text).optJSONObject("error")?.optString("message")
    }.getOrNull().orEmpty().ifBlank { "请求失败（$code）" }
}
