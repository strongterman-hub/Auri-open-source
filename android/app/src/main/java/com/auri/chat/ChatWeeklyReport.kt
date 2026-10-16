package com.auri.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowForward
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

private val ReportMetadataSaver = listSaver<WeeklyReport, String>(
    save = { listOf(it.id, it.weekStart, it.weekEnd, it.status, it.summary, it.error, it.revision.toString()) },
    restore = { WeeklyReport(it[0], it[1], it[2], it[3], it[4], error = it[5], revision = it[6].toInt()) },
)

@Composable
internal fun ChatWeeklyReport(reportId: String, initialSummary: String, onOpenReport: (String) -> Unit) {
    val context = LocalContext.current
    val token = remember { AuthStore(context).getToken().orEmpty() }
    val api = remember { HealthReportApi() }
    var report by rememberSaveable(reportId, stateSaver = ReportMetadataSaver) {
        mutableStateOf(WeeklyReport(reportId, "", "", "", ""))
    }
    LaunchedEffect(reportId) {
        try {
            while (true) {
                val fetched = withContext(Dispatchers.IO) { api.get(token, reportId, includeHtml = false) }
                report = fetched
                if (fetched.status !in listOf("queued", "generating")) break
                delay(15_000)
            }
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (_: Exception) {
            // The persisted message remains usable; detailed errors belong in the reader.
        }
    }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("健康周报", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
        Text(
            weeklyReportPreview(report.summary.takeIf { it.isNotBlank() } ?: initialSummary),
            style = MaterialTheme.typography.bodyMedium,
        )
        if (report.weekStart.isNotBlank()) {
            val saved = report
            Text(
                "${saved.weekStart} — ${saved.weekEnd}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (saved.status != "ready") {
                Text(
                    when {
                        saved.revision > 0 -> "已保存的报告可在详情中阅读"
                        saved.error.isNotBlank() || saved.status == "failed" -> "暂未生成，可在详情中重试"
                        else -> "正在整理，可在详情中查看进度"
                    },
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        TextButton(onClick = { onOpenReport(reportId) }, modifier = Modifier.fillMaxWidth()) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text("详细内容")
                Spacer(Modifier.weight(1f))
                Icon(Icons.AutoMirrored.Outlined.ArrowForward, contentDescription = null, modifier = Modifier.size(18.dp))
            }
        }
    }
}
