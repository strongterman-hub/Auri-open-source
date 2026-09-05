package com.auri.chat

import java.time.LocalDate
import java.time.ZoneId

// Health data dates are authored by the server in the user's timezone. Use the
// device default zone for client-side "today"/range math so it matches the
// timezone the device reports to the server.
val HealthZone: ZoneId = ZoneId.systemDefault()

fun todayInHealthZone(): String = LocalDate.now(HealthZone).toString()

enum class MetricType(
    val label: String,
    val unit: String,
    val category: String,
) {
    STEPS("步数", "步", "活动"),
    HEART_RATE("心率", "次/分", "心率"),
    RESTING_HEART_RATE("静息心率", "次/分", "心率"),
    SLEEP("睡眠", "", "睡眠"),
    RECOVERY_SCORE("恢复得分", "分", "恢复"),
    DISTANCE("距离", "公里", "活动"),
    CALORIES("消耗", "千卡", "活动"),
    WEIGHT("体重", "公斤", "身体成分"),
    BODY_FAT("体脂率", "%", "身体成分"),
    BMI("BMI", "", "身体成分"),
    MUSCLE_MASS("肌肉量", "公斤", "身体成分"),
    BODY_WATER("水分率", "%", "身体成分"),
    BONE_MASS("骨量", "公斤", "身体成分"),
    VISCERAL_FAT("内脏脂肪", "级", "身体成分"),
    BMR("基础代谢", "千卡", "身体成分"),
    SPO2("血氧", "%", "血氧与压力"),
    STRESS("压力", "分", "血氧与压力"),
}

fun SleepScoreEntity.toRecoveryMetric(): DailyMetric? = recoveryScore?.let { score ->
    DailyMetric(
        metricType = MetricType.RECOVERY_SCORE,
        day = sleepDay,
        value1 = score.toDouble(),
        value2 = null,
        value3 = null,
    )
}

enum class HealthSampleType {
    HEART_RATE,
    RESTING_HEART_RATE,
    SLEEP_STAGE,
    SLEEP_SESSION,
    SLEEP_HRV,
    SPO2,
    STRESS,
    WORKOUT,
    ABNORMAL_HEART_BEAT,
}

data class DailyMetric(
    val metricType: MetricType,
    val day: String,
    val value1: Double?,
    val value2: Double?,
    val value3: Double?,
    val source: String? = null,
    val sourceUpdatedAt: Long = 0L,
    val resolutionPolicy: String? = null,
    val updatedAt: Long = 0L,
)

fun HealthMetricEntity.toDailyMetric(): DailyMetric? {
    val type = runCatching { MetricType.valueOf(metricType) }.getOrNull() ?: return null
    return DailyMetric(
        metricType = type,
        day = day,
        value1 = value1,
        value2 = value2,
        value3 = value3,
        source = source,
        sourceUpdatedAt = sourceUpdatedAt,
        resolutionPolicy = resolutionPolicy,
        updatedAt = updatedAt,
    )
}
