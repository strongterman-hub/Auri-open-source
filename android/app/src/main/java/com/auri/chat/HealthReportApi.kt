package com.auri.chat

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

internal data class WeeklyReport(
    val id: String, val weekStart: String, val weekEnd: String,
    val status: String, val summary: String, val html: String = "",
    val error: String = "", val revision: Int = 0,
)

internal class HealthReportApi {
    private fun request(method: String, path: String, token: String, body: JSONObject? = null): JSONObject {
        val connection = URL("${BuildConfig.API_BASE_URL}/health/reports$path").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = 15_000
            connection.readTimeout = 25_000
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.setRequestProperty("Accept", "application/json")
            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json")
                connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val code = connection.responseCode
            val text = (if (code in 200..299) connection.inputStream else connection.errorStream)?.bufferedReader()?.use { it.readText() }.orEmpty()
            val root = runCatching { JSONObject(text) }.getOrDefault(JSONObject())
            if (code !in 200..299) {
                throw IllegalStateException(root.optJSONObject("error")?.optString("message") ?: root.optString("detail").ifBlank { "周报暂时无法加载，请稍后重试" })
            }
            return root
        } finally { connection.disconnect() }
    }
    fun register(token: String) = request("POST", "/client", token, JSONObject().put("version_code", BuildConfig.VERSION_CODE))
    fun settings(token: String) = request("GET", "/settings", token)
    fun setEnabled(token: String, enabled: Boolean) = request("PUT", "/settings", token, JSONObject().put("enabled", enabled))
    fun generate(token: String): String = request("POST", "/weekly", token, JSONObject()).getString("id")
    fun refresh(token: String, id: String) = request("POST", "/$id/refresh", token, JSONObject())
    fun list(token: String): List<WeeklyReport> {
        val array = request("GET", "", token).optJSONArray("reports") ?: return emptyList()
        return (0 until array.length()).map { parse(array.getJSONObject(it)) }
    }
    fun get(token: String, id: String, includeHtml: Boolean = true): WeeklyReport {
        require(id.matches(Regex("[a-f0-9]{32}"))) { "周报入口无效" }
        return parse(request("GET", "/$id?include_html=$includeHtml", token))
    }
    private fun parse(item: JSONObject) = WeeklyReport(
        id = item.getString("id"), weekStart = item.getString("week_start"), weekEnd = item.getString("week_end"),
        status = item.getString("status"), summary = item.optString("summary"), html = item.optString("html"),
        error = if (item.isNull("error")) "" else item.optString("error"), revision = item.optInt("revision"),
    )
}
