package com.auri.chat

import android.graphics.Color as AndroidColor
import android.webkit.WebView
import android.webkit.WebViewClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.Alignment
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.io.ByteArrayInputStream

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HealthReportScreen(initialReportId: String? = null, onBack: () -> Unit) {
    val context = LocalContext.current
    val token = remember { AuthStore(context).getToken().orEmpty() }
    val api = remember { HealthReportApi() }
    var selected by rememberSaveable { mutableStateOf(initialReportId) }
    var reports by remember { mutableStateOf<List<WeeklyReport>>(emptyList()) }
    var report by remember { mutableStateOf<WeeklyReport?>(null) }
    var enabled by remember { mutableStateOf(true) }
    var loading by remember { mutableStateOf(true) }
    var working by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    var reload by remember { mutableIntStateOf(0) }
    var requestKind by remember { mutableStateOf<String?>(null) }
    fun back() {
        if (selected != null && initialReportId == null) { selected = null; report = null } else onBack()
    }
    BackHandler { back() }
    LaunchedEffect(requestKind) {
        val kind = requestKind ?: return@LaunchedEffect
        working = true; error = ""
        try {
            when (kind) {
                "generate" -> selected = withContext(Dispatchers.IO) { api.generate(token) }
                "refresh" -> selected?.let { id -> withContext(Dispatchers.IO) { api.refresh(token, id) } }
                "toggle" -> enabled = withContext(Dispatchers.IO) { api.setEnabled(token, !enabled).getBoolean("enabled") }
            }
            reload++
        } catch (e: kotlinx.coroutines.CancellationException) { throw e }
        catch (e: Exception) { error = readableNetworkError(e, "周报请求失败，请重试") }
        finally { working = false; requestKind = null }
    }
    LaunchedEffect(selected, reload) {
        loading = true; error = ""
        try {
            withContext(Dispatchers.IO) { api.register(token) }
            if (selected == null) {
                reports = withContext(Dispatchers.IO) { api.list(token) }
                enabled = withContext(Dispatchers.IO) { api.settings(token).getBoolean("enabled") }
            } else {
                report = null
                while (true) {
                    val fetched = withContext(Dispatchers.IO) { api.get(token, selected!!) }
                    report = fetched
                    loading = false
                    if (fetched.status !in listOf("queued", "generating")) break
                    delay(4_000)
                }
            }
        } catch (e: kotlinx.coroutines.CancellationException) { throw e }
        catch (e: Exception) { error = readableNetworkError(e, "周报暂时无法加载") }
        finally { loading = false }
    }
    AuriBackground {
        Scaffold(containerColor = androidx.compose.ui.graphics.Color.Transparent, contentColor = MaterialTheme.colorScheme.onSurface, topBar = {
            TopAppBar(title = { Text(if (selected == null) "健康周报" else "这一周的回顾") }, navigationIcon = {
                IconButton(onClick = ::back) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回") }
            }, colors = TopAppBarDefaults.topAppBarColors(containerColor = androidx.compose.ui.graphics.Color.Transparent))
        }) { padding ->
            if (selected == null) {
                LazyColumn(Modifier.fillMaxSize().padding(padding), contentPadding = PaddingValues(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    item {
                        Card {
                            Column(Modifier.padding(20.dp)) {
                                Text("和 Auri 一起回看这一周", style = MaterialTheme.typography.titleLarge)
                                Spacer(Modifier.height(10.dp))
                                Text("从睡眠、活动和你的日常里，发现值得留意的变化。每期内容由 AI 根据已有记录生成。", color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                    Text("每周自动回顾", Modifier.weight(1f))
                                    Switch(checked = enabled, enabled = !working && !loading, onCheckedChange = { requestKind = "toggle" })
                                }
                                Text("自动回顾遵守主动消息开关与休息时间。生成使用 Credits，重复查看不消耗。", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                Spacer(Modifier.height(16.dp))
                                Button(enabled = !working && !loading, onClick = { requestKind = "generate" }) { Text("生成上周回顾") }
                            }
                        }
                    }
                    if (loading) item { CircularProgressIndicator() }
                    if (error.isNotEmpty()) item { ReportError(error) { reload++ } }
                    if (!loading && error.isEmpty() && reports.isEmpty()) item { Text("第一份周报会出现在这里。先同步健康数据，也可以主动生成上周回顾。", color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    items(reports, key = { it.id }) { row ->
                        Card(Modifier.fillMaxWidth().clickable { selected = row.id }) {
                            Column(Modifier.padding(20.dp)) {
                                Text("${row.weekStart} — ${row.weekEnd}", style = MaterialTheme.typography.titleMedium)
                                Spacer(Modifier.height(8.dp))
                                Text(row.summary.ifBlank { if (row.status == "failed") "生成暂未完成，点开重试" else "Auri 正在整理这周的记录…" })
                                Spacer(Modifier.height(8.dp))
                                Text("查看周报 ›", color = MaterialTheme.colorScheme.primary)
                            }
                        }
                    }
                }
            } else if (error.isNotEmpty()) {
                Box(Modifier.fillMaxSize().padding(padding).padding(24.dp), contentAlignment = Alignment.Center) { ReportError(error) { reload++ } }
            } else if (report?.html?.isNotBlank() == true) {
                Column(Modifier.fillMaxSize().padding(padding)) {
                    if (report!!.status == "failed") {
                        Text(report!!.error.ifBlank { "更新暂未完成，仍可阅读已保存的报告" }, Modifier.padding(12.dp), style = MaterialTheme.typography.bodySmall)
                    } else if (report!!.status != "ready") {
                        Text("正在更新这份回顾…", Modifier.padding(12.dp), style = MaterialTheme.typography.bodySmall)
                    }
                    ReportWebView(report!!.html, Modifier.weight(1f).fillMaxWidth())
                    TextButton(enabled = !working && report!!.status in listOf("ready", "failed"), onClick = { requestKind = "refresh" }, modifier = Modifier.align(Alignment.End)) { Text(if (report!!.status == "failed") "重试更新本期报告" else "同步并更新本期报告") }
                }
            } else {
                Column(Modifier.fillMaxSize().padding(padding).padding(28.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
                    if (loading || report?.status in listOf("queued", "generating")) {
                        CircularProgressIndicator(); Spacer(Modifier.height(20.dp))
                        Text("Auri 正在回看这一周", style = MaterialTheme.typography.titleLarge)
                        Text("同步记录、整理变化并绘制图表，完成后会保存到这里。", color = MaterialTheme.colorScheme.onSurfaceVariant)
                        report?.error?.takeIf { it.isNotBlank() }?.let { Text(it, Modifier.padding(top = 12.dp)) }
                    } else {
                        ReportError(report?.error?.ifBlank { "这一周还没有可用健康记录" } ?: "周报暂未完成") { requestKind = "refresh" }
                    }
                }
            }
        }
    }
}

@Composable
private fun ReportError(message: String, retry: () -> Unit) {
    Column {
        Text(message, color = MaterialTheme.colorScheme.onSurfaceVariant)
        TextButton(onClick = retry) { Text("重试") }
    }
}

@Composable
private fun ReportWebView(html: String, modifier: Modifier) {
    val view = remember { mutableStateOf<WebView?>(null) }
    DisposableEffect(Unit) { onDispose { view.value?.apply { stopLoading(); destroy() }; view.value = null } }
    AndroidView(modifier = modifier, factory = { context ->
        WebView(context).apply {
            view.value = this
            setBackgroundColor(AndroidColor.rgb(16, 16, 28))
            settings.javaScriptEnabled = false
            settings.domStorageEnabled = false
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.blockNetworkLoads = true
            settings.setSupportZoom(true)
            settings.builtInZoomControls = true
            settings.displayZoomControls = false
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?) = true
                override fun shouldInterceptRequest(view: WebView?, request: WebResourceRequest?) = WebResourceResponse("text/plain", "UTF-8", ByteArrayInputStream(ByteArray(0)))
            }
        }
    }, update = { web ->
        if (web.tag != html) {
            web.tag = html
            web.loadDataWithBaseURL("https://reports.auri.invalid/", html, "text/html", "UTF-8", null)
        }
    })
}
