package com.auri.chat

import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

class HealthApi(
    private val baseUrl: String = ApiConfig.BASE_URL,
) {
    data class FetchResult(
        val metrics: List<HealthMetricEntity>,
        val samples: List<HealthSampleEntity>,
    )

    data class SleepScoreFetchResult(
        val visible: Boolean,
        val scores: List<SleepScoreEntity>,
    )

    fun fetch(
        token: String,
        fromDay: String,
        toDay: String,
    ): FetchResult {
        val encodedFrom = URLEncoder.encode(fromDay, Charsets.UTF_8.name())
        val encodedTo = URLEncoder.encode(toDay, Charsets.UTF_8.name())
        val response = request(
            "GET",
            "/health/metrics?from_day=$encodedFrom&to_day=$encodedTo",
            null,
            token,
        )
        val array = response.getJSONArray("metrics")
        val metrics = buildList {
            for (index in 0 until array.length()) {
                val item = array.getJSONObject(index)
                add(
                    HealthMetricEntity(
                        id = "${item.getString("metric_type")}:${item.getString("day")}",
                        metricType = item.getString("metric_type"),
                        day = item.getString("day"),
                        value1 = item.optNullableDouble("value1"),
                        value2 = item.optNullableDouble("value2"),
                        value3 = item.optNullableDouble("value3"),
                        source = item.optNullableString("source"),
                        sourceUpdatedAt = item.optLong("source_updated_at", 0L),
                        resolutionPolicy = item.optNullableString("resolution_policy"),
                        updatedAt = item.optLong("updated_at", 0L),
                    ),
                )
            }
        }
        val sampleArray = response.optJSONArray("samples") ?: JSONArray()
        val samples = buildList {
            for (index in 0 until sampleArray.length()) {
                val item = sampleArray.getJSONObject(index)
                add(
                    HealthSampleEntity(
                        id = "${item.getString("metric_type")}:${item.getString("day")}:${item.getString("bucket_start")}",
                        metricType = item.getString("metric_type"),
                        day = item.getString("day"),
                        bucketStart = item.getString("bucket_start"),
                        bucketEnd = item.getString("bucket_end"),
                        value1 = item.optNullableDouble("value1"),
                        value2 = item.optNullableDouble("value2"),
                        value3 = item.optNullableDouble("value3"),
                        value4 = item.optNullableString("value4"),
                        updatedAt = item.optLong("updated_at", 0L),
                    ),
                )
            }
        }
        return FetchResult(metrics, samples)
    }

    fun fetchSleepScores(
        token: String,
        fromDay: String,
        toDay: String,
    ): SleepScoreFetchResult {
        val encodedFrom = URLEncoder.encode(fromDay, Charsets.UTF_8.name())
        val encodedTo = URLEncoder.encode(toDay, Charsets.UTF_8.name())
        val response = request(
            "GET",
            "/health/sleep-scores?from_day=$encodedFrom&to_day=$encodedTo",
            null,
            token,
        )
        val array = response.optJSONArray("scores") ?: JSONArray()
        val scores = buildList {
            for (index in 0 until array.length()) {
                val item = array.getJSONObject(index)
                val health = item.getJSONObject("sleep_health")
                val recovery = item.getJSONObject("recovery")
                val calibration = item.getJSONObject("calibration")
                val meta = item.getJSONObject("meta")
                val sleepDay = item.getString("sleep_day")
                val algorithmVersion = meta.getString("algorithm_version")
                add(
                    SleepScoreEntity(
                        id = "$algorithmVersion:$sleepDay",
                        sleepDay = sleepDay,
                        sessionStart = item.getString("session_start"),
                        sessionEnd = item.getString("session_end"),
                        sleepHealthScore = health.optNullableInt("score"),
                        sleepHealthConfidence = health.optInt("confidence", 0),
                        sleepHealthStatus = health.optString("status", "insufficient_data"),
                        sleepHealthComponents = health.optJSONObject("components")?.toString() ?: "{}",
                        recoveryScore = recovery.optNullableInt("score"),
                        recoveryConfidence = recovery.optInt("confidence", 0),
                        recoveryStatus = recovery.optString("status", "insufficient_data"),
                        recoveryComponents = recovery.optJSONObject("components")?.toString() ?: "{}",
                        recoveryMissing = recovery.optJSONArray("missing")?.toString() ?: "[]",
                        calibrationState = calibration.optString("state", "no_data"),
                        calibrationDay = calibration.optNullableInt("day"),
                        calibrationTotalDays = calibration.optInt("total_days", 14),
                        validNights = calibration.optInt("valid_nights", 0),
                        algorithmVersion = algorithmVersion,
                        vendorScore = meta.optNullableInt("vendor_score"),
                        computedAt = meta.optLong("computed_at", 0L),
                    ),
                )
            }
        }
        return SleepScoreFetchResult(response.optBoolean("visible", false), scores)
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
            connection.readTimeout = 60_000
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

    private fun JSONObject.optNullableDouble(key: String): Double? =
        if (isNull(key)) null else optDouble(key)

    private fun JSONObject.optNullableInt(key: String): Int? =
        if (!has(key) || isNull(key)) null else optInt(key)

    private fun JSONObject.optNullableString(key: String): String? =
        if (isNull(key) || !has(key)) null else optString(key)

    private fun parseError(text: String): String =
        runCatching {
            val root = JSONObject(text)
            val message = root.optJSONObject("error")?.optString("message")
                ?: root.optString("detail").ifBlank { null }
            message.orEmpty().ifBlank { text }
        }.getOrDefault(text).ifBlank { "健康数据请求失败" }
}
