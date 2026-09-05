package com.auri.chat

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.LocalDate
import java.time.YearMonth
import java.time.ZoneId
import java.util.UUID

private const val DEFAULT_SCHEDULE_TIMEZONE = "Asia/Shanghai"

data class ScheduleUiState(
    val month: YearMonth = YearMonth.now(ZoneId.of(DEFAULT_SCHEDULE_TIMEZONE)),
    val selectedDate: LocalDate = LocalDate.now(ZoneId.of(DEFAULT_SCHEDULE_TIMEZONE)),
    val timezone: String = DEFAULT_SCHEDULE_TIMEZONE,
    val events: List<ScheduleEvent> = emptyList(),
    val isLoading: Boolean = false,
    val isSaving: Boolean = false,
    val error: String? = null,
)

sealed interface SaveResult {
    data object Success : SaveResult
    data class ConfirmConflict(val retry: () -> Unit) : SaveResult
    data class ConfirmReset(val retry: () -> Unit) : SaveResult
    data class Failure(val message: String) : SaveResult
}

class ScheduleViewModel(application: Application) : AndroidViewModel(application) {
    private val api = ScheduleApi()
    private val auth = AuthStore(application)
    private val _state = MutableStateFlow(ScheduleUiState())
    val state: StateFlow<ScheduleUiState> = _state.asStateFlow()
    private var refreshJob: Job? = null
    private var alignPending = true

    init { refresh(alignToTimezone = true) }

    fun select(day: LocalDate) {
        _state.value = _state.value.copy(selectedDate = day)
    }

    fun moveMonth(delta: Long) {
        val month = _state.value.month.plusMonths(delta)
        alignPending = false
        _state.value = _state.value.copy(month = month, selectedDate = month.atDay(1))
        refresh()
    }

    fun today() {
        val zone = runCatching { ZoneId.of(_state.value.timezone) }
            .getOrDefault(ZoneId.of(DEFAULT_SCHEDULE_TIMEZONE))
        val day = LocalDate.now(zone)
        alignPending = false
        _state.value = _state.value.copy(month = YearMonth.from(day), selectedDate = day)
        refresh()
    }

    fun refresh(alignToTimezone: Boolean = false) {
        val shouldAlign = alignToTimezone || alignPending
        refreshJob?.cancel()
        refreshJob = viewModelScope.launch {
            val token = auth.getToken() ?: return@launch
            val current = _state.value
            _state.value = _state.value.copy(isLoading = true, error = null)
            runCatching {
                withContext(Dispatchers.IO) {
                    val timezone = runCatching { api.timezone(token) }.getOrDefault(current.timezone)
                    val zone = runCatching { ZoneId.of(timezone) }
                        .getOrDefault(ZoneId.of(DEFAULT_SCHEDULE_TIMEZONE))
                    val today = LocalDate.now(zone)
                    val month = if (shouldAlign) YearMonth.from(today) else current.month
                    Triple(timezone, today, api.list(month.atDay(1), month.plusMonths(1).atDay(1), token))
                }
            }
                .onSuccess { (timezone, today, events) ->
                    alignPending = false
                    _state.value = _state.value.copy(
                        month = if (shouldAlign) YearMonth.from(today) else current.month,
                        selectedDate = if (shouldAlign) today else current.selectedDate,
                        timezone = timezone,
                        events = events,
                        isLoading = false,
                    )
                }
                .onFailure {
                    if (it is CancellationException) return@onFailure
                    _state.value = _state.value.copy(isLoading = false, error = it.message ?: "日程加载失败")
                }
        }
    }

    suspend fun series(id: String): Result<ScheduleSeries> {
        val token = auth.getToken() ?: return Result.failure(IllegalStateException("登录已过期"))
        return runCatching { withContext(Dispatchers.IO) { api.get(id, token) } }
    }

    fun create(draft: ScheduleDraft, allowConflicts: Boolean = false, requestId: String = UUID.randomUUID().toString(), done: (SaveResult) -> Unit) {
        save(requestId, allowConflicts, false, done) { token, allow, _ -> api.create(draft, requestId, allow, token) }
    }

    fun update(event: ScheduleEvent, draft: ScheduleDraft, scope: String, allowConflicts: Boolean = false,
               resetExceptions: Boolean = false, requestId: String = UUID.randomUUID().toString(), done: (SaveResult) -> Unit) {
        save(requestId, allowConflicts, resetExceptions, done) { token, allow, reset ->
            api.update(event.id, event.version, draft, scope, event.occurrenceDate, requestId, allow, reset, token)
        }
    }

    fun setStatus(event: ScheduleEvent, status: String, scope: String, done: (SaveResult) -> Unit) {
        val requestId = UUID.randomUUID().toString()
        save(requestId, true, false, done) { token, _, _ -> api.updateStatus(event, status, scope, requestId, token) }
    }

    private fun save(requestId: String, allowConflicts: Boolean, resetExceptions: Boolean,
                     done: (SaveResult) -> Unit, action: (String, Boolean, Boolean) -> ScheduleSeries) {
        viewModelScope.launch {
            val token = auth.getToken()
            if (token == null) { done(SaveResult.Failure("登录已过期")); return@launch }
            _state.value = _state.value.copy(isSaving = true, error = null)
            runCatching { withContext(Dispatchers.IO) { action(token, allowConflicts, resetExceptions) } }
                .onSuccess { _state.value = _state.value.copy(isSaving = false); refresh(); done(SaveResult.Success) }
                .onFailure { error ->
                    _state.value = _state.value.copy(isSaving = false)
                    when {
                        error is ScheduleApiException && error.kind == "conflict" && !allowConflicts ->
                            done(SaveResult.ConfirmConflict { save(requestId, true, resetExceptions, done, action) })
                        error is ScheduleApiException && error.kind == "exceptions" && !resetExceptions ->
                            done(SaveResult.ConfirmReset { save(requestId, allowConflicts, true, done, action) })
                        else -> done(SaveResult.Failure(error.message ?: "保存失败"))
                    }
                }
        }
    }

    fun clearError() { _state.value = _state.value.copy(error = null) }
}
