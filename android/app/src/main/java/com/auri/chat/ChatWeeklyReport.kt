package com.auri.chat

import android.os.SystemClock
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

@Composable
internal fun ChatWeeklyReport(reportId: String) {
    val context = LocalContext.current
    val token = remember { AuthStore(context).getToken().orEmpty() }
    val api = remember { HealthReportApi() }
    var report by remember(reportId) { mutableStateOf<WeeklyReport?>(null) }
    var error by remember(reportId) { mutableStateOf("") }
    var reload by remember(reportId) { mutableStateOf(0) }
    var retryGeneration by remember(reportId) { mutableStateOf(false) }
    var heightDp by remember(reportId) { mutableStateOf(400f) }
    var elapsedMs by remember(reportId) { mutableStateOf(0L) }
    LaunchedEffect(reportId, reload) {
        error = ""
        elapsedMs = 0L
        val started = SystemClock.elapsedRealtime()
        try {
            if (retryGeneration) {
                withContext(Dispatchers.IO) { api.refresh(token, reportId) }
                retryGeneration = false
            }
            while (true) {
                val fetched = withContext(Dispatchers.IO) { api.get(token, reportId) }
                report = fetched
                elapsedMs = SystemClock.elapsedRealtime() - started
                if (fetched.status !in listOf("queued", "generating")) break
                delay(if (elapsedMs >= 45_000 || fetched.error.isNotBlank()) 15_000 else 4_000)
            }
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            error = readableNetworkError(e, "周报暂时无法加载")
        }
    }
    val presentation = weeklyReportPresentation(report, elapsedMs)
    Column(Modifier.fillMaxWidth()) {
        if (presentation == WeeklyReportPresentation.Ready) {
            if (error.isNotEmpty()) {
                Text("更新暂时无法读取，已保存的周报仍可阅读。", color = MaterialTheme.colorScheme.onSurfaceVariant)
                TextButton(onClick = { reload++ }) { Text("重试加载") }
            } else if (report!!.error.isNotBlank()) {
                Text(report!!.error, color = MaterialTheme.colorScheme.onSurfaceVariant)
                TextButton(onClick = { retryGeneration = true; reload++ }) { Text("重试更新") }
            }
            ReportWebView(report!!.html, Modifier.fillMaxWidth().height(heightDp.dp)) { measured ->
                val next = (measured + 2f).coerceIn(96f, 30_000f)
                if (kotlin.math.abs(next - heightDp) > 1f) heightDp = next
            }
        } else if (error.isNotEmpty()) {
            Text(error, color = MaterialTheme.colorScheme.onSurfaceVariant)
            TextButton(onClick = { reload++ }) { Text("重试加载") }
        } else if (presentation == WeeklyReportPresentation.Failed) {
            Text(report!!.error.ifBlank { "周报暂未生成，请同步健康记录后重试" }, color = MaterialTheme.colorScheme.onSurfaceVariant)
            TextButton(onClick = { retryGeneration = true; reload++ }) { Text("重试生成") }
        } else if (presentation == WeeklyReportPresentation.Background) {
            Text("周报仍在后台整理，你可以继续聊天。", color = MaterialTheme.colorScheme.onSurface)
            Text("生成完成后会在这里显示，无需一直等待。", color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            Text(if (report == null) "正在读取周报…" else "Auri 正在整理这周的健康周报", color = MaterialTheme.colorScheme.onSurface)
            Spacer(Modifier.height(12.dp))
            CircularProgressIndicator()
            report?.error?.takeIf { it.isNotBlank() }?.let {
                Text(it, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
