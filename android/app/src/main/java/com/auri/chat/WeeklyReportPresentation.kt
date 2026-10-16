package com.auri.chat

internal enum class WeeklyReportPresentation { Reading, Ready, Generating, Background, Failed }

internal fun weeklyReportPresentation(report: WeeklyReport?, elapsedMs: Long): WeeklyReportPresentation = when {
    report == null -> WeeklyReportPresentation.Reading
    report.html.isNotBlank() -> WeeklyReportPresentation.Ready
    report.error.isNotBlank() || report.status !in listOf("queued", "generating") -> WeeklyReportPresentation.Failed
    elapsedMs >= 45_000 -> WeeklyReportPresentation.Background
    else -> WeeklyReportPresentation.Generating
}
