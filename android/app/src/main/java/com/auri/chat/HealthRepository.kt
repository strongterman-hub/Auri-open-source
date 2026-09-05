package com.auri.chat

import android.content.Context
import java.time.LocalDate

class HealthRepository(context: Context) {
    private val appContext = context.applicationContext
    private val dao = AuriDatabase.get(appContext).healthMetricDao()
    private val sampleDao = AuriDatabase.get(appContext).healthSampleDao()
    private val sleepScoreDao = AuriDatabase.get(appContext).sleepScoreDao()

    suspend fun load(rangeDays: Int, endDay: String? = null): List<HealthMetricEntity> {
        val (fromDay, toDay) = rangeBounds(rangeDays, endDay)
        return MetricType.entries.flatMap { type ->
            dao.getRange(type.name, fromDay, toDay)
        }
    }

    suspend fun loadSamples(rangeDays: Int, endDay: String? = null): List<HealthSampleEntity> {
        val (fromDay, toDay) = rangeBounds(rangeDays, endDay)
        return HealthSampleType.entries.flatMap { type ->
            sampleDao.getRange(type.name, fromDay, toDay)
        }
    }

    suspend fun loadLatestMetrics(): List<HealthMetricEntity> =
        MetricType.entries.mapNotNull { type -> dao.getLatest(type.name) }

    suspend fun loadLatestSamples(): List<HealthSampleEntity> =
        HealthSampleType.entries.mapNotNull { type -> sampleDao.getLatestSample(type.name) }

    suspend fun loadLatestSleepScore(): SleepScoreEntity? = sleepScoreDao.getLatest()

    suspend fun loadSleepScores(
        rangeDays: Int,
        endDay: String? = null,
    ): List<SleepScoreEntity> {
        val (fromDay, toDay) = rangeBounds(rangeDays, endDay)
        return sleepScoreDao.getRange(fromDay, toDay)
    }

    suspend fun loadType(type: String, rangeDays: Int, endDay: String? = null): List<HealthMetricEntity> {
        val (fromDay, toDay) = rangeBounds(rangeDays, endDay)
        return dao.getRange(type, fromDay, toDay)
    }

    suspend fun loadSampleType(type: String, rangeDays: Int, endDay: String? = null): List<HealthSampleEntity> {
        val (fromDay, toDay) = rangeBounds(rangeDays, endDay)
        return sampleDao.getRange(type, fromDay, toDay)
    }

    fun rangeBounds(rangeDays: Int, endDay: String? = null): Pair<String, String> {
        val end = endDay?.let { LocalDate.parse(it) } ?: LocalDate.now(HealthZone)
        val start = end.minusDays((rangeDays - 1).toLong())
        return start.toString() to end.toString()
    }

    suspend fun replaceRange(
        metrics: List<HealthMetricEntity>,
        samples: List<HealthSampleEntity>,
        fromDay: String,
        toDay: String,
    ) {
        MetricType.entries.forEach { type ->
            dao.deleteRange(type.name, fromDay, toDay)
        }
        if (metrics.isNotEmpty()) {
            dao.upsertAll(metrics)
        }
        HealthSampleType.entries.forEach { type ->
            sampleDao.deleteRange(type.name, fromDay, toDay)
        }
        if (samples.isNotEmpty()) {
            sampleDao.upsertAll(samples)
        }
    }

    suspend fun replaceSampleTypeRange(
        type: String,
        samples: List<HealthSampleEntity>,
        fromDay: String,
        toDay: String,
    ) {
        sampleDao.deleteRange(type, fromDay, toDay)
        if (samples.isNotEmpty()) {
            sampleDao.upsertAll(samples)
        }
    }

    suspend fun replaceSleepScores(
        scores: List<SleepScoreEntity>,
        fromDay: String,
        toDay: String,
    ) {
        sleepScoreDao.deleteRange(fromDay, toDay)
        if (scores.isNotEmpty()) {
            sleepScoreDao.upsertAll(scores)
        }
    }

    suspend fun clearLocal() {
        dao.clearAll()
        sampleDao.clearAll()
        sleepScoreDao.clearAll()
    }
}
