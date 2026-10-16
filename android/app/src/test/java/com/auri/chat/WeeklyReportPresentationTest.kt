package com.auri.chat

import org.junit.Assert.assertEquals
import org.junit.Test

class WeeklyReportPresentationTest {
    @Test fun previewKeepsShortSummaryAndBoundsLongMessagesWithoutSplittingEmoji() {
        assertEquals("本周活动记录较完整。", weeklyReportPreview("  本周活动记录较完整。 \n"))
        val preview = weeklyReportPreview("好".repeat(78) + "🙂".repeat(10))
        assertEquals(80, preview.codePointCount(0, preview.length))
        assertEquals("好".repeat(78) + "🙂…", preview)
        assertEquals(false, weeklyReportPreview("").isBlank())
    }
    private fun report(status: String, error: String = "", html: String = "") = WeeklyReport(
        id = "a".repeat(32), weekStart = "2026-10-05", weekEnd = "2026-10-11",
        status = status, summary = "", html = html, error = error,
    )

    @Test fun backoffFailureStopsSpinnerImmediately() {
        assertEquals(WeeklyReportPresentation.Failed, weeklyReportPresentation(report("queued", "生成超时，稍后重试"), 1_000))
    }

    @Test fun longGenerationMovesToBackgroundWithoutSpinner() {
        assertEquals(WeeklyReportPresentation.Generating, weeklyReportPresentation(report("generating"), 44_999))
        assertEquals(WeeklyReportPresentation.Background, weeklyReportPresentation(report("generating"), 45_000))
    }

    @Test fun cachedBodyRemainsReadableWhenAnUpdateFailsOrIsQueued() {
        for (status in listOf("failed", "queued", "generating")) {
            assertEquals(WeeklyReportPresentation.Ready, weeklyReportPresentation(report(status, "更新失败", "<p>已保存正文</p>"), 90_000))
        }
    }

    @Test fun completedWithoutBodyIsAnErrorRatherThanEndlessLoading() {
        assertEquals(WeeklyReportPresentation.Failed, weeklyReportPresentation(report("ready"), 1_000))
        assertEquals(WeeklyReportPresentation.Failed, weeklyReportPresentation(report("failed", "缺少健康记录"), 1_000))
    }
}
