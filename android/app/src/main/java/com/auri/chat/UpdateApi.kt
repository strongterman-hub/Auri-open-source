package com.auri.chat

import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

data class UpdateInfo(
    val versionCode: Int,
    val versionName: String,
    val force: Boolean,
    val downloadUrl: String?,
    val sha256: String?,
    val apkSize: Long,
    val changelog: String,
)

class UpdateApi(private val baseUrl: String = ApiConfig.BASE_URL) {
    fun check(): UpdateInfo {
        val connection = URL("$baseUrl/update/check").openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "GET"
            connection.connectTimeout = 30_000
            connection.readTimeout = 30_000
            connection.useCaches = false
            connection.setRequestProperty("Accept", "application/json")
            connection.setRequestProperty("Cache-Control", "no-cache")

            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (code !in 200..299) {
                throw IllegalStateException("HTTP $code: $text")
            }

            val json = JSONObject(text)
            UpdateInfo(
                versionCode = json.optInt("version_code", 0),
                versionName = json.optString("version_name", ""),
                force = json.optBoolean("force", false),
                downloadUrl = json.optNullableString("download_url"),
                sha256 = json.optNullableString("sha256"),
                apkSize = json.optLong("apk_size", 0L),
                changelog = json.optString("changelog", ""),
            )
        } finally {
            connection.disconnect()
        }
    }

    fun download(url: String, target: File, onProgress: (Long, Long) -> Unit) {
        val connection = URL(url).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "GET"
            connection.connectTimeout = 30_000
            connection.readTimeout = 120_000
            connection.useCaches = false
            connection.setRequestProperty("Cache-Control", "no-cache")

            val code = connection.responseCode
            if (code !in 200..299) {
                val text = connection.errorStream?.bufferedReader()?.use { it.readText() }.orEmpty()
                throw IllegalStateException("HTTP $code: $text")
            }

            val total = connection.contentLengthLong
            connection.inputStream.use { input ->
                target.outputStream().use { output ->
                    val buffer = ByteArray(8192)
                    var received = 0L
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        output.write(buffer, 0, read)
                        received += read
                        onProgress(received, total)
                    }
                }
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun JSONObject.optNullableString(key: String): String? =
        if (isNull(key)) null else optString(key).takeIf { it.isNotBlank() }
}
