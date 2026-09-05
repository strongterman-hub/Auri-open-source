package com.auri.chat

import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

data class XiaomiLoginStart(
    val sessionId: String,
    val qrImagePngBase64: String,
)

data class XiaomiLoginStatus(
    val status: String,
    val error: String?,
)

data class XiaomiSyncResult(
    val synced: Int,
    val metrics: Int,
    val samples: Int,
    val fromDay: String,
    val toDay: String,
    val availableDataTypes: List<String>,
)

data class XiaomiStatus(
    val bound: Boolean,
    val lastSyncAt: Long?,
    val availableDataTypes: List<String>,
)

class XiaomiApi(
    private val baseUrl: String = ApiConfig.BASE_URL,
) {
    fun startLogin(token: String): XiaomiLoginStart {
        val response = request("POST", "/integrations/xiaomi/login/start", null, token)
        return XiaomiLoginStart(
            sessionId = response.getString("session_id"),
            qrImagePngBase64 = response.getString("qr_image_png_base64"),
        )
    }

    fun pollLoginStatus(token: String, sessionId: String): XiaomiLoginStatus {
        val encoded = URLEncoder.encode(sessionId, Charsets.UTF_8.name())
        val response = request("GET", "/integrations/xiaomi/login/status?session_id=$encoded", null, token)
        return XiaomiLoginStatus(
            status = response.getString("status"),
            error = response.optNullableString("error"),
        )
    }

    fun triggerSync(token: String): XiaomiSyncResult {
        val response = request("POST", "/integrations/xiaomi/sync", null, token)
        return XiaomiSyncResult(
            synced = response.getInt("synced"),
            metrics = response.getInt("metrics"),
            samples = response.getInt("samples"),
            fromDay = response.getString("from_day"),
            toDay = response.getString("to_day"),
            availableDataTypes = response.optStringArray("available_data_types"),
        )
    }

    fun getStatus(token: String): XiaomiStatus {
        val response = request("GET", "/integrations/xiaomi/status", null, token)
        return XiaomiStatus(
            bound = response.getBoolean("bound"),
            lastSyncAt = response.optNullableLong("last_sync_at"),
            availableDataTypes = response.optStringArray("available_data_types"),
        )
    }

    fun disconnect(token: String) {
        request("DELETE", "/integrations/xiaomi", null, token)
    }

    private fun request(
        method: String,
        path: String,
        body: String?,
        token: String,
    ): JSONObject {
        val connection = URL(baseUrl + path).openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = method
            connection.connectTimeout = 60_000
            connection.readTimeout = 120_000
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("Accept", "application/json")
            connection.setRequestProperty("Authorization", "Bearer $token")

            if (body != null) {
                connection.doOutput = true
                connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }

            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader()?.use(BufferedReader::readText).orEmpty()
            if (code !in 200..299) {
                throw IllegalStateException(parseError(text))
            }
            if (text.isBlank()) JSONObject() else JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }

    private fun JSONObject.optNullableString(key: String): String? =
        if (isNull(key)) null else optString(key)

    private fun JSONObject.optNullableLong(key: String): Long? =
        if (isNull(key)) null else optLong(key)

    private fun JSONObject.optStringArray(key: String): List<String> {
        val array = optJSONArray(key) ?: return emptyList()
        return buildList {
            for (index in 0 until array.length()) {
                add(array.getString(index))
            }
        }
    }

    private fun parseError(text: String): String =
        runCatching {
            val message = JSONObject(text).optJSONObject("error")?.optString("message")
                ?: JSONObject(text).optString("detail").ifBlank { null }
            message.orEmpty().ifBlank { text }
        }.getOrDefault(text).ifBlank { "小米健康请求失败" }
}
