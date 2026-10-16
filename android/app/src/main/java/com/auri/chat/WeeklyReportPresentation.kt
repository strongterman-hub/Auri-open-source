package com.auri.chat

internal enum class WeeklyReportPresentation { Reading, Ready, Generating, Background, Failed }

internal fun weeklyReportPreview(text: String): String {
    val compact = text.replace(Regex("\\s+"), " ").trim()
        .ifBlank { "这份健康回顾的内容与生成进度，可以在详情中查看。" }
    val length = compact.codePointCount(0, compact.length)
    return if (length <= 80) compact else compact.substring(0, compact.offsetByCodePoints(0, 79)).trimEnd() + "…"
}

internal fun weeklyReportPresentation(report: WeeklyReport?, elapsedMs: Long): WeeklyReportPresentation = when {
    report == null -> WeeklyReportPresentation.Reading
    report.html.isNotBlank() -> WeeklyReportPresentation.Ready
    report.error.isNotBlank() || report.status !in listOf("queued", "generating") -> WeeklyReportPresentation.Failed
    elapsedMs >= 45_000 -> WeeklyReportPresentation.Background
    else -> WeeklyReportPresentation.Generating
}
