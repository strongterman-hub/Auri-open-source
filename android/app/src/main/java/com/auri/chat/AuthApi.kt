package com.auri.chat

import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

data class AuthResult(
    val token: String,
    val email: String,
)

data class EmailCodeResult(
    val expiresInSeconds: Int,
    val resendAfterSeconds: Int,
)

class AuthApi(
    private val baseUrl: String = ApiConfig.BASE_URL,
) {
    fun requestRegistrationCode(email: String): EmailCodeResult {
        val body = JSONObject().put("email", email).toString()
        val json = postJson("/auth/register/code", body)
        return EmailCodeResult(
            expiresInSeconds = json.getInt("expires_in_seconds"),
            resendAfterSeconds = json.getInt("resend_after_seconds"),
        )
    }

    fun requestPasswordChangeCode(token: String): EmailCodeResult {
        val json = postJson("/auth/password/code", JSONObject().toString(), token)
        return EmailCodeResult(
            expiresInSeconds = json.getInt("expires_in_seconds"),
            resendAfterSeconds = json.getInt("resend_after_seconds"),
        )
    }

    fun requestPasswordResetCode(email: String): EmailCodeResult {
        val body = JSONObject().put("email", email).toString()
        val json = postJson("/auth/password/reset/code", body)
        return EmailCodeResult(
            expiresInSeconds = json.getInt("expires_in_seconds"),
            resendAfterSeconds = json.getInt("resend_after_seconds"),
        )
    }

    fun changePassword(
        token: String,
        verificationCode: String,
        newPassword: String,
    ): AuthResult {
        val payload = JSONObject()
            .put("verification_code", verificationCode)
            .put("new_password", newPassword)
        return parseAuthResult(postJson("/auth/password", payload.toString(), token))
    }

    fun resetPassword(
        email: String,
        verificationCode: String,
        newPassword: String,
    ): AuthResult {
        val payload = JSONObject()
            .put("email", email)
            .put("verification_code", verificationCode)
            .put("new_password", newPassword)
        return parseAuthResult(postJson("/auth/password/reset", payload.toString()))
    }

    fun register(email: String, password: String, verificationCode: String): AuthResult =
        call("/auth/register", email, password, verificationCode)

    fun login(email: String, password: String): AuthResult =
        call("/auth/login", email, password, null)

    fun logout(token: String) {
        val connection = URL(baseUrl + "/auth/logout").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 30_000
            connection.readTimeout = 30_000
            connection.setRequestProperty("Authorization", "Bearer $token")
            connection.responseCode
        } finally {
            connection.disconnect()
        }
    }

    fun deleteAccount(token: String) {
        val connection = URL(baseUrl + "/auth/account").openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "DELETE"
            connection.connectTimeout = 30_000
            connection.readTimeout = 30_000
            connection.setRequestProperty("Authorization", "Bearer $token")
            val code = connection.responseCode
            if (code !in 200..299) {
                val text = connection.errorStream
                    ?.bufferedReader()
                    ?.use(BufferedReader::readText)
                    .orEmpty()
                throw IllegalStateException(parseError(text))
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun call(
        path: String,
        email: String,
        password: String,
        verificationCode: String?,
    ): AuthResult {
        val payload = JSONObject()
            .put("email", email)
            .put("password", password)
        if (verificationCode != null) {
            payload.put("verification_code", verificationCode)
        }
        return parseAuthResult(postJson(path, payload.toString()))
    }

    private fun parseAuthResult(json: JSONObject): AuthResult {
        return AuthResult(
            token = json.getString("token"),
            email = json.getJSONObject("user").getString("email"),
        )
    }

    private fun postJson(path: String, body: String, token: String? = null): JSONObject {
        val connection = URL(baseUrl + path).openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 60_000
            connection.readTimeout = 60_000
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("Accept", "application/json")
            if (token != null) {
                connection.setRequestProperty("Authorization", "Bearer $token")
            }
            connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }

            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader()?.use(BufferedReader::readText).orEmpty()
            if (code !in 200..299) {
                throw IllegalStateException(parseError(text))
            }
            JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }

    private fun parseError(text: String): String {
        return runCatching {
            val error = JSONObject(text).optJSONObject("error")
            error?.optString("message").orEmpty().ifBlank { text }
        }.getOrDefault(text).ifBlank { "请求失败" }
    }
}
