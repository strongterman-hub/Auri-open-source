package com.auri.chat

import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

data class CreditsBalance(
    val balanceCredits: String,
    val creditsPerYuan: Int,
)

data class CreditsOrder(
    val orderId: String,
    val amountYuan: Int,
    val credits: Int,
    val status: String,
    val orderString: String?,
)

class CreditsApi(private val baseUrl: String = ApiConfig.BASE_URL) {
    fun balance(token: String): CreditsBalance {
        val json = request("GET", "/billing/balance", null, token)
        return CreditsBalance(
            balanceCredits = json.getString("balance_credits"),
            creditsPerYuan = json.getInt("credits_per_yuan"),
        )
    }

    fun createOrder(amountYuan: Int, token: String): CreditsOrder {
        val body = JSONObject().put("amount_yuan", amountYuan).toString()
        return parseOrder(request("POST", "/billing/orders", body, token))
    }

    fun order(orderId: String, token: String, refresh: Boolean): CreditsOrder {
        val path = "/billing/orders/$orderId?refresh=$refresh"
        return parseOrder(request("GET", path, null, token))
    }

    private fun parseOrder(json: JSONObject): CreditsOrder = CreditsOrder(
        orderId = json.getString("order_id"),
        amountYuan = json.getInt("amount_yuan"),
        credits = json.getInt("credits"),
        status = json.getString("status"),
        orderString = if (json.isNull("order_string")) null else json.optString("order_string"),
    )

    private fun request(
        method: String,
        path: String,
        body: String?,
        token: String,
    ): JSONObject {
        val connection = URL(baseUrl + path).openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = method
            connection.connectTimeout = 30_000
            connection.readTimeout = 30_000
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
                val message = runCatching {
                    JSONObject(text).optJSONObject("error")?.optString("message")
                }.getOrNull().orEmpty().ifBlank { "请求失败（$code）" }
                throw IllegalStateException(message)
            }
            JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }
}
