package com.auri.chat

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class HealthUiState(
    val metrics: List<DailyMetric> = emptyList(),
    val samples: List<HealthSampleEntity> = emptyList(),
    val sleepScores: List<SleepScoreEntity> = emptyList(),
    val dualSleepScoreVisible: Boolean = false,
    val workoutSamples: List<HealthSampleEntity> = emptyList(),
    val isLoading: Boolean = false,
    val error: String? = null,
    val xiaomiBound: Boolean = false,
    val xiaomiLastSyncAt: Long? = null,
    val isXiaomiSyncing: Boolean = false,
)

data class MetricDetailData(
    val metrics: List<DailyMetric>,
    val samples: List<HealthSampleEntity>,
    val sleepScores: List<SleepScoreEntity> = emptyList(),
    val dualSleepScoreVisible: Boolean = false,
)

// The overview is a "latest value per metric" snapshot, so it must not be hidden
// behind a short recency window. Fetch a generous history window to populate the
// local cache; the overview then reads only the latest row per metric type.
private const val OVERVIEW_FETCH_RANGE_DAYS = 365
private const val WORKOUT_RANGE_DAYS = 30
private const val AUTO_SYNC_STALE_MILLIS = 5 * 60 * 1000L

class HealthViewModel(application: Application) : AndroidViewModel(application) {
    private val repository = HealthRepository(application)
    private val authStore = AuthStore(application)
    private val healthApi = HealthApi()
    private val xiaomiApi = XiaomiApi()
    private val _state = MutableStateFlow(HealthUiState())
    val state: StateFlow<HealthUiState> = _state.asStateFlow()
    private var fetchedFromDay: String? = null

    init {
        loadCachedXiaomiStatus()
        load()
        pullRemote()
        refreshXiaomiStatus(autoSyncIfStale = true)
    }

    private fun loadCachedXiaomiStatus() {
        _state.value = _state.value.copy(
            xiaomiBound = authStore.getXiaomiBound(),
            xiaomiLastSyncAt = authStore.getXiaomiLastSyncAt(),
            dualSleepScoreVisible = authStore.getDualSleepScoreVisible(),
        )
    }

    fun load() {
        viewModelScope.launch {
            _state.value = _state.value.copy(isLoading = true, error = null)
            runCatching {
                val metrics = repository.loadLatestMetrics().mapNotNull { it.toDailyMetric() }
                val samples = repository.loadLatestSamples()
                val sleepScores = listOfNotNull(repository.loadLatestSleepScore())
                _state.value = _state.value.copy(
                    metrics = metrics,
                    samples = samples,
                    sleepScores = sleepScores,
                    isLoading = false,
                )
            }.onFailure {
                _state.value = _state.value.copy(
                    isLoading = false,
                    error = it.message ?: "加载本地数据失败",
                )
            }
        }
    }

    fun clearLocalCache() {
        viewModelScope.launch {
            runCatching { repository.clearLocal() }
            load()
        }
    }

    fun refreshXiaomiStatus(autoSyncIfStale: Boolean = false) {
        viewModelScope.launch {
            val token = authStore.getToken() ?: return@launch
            runCatching {
                withContext(Dispatchers.IO) { xiaomiApi.getStatus(token) }
            }.onSuccess { status ->
                authStore.saveXiaomiStatus(status.bound, status.lastSyncAt)
                _state.value = _state.value.copy(
                    xiaomiBound = status.bound,
                    xiaomiLastSyncAt = status.lastSyncAt,
                )
                val stale = status.lastSyncAt == null ||
                    System.currentTimeMillis() - status.lastSyncAt >= AUTO_SYNC_STALE_MILLIS
                if (autoSyncIfStale && status.bound && stale && !_state.value.isXiaomiSyncing) {
                    syncXiaomi()
                }
            }
        }
    }

    fun syncXiaomi() {
        viewModelScope.launch {
            val token = authStore.getToken() ?: return@launch
            _state.value = _state.value.copy(isXiaomiSyncing = true, isLoading = true, error = null)
            runCatching {
                withContext(Dispatchers.IO) { xiaomiApi.triggerSync(token) }
            }.onSuccess {
                val syncedAt = System.currentTimeMillis()
                authStore.saveXiaomiStatus(true, syncedAt)
                _state.value = _state.value.copy(
                    isXiaomiSyncing = false,
                    isLoading = false,
                    xiaomiBound = true,
                    xiaomiLastSyncAt = syncedAt,
                )
                pullRemote()
            }.onFailure {
                _state.value = _state.value.copy(
                    isXiaomiSyncing = false,
                    isLoading = false,
                    error = it.message ?: "同步小米健康数据失败",
                )
            }
        }
    }

    fun pullRemote() {
        viewModelScope.launch {
            val token = authStore.getToken() ?: return@launch
            val (fromDay, toDay) = repository.rangeBounds(OVERVIEW_FETCH_RANGE_DAYS)
            runCatching {
                val remote = withContext(Dispatchers.IO) {
                    healthApi.fetch(token, fromDay, toDay)
                }
                val remoteScores = withContext(Dispatchers.IO) {
                    runCatching { healthApi.fetchSleepScores(token, fromDay, toDay) }.getOrNull()
                }
                if (remote.metrics.isNotEmpty() || remote.samples.isNotEmpty()) {
                    repository.replaceRange(remote.metrics, remote.samples, fromDay, toDay)
                }
                fetchedFromDay = if (fetchedFromDay == null) fromDay else minOf(fetchedFromDay!!, fromDay)
                remoteScores?.let {
                    repository.replaceSleepScores(it.scores, fromDay, toDay)
                    authStore.saveDualSleepScoreVisible(it.visible)
                }
                val metrics = repository.loadLatestMetrics().mapNotNull { it.toDailyMetric() }
                val samples = repository.loadLatestSamples()
                val sleepScores = listOfNotNull(repository.loadLatestSleepScore())
                _state.value = _state.value.copy(
                    metrics = metrics,
                    samples = samples,
                    sleepScores = sleepScores,
                    dualSleepScoreVisible = remoteScores?.visible
                        ?: _state.value.dualSleepScoreVisible,
                    error = null,
                )
            }.onFailure {
                _state.value = _state.value.copy(
                    error = it.message ?: "从服务端拉取健康数据失败",
                )
            }
            refreshWorkouts()
        }
    }

    fun refreshWorkouts() {
        viewModelScope.launch {
            val token = authStore.getToken() ?: return@launch
            val (fromDay, toDay) = repository.rangeBounds(WORKOUT_RANGE_DAYS)
            runCatching {
                val remote = withContext(Dispatchers.IO) {
                    healthApi.fetch(token, fromDay, toDay)
                }
                val workouts = remote.samples.filter { it.metricType == HealthSampleType.WORKOUT.name }
                if (workouts.isNotEmpty()) {
                    repository.replaceSampleTypeRange(HealthSampleType.WORKOUT.name, workouts, fromDay, toDay)
                }
                val local = repository.loadSampleType(HealthSampleType.WORKOUT.name, WORKOUT_RANGE_DAYS)
                _state.value = _state.value.copy(workoutSamples = local)
            }.onFailure {
                // Keep the previously loaded workout list on refresh failure.
            }
        }
    }

    suspend fun fetchDetail(type: MetricType, days: Int, endDay: String?): MetricDetailData {
        val (fromDay, toDay) = repository.rangeBounds(days, endDay)
        val current = fetchedFromDay
        if (current == null || fromDay < current) {
            val token = authStore.getToken()
            if (token != null) {
                runCatching {
                    withContext(Dispatchers.IO) {
                        val remote = healthApi.fetch(token, fromDay, toDay)
                        if (remote.metrics.isNotEmpty() || remote.samples.isNotEmpty()) {
                            repository.replaceRange(remote.metrics, remote.samples, fromDay, toDay)
                        }
                        runCatching {
                            healthApi.fetchSleepScores(token, fromDay, toDay)
                        }.getOrNull()?.let { remoteScores ->
                            repository.replaceSleepScores(remoteScores.scores, fromDay, toDay)
                            authStore.saveDualSleepScoreVisible(remoteScores.visible)
                            _state.value = _state.value.copy(
                                dualSleepScoreVisible = remoteScores.visible,
                            )
                        }
                    }
                }.onSuccess {
                    fetchedFromDay = if (current == null) fromDay else minOf(current, fromDay)
                }
            }
        }
        val sleepScores = if (type == MetricType.SLEEP || type == MetricType.RECOVERY_SCORE) {
            repository.loadSleepScores(days, endDay)
        } else {
            emptyList()
        }
        val metrics = if (type == MetricType.RECOVERY_SCORE) {
            sleepScores.mapNotNull { it.toRecoveryMetric() }
        } else {
            repository.loadType(type.name, days, endDay).mapNotNull { it.toDailyMetric() }
        }
        val samples = sampleTypesFor(type).flatMap { repository.loadSampleType(it, days, endDay) }
        return MetricDetailData(metrics, samples, sleepScores, _state.value.dualSleepScoreVisible)
    }

    suspend fun cachedDetail(type: MetricType, days: Int, endDay: String?): MetricDetailData {
        val sleepScores = if (type == MetricType.SLEEP || type == MetricType.RECOVERY_SCORE) {
            repository.loadSleepScores(days, endDay)
        } else {
            emptyList()
        }
        val metrics = if (type == MetricType.RECOVERY_SCORE) {
            sleepScores.mapNotNull { it.toRecoveryMetric() }
        } else {
            repository.loadType(type.name, days, endDay).mapNotNull { it.toDailyMetric() }
        }
        val samples = sampleTypesFor(type).flatMap { repository.loadSampleType(it, days, endDay) }
        return MetricDetailData(metrics, samples, sleepScores, _state.value.dualSleepScoreVisible)
    }

    fun refreshDetail(type: MetricType, days: Int, endDay: String?, onResult: (MetricDetailData) -> Unit) {
        viewModelScope.launch {
            val result = runCatching { fetchDetail(type, days, endDay) }
                .recoverCatching { cachedDetail(type, days, endDay) }
            onResult(result.getOrElse { MetricDetailData(emptyList(), emptyList()) })
        }
    }

    private fun sampleTypesFor(type: MetricType): List<String> = when (type) {
        MetricType.HEART_RATE -> listOf("HEART_RATE")
        MetricType.SLEEP -> listOf(
            "SLEEP_STAGE",
            "SLEEP_SESSION",
            "RESTING_HEART_RATE",
            "SLEEP_HRV",
        )
        MetricType.SPO2 -> listOf("SPO2")
        MetricType.STRESS -> listOf("STRESS")
        else -> emptyList()
    }
}
