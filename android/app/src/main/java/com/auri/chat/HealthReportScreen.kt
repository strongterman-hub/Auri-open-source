package com.auri.chat

import android.os.SystemClock
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun HealthReportScreen(reportId: String, onBack: () -> Unit) {
    BackHandler(onBack = onBack)
    val context = LocalContext.current
    val token = remember { AuthStore(context).getToken().orEmpty() }
    val api = remember { HealthReportApi() }
    var report by remember(reportId) { mutableStateOf<WeeklyReport?>(null) }
    var error by remember(reportId) { mutableStateOf("") }
    var reload by remember(reportId) { mutableStateOf(0) }
    var retryGeneration by remember(reportId) { mutableStateOf(false) }
    var elapsedMs by remember(reportId) { mutableStateOf(0L) }
    LaunchedEffect(reportId, reload) {
        error = ""
        elapsedMs = 0L
        val started = SystemClock.elapsedRealtime()
        try {
            if (retryGeneration) {
                retryGeneration = false
                withContext(Dispatchers.IO) { api.refresh(token, reportId) }
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
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("健康周报")
                        report?.let {
                            Text("${it.weekStart} — ${it.weekEnd}", style = MaterialTheme.typography.labelSmall)
                        }
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "返回聊天")
                    }
                },
            )
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            if (presentation == WeeklyReportPresentation.Ready) {
                if (error.isNotBlank() || report!!.error.isNotBlank()) {
                    Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp)) {
                        Text(error.ifBlank { report!!.error }, style = MaterialTheme.typography.bodySmall)
                        TextButton(onClick = {
                            retryGeneration = error.isBlank()
                            reload++
                        }) { Text(if (error.isBlank()) "重试更新" else "重试加载") }
                    }
                }
                ReportWebView(report!!.html, Modifier.fillMaxWidth().weight(1f))
            } else {
                Column(
                    Modifier.fillMaxSize().padding(24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(16.dp, Alignment.CenterVertically),
                ) {
                    when {
                        error.isNotBlank() -> {
                            Text(error)
                            FilledTonalButton(onClick = { reload++ }) { Text("重试加载") }
                        }
                        presentation == WeeklyReportPresentation.Failed -> {
                            Text(report!!.error.ifBlank { "周报暂未生成，请同步健康记录后重试" })
                            FilledTonalButton(onClick = { retryGeneration = true; reload++ }) { Text("重试生成") }
                        }
                        presentation == WeeklyReportPresentation.Background -> {
                            Text("周报仍在后台整理")
                            Text("可以先返回聊天，稍后从这条消息继续查看。", color = MaterialTheme.colorScheme.onSurfaceVariant)
                            FilledTonalButton(onClick = onBack) { Text("返回聊天") }
                        }
                        else -> {
                            CircularProgressIndicator()
                            Text(if (report == null) "正在读取周报…" else "Auri 正在整理这周的健康周报")
                        }
                    }
                }
            }
        }
    }
}
