package com.auri.chat

import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime

data class ScheduleEvent(
    val id: String,
    val title: String,
    val startsAt: OffsetDateTime,
    val endsAt: OffsetDateTime,
    val timezone: String,
    val allDay: Boolean,
    val location: String,
    val notes: String,
    val reminderMinutes: Int?,
    val repeat: String,
    val repeatUntil: LocalDate?,
    val status: String,
    val version: Int,
    val occurrenceDate: LocalDate,
    val isException: Boolean,
)

data class ScheduleSeries(
    val id: String,
    val title: String,
    val startsAt: LocalDateTime,
    val endsAt: LocalDateTime,
    val timezone: String,
    val allDay: Boolean,
    val location: String,
    val notes: String,
    val reminderMinutes: Int?,
    val repeat: String,
    val repeatUntil: LocalDate?,
    val status: String,
    val version: Int,
    val exceptionCount: Int,
)

data class ScheduleDraft(
    val title: String,
    val startsAt: LocalDateTime,
    val endsAt: LocalDateTime,
    val timezone: String,
    val allDay: Boolean,
    val location: String = "",
    val notes: String = "",
    val reminderMinutes: Int? = null,
    val repeat: String = "none",
    val repeatUntil: LocalDate? = null,
    val status: String = "scheduled",
)

class ScheduleApiException(
    message: String,
    val statusCode: Int,
    val kind: String? = null,
) : IllegalStateException(message)

class ScheduleApi(private val baseUrl: String = ApiConfig.BASE_URL) {
    fun timezone(token: String): String = request("GET", "/profile/timezone", null, token)
        .getString("timezone")

    fun list(start: LocalDate, endExclusive: LocalDate, token: String): List<ScheduleEvent> {
        val json = request("GET", "/schedule?start=$start&end=$endExclusive", null, token)
        val events = json.getJSONArray("events")
        return List(events.length()) { parseOccurrence(events.getJSONObject(it)) }
    }

    fun get(id: String, token: String): ScheduleSeries = parseSeries(
        request("GET", "/schedule/$id", null, token),
    )

    fun create(draft: ScheduleDraft, requestId: String, allowConflicts: Boolean, token: String): ScheduleSeries {
        val body = JSONObject()
            .put("request_id", requestId)
            .put("allow_conflicts", allowConflicts)
            .put("event", draftJson(draft))
        return parseSeries(request("POST", "/schedule", body.toString(), token).getJSONObject("event"))
    }

    fun update(
        id: String,
        version: Int,
        draft: ScheduleDraft,
        scope: String,
        occurrenceDate: LocalDate?,
        requestId: String,
        allowConflicts: Boolean,
        resetExceptions: Boolean,
        token: String,
    ): ScheduleSeries {
        val changes = draftJson(draft)
        if (scope == "occurrence") {
            changes.remove("repeat")
            changes.remove("repeat_until")
        }
        val body = JSONObject()
            .put("request_id", requestId)
            .put("version", version)
            .put("scope", scope)
            .put("changes", changes)
            .put("allow_conflicts", allowConflicts)
            .put("reset_exceptions", resetExceptions)
        occurrenceDate?.let { body.put("occurrence_date", it.toString()) }
        return parseSeries(request("PATCH", "/schedule/$id", body.toString(), token).getJSONObject("event"))
    }

    fun updateStatus(
        event: ScheduleEvent,
        status: String,
        scope: String,
        requestId: String,
        token: String,
    ): ScheduleSeries {
        val body = JSONObject()
            .put("request_id", requestId)
            .put("version", event.version)
            .put("scope", scope)
            .put("changes", JSONObject().put("status", status))
            .put("allow_conflicts", true)
            .put("reset_exceptions", false)
        if (scope == "occurrence") body.put("occurrence_date", event.occurrenceDate.toString())
        return parseSeries(request("PATCH", "/schedule/${event.id}", body.toString(), token).getJSONObject("event"))
    }

    private fun draftJson(draft: ScheduleDraft) = JSONObject()
        .put("title", draft.title)
        .put("starts_at", draft.startsAt.toString())
        .put("ends_at", draft.endsAt.toString())
        .put("timezone", draft.timezone)
        .put("all_day", draft.allDay)
        .put("location", draft.location)
        .put("notes", draft.notes)
        .put("reminder_minutes", draft.reminderMinutes ?: JSONObject.NULL)
        .put("repeat", draft.repeat)
        .put("repeat_until", draft.repeatUntil?.toString() ?: JSONObject.NULL)
        .put("status", draft.status)

    private fun parseOccurrence(json: JSONObject) = ScheduleEvent(
        id = json.getString("id"),
        title = json.getString("title"),
        startsAt = OffsetDateTime.parse(json.getString("starts_at")),
        endsAt = OffsetDateTime.parse(json.getString("ends_at")),
        timezone = json.getString("timezone"),
        allDay = json.getBoolean("all_day"),
        location = json.optString("location"),
        notes = json.optString("notes"),
        reminderMinutes = json.optIntOrNull("reminder_minutes"),
        repeat = json.optString("repeat", "none"),
        repeatUntil = json.optStringOrNull("repeat_until")?.let(LocalDate::parse),
        status = json.getString("status"),
        version = json.getInt("version"),
        occurrenceDate = LocalDate.parse(json.getString("occurrence_date")),
        isException = json.optBoolean("is_exception"),
    )

    private fun parseSeries(json: JSONObject) = ScheduleSeries(
        id = json.getString("id"),
        title = json.getString("title"),
        startsAt = LocalDateTime.parse(json.getString("starts_at")),
        endsAt = LocalDateTime.parse(json.getString("ends_at")),
        timezone = json.getString("timezone"),
        allDay = json.getBoolean("all_day"),
        location = json.optString("location"),
        notes = json.optString("notes"),
        reminderMinutes = json.optIntOrNull("reminder_minutes"),
        repeat = json.optString("repeat", "none"),
        repeatUntil = json.optStringOrNull("repeat_until")?.let(LocalDate::parse),
        status = json.getString("status"),
        version = json.getInt("version"),
        exceptionCount = json.optInt("exception_count", 0),
    )

    private fun JSONObject.optStringOrNull(name: String): String? =
        if (isNull(name)) null else optString(name).takeIf { it.isNotBlank() && it != "null" }

    private fun JSONObject.optIntOrNull(name: String): Int? =
        if (isNull(name) || !has(name)) null else getInt(name)

    private fun request(method: String, path: String, body: String?, token: String): JSONObject {
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
                val detail = runCatching {
                    val root = JSONObject(text)
                    val raw = root.opt("detail")
                    if (raw is JSONObject) raw else root.optJSONObject("error")
                }.getOrNull()
                throw ScheduleApiException(
                    detail?.optString("message")?.takeIf { it.isNotBlank() } ?: "日程请求失败（$code）",
                    code,
                    detail?.optString("kind")?.takeIf { it.isNotBlank() },
                )
            }
            JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }
}
