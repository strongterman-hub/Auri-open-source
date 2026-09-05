package com.auri.chat

import android.os.VibrationEffect
import android.os.Vibrator
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.delay
import java.text.SimpleDateFormat
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.Date
import java.util.Locale
import kotlin.math.roundToInt

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HealthScreen(
    onBack: () -> Unit,
    viewModel: HealthViewModel = viewModel(),
) {
    val state by viewModel.state.collectAsState()
    var detailType by rememberSaveable { mutableStateOf<MetricType?>(null) }
    var showManagement by rememberSaveable { mutableStateOf(false) }
    var showWorkouts by rememberSaveable { mutableStateOf(false) }
    var showAbnormal by rememberSaveable { mutableStateOf(false) }
    var showDatePicker by rememberSaveable { mutableStateOf(false) }
    var showXiaomiConnect by rememberSaveable { mutableStateOf(false) }
    var detailRangeDays by rememberSaveable { mutableStateOf(1) }
    var detailEndDay by rememberSaveable { mutableStateOf<String?>(todayInHealthZone()) }
    val overviewScrollState = rememberScrollState()

    LaunchedEffect(showWorkouts) {
        if (showWorkouts) {
            viewModel.refreshWorkouts()
        }
    }

    if (showXiaomiConnect) {
        XiaomiConnectScreen(
            onBack = { showXiaomiConnect = false },
            onConnected = {
                showXiaomiConnect = false
                viewModel.refreshXiaomiStatus()
                viewModel.pullRemote()
            },
        )
        return
    }

    BackHandler {
        when {
            showManagement -> showManagement = false
            showWorkouts -> showWorkouts = false
            showAbnormal -> showAbnormal = false
            detailType != null -> detailType = null
            else -> onBack()
        }
    }

    AuriBackground {
        Scaffold(
            modifier = Modifier.fillMaxSize(),
            containerColor = Color.Transparent,
            topBar = {
                TopAppBar(
                    title = {
                        Text(
                            when {
                                showManagement -> "数据管理"
                                showWorkouts -> "运动记录"
                                showAbnormal -> "异常心跳"
                                detailType != null -> "${detailType!!.label}详情"
                                else -> "健康"
                            },
                        )
                    },
                    navigationIcon = {
                        TextButton(
                            onClick = {
                                when {
                                    showManagement -> showManagement = false
                                    showWorkouts -> showWorkouts = false
                                    showAbnormal -> showAbnormal = false
                                    detailType != null -> detailType = null
                                    else -> onBack()
                                }
                            },
                        ) {
                            Icon(
                                imageVector = Icons.AutoMirrored.Outlined.ArrowBack,
                                contentDescription = "返回",
                                tint = MaterialTheme.colorScheme.onSurface,
                            )
                        }
                    },
                    actions = {
                        if (!showManagement && !showWorkouts && !showAbnormal && detailType == null) {
                            TextButton(onClick = { showManagement = true }) {
                                Icon(
                                    imageVector = Icons.Outlined.Settings,
                                    contentDescription = "数据管理",
                                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.62f),
                        titleContentColor = MaterialTheme.colorScheme.onSurface,
                    ),
                )
            },
        ) { innerPadding ->
        when {
            showManagement -> {
                HealthManagementContent(
                    state = state,
                    onOpenXiaomi = { showXiaomiConnect = true },
                    onSyncXiaomi = viewModel::syncXiaomi,
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(innerPadding),
                )
            }

            showWorkouts -> {
                WorkoutListContent(
                    samples = state.workoutSamples,
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(innerPadding)
                        .verticalScroll(rememberScrollState())
                        .padding(16.dp),
                )
            }

            showAbnormal -> {
                AbnormalHeartBeatListContent(
                    samples = state.samples.filter { it.metricType == HealthSampleType.ABNORMAL_HEART_BEAT.name },
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(innerPadding)
                        .verticalScroll(rememberScrollState())
                        .padding(16.dp),
                )
            }

            detailType != null -> {
                MetricDetailContent(
                    type = detailType!!,
                    viewModel = viewModel,
                    rangeDays = detailRangeDays,
                    endDay = detailEndDay,
                    onRangeChange = { days ->
                        detailRangeDays = days
                        if (days == 1) detailEndDay = todayInHealthZone()
                    },
                    onOpenDatePicker = { showDatePicker = true },
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(innerPadding)
                        .verticalScroll(rememberScrollState())
                        .padding(16.dp),
                )
            }

            else -> {
                HealthOverviewContent(
                    state = state,
                    onSync = viewModel::syncXiaomi,
                    onMetricClick = { type ->
                        detailType = type
                        detailRangeDays = 1
                        detailEndDay = todayInHealthZone()
                    },
                    onWorkoutClick = { showWorkouts = true },
                    onAbnormalClick = { showAbnormal = true },
                    scrollState = overviewScrollState,
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(innerPadding),
                )
            }
        }
    }

    if (showDatePicker) {
        val datePickerState = rememberDatePickerState(
            initialSelectedDateMillis = detailEndDay?.let {
                runCatching {
                    LocalDate.parse(it).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli()
                }.getOrNull()
            },
        )
        DatePickerDialog(
            onDismissRequest = { showDatePicker = false },
            confirmButton = {
                TextButton(
                    onClick = {
                        datePickerState.selectedDateMillis?.let { millis ->
                            detailEndDay = Instant.ofEpochMilli(millis)
                                .atZone(ZoneOffset.UTC)
                                .toLocalDate()
                                .toString()
                        }
                        showDatePicker = false
                    },
                ) {
                    Text("确定")
                }
            },
            dismissButton = {
                TextButton(onClick = { showDatePicker = false }) {
                    Text("取消")
                }
            },
        ) {
            DatePicker(state = datePickerState)
        }
    }
    }
}

@Composable
private fun HealthOverviewContent(
    state: HealthUiState,
    onSync: () -> Unit,
    onMetricClick: (MetricType) -> Unit,
    onWorkoutClick: () -> Unit,
    onAbnormalClick: () -> Unit,
    scrollState: androidx.compose.foundation.ScrollState,
    modifier: Modifier = Modifier,
) {
    var pullDistance by remember { mutableStateOf(0f) }
    val currentState by rememberUpdatedState(state)
    val currentOnSync by rememberUpdatedState(onSync)

    Box(
        modifier = modifier.fillMaxSize(),
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(scrollState)
                .pointerInput(Unit) {
                    awaitEachGesture {
                        val down = awaitFirstDown(requireUnconsumed = false)
                        var total = 0f
                        var pulling = false
                        while (true) {
                            val event = awaitPointerEvent()
                            val change = event.changes.firstOrNull { it.id == down.id } ?: break
                            if (!change.pressed) break
                            val dy = change.position.y - change.previousPosition.y
                            if (!pulling && scrollState.value == 0 && dy > 0f && !currentState.isLoading) {
                                pulling = true
                            }
                            if (pulling) {
                                total += dy
                                pullDistance = total.coerceIn(0f, 180f)
                                change.consume()
                                if (pullDistance == 0f && dy < 0f) {
                                    pulling = false
                                    total = 0f
                                }
                            }
                        }
                        if (pullDistance > 80f && !currentState.isLoading) {
                            currentOnSync()
                        }
                        pullDistance = 0f
                    }
                }
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            state.error?.let {
                Text(
                    text = it,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.fillMaxWidth(),
                )
            }

            if (state.isLoading) {
                LoadingDotsText()
            }

            state.xiaomiLastSyncAt?.let { ts ->
                Text(
                    "最近同步：${formatTime(ts)}",
                    color = AuriTokens.Muted,
                    style = MaterialTheme.typography.bodySmall,
                )
            }

            val recoveryMetrics = if (state.dualSleepScoreVisible) {
                state.sleepScores.mapNotNull { it.toRecoveryMetric() }
            } else {
                emptyList()
            }
            val totallyEmpty = !state.isLoading &&
                state.metrics.isEmpty() && state.samples.isEmpty() && recoveryMetrics.isEmpty()
            if (totallyEmpty) {
                Text(
                    "暂无健康数据。下拉同步，或前往数据管理连接小米健康。",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else {
            MetricType.entries.groupBy { it.category }.forEach { (category, types) ->
                Text(
                    category,
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                val hasData = types.any { type ->
                    if (type == MetricType.RECOVERY_SCORE) {
                        recoveryMetrics.isNotEmpty()
                    } else {
                        state.metrics.any { it.metricType == type }
                    }
                }
                if (!hasData) {
                    Text(
                        "暂无数据，同步后可见",
                        color = AuriTokens.Muted,
                        style = MaterialTheme.typography.bodySmall,
                    )
                } else {
                    types.forEach { type ->
                        val metrics = if (type == MetricType.RECOVERY_SCORE) {
                            recoveryMetrics
                        } else {
                            state.metrics.filter { it.metricType == type }
                        }
                        if (metrics.isNotEmpty()) {
                            MetricCard(
                                type = type,
                                metrics = metrics,
                                samples = state.samples,
                                sleepScore = if (type == MetricType.SLEEP && state.dualSleepScoreVisible) {
                                    state.sleepScores.maxByOrNull { it.sleepDay }
                                } else {
                                    null
                                },
                                onClick = { onMetricClick(type) },
                            )
                        }
                    }
                }
            }
            }

            val workoutSamples = state.workoutSamples
            if (workoutSamples.isNotEmpty()) {
                SummaryCard(
                    title = "运动记录",
                    subtitle = "共 ${workoutSamples.size} 条运动会话",
                    onClick = onWorkoutClick,
                )
            }

            val abnormalSamples = state.samples.filter { it.metricType == HealthSampleType.ABNORMAL_HEART_BEAT.name }
            if (abnormalSamples.isNotEmpty()) {
                SummaryCard(
                    title = "异常心跳",
                    subtitle = "共 ${abnormalSamples.size} 条事件",
                    onClick = onAbnormalClick,
                )
            }
        }

        AnimatedVisibility(
            visible = pullDistance > 0f,
            modifier = Modifier.align(Alignment.TopCenter),
            enter = slideInVertically(
                animationSpec = tween(durationMillis = 2000),
            ) { -it },
            exit = slideOutVertically(
                animationSpec = tween(durationMillis = 2000),
            ) { -it },
        ) {
            Surface(
                shape = RoundedCornerShape(20.dp),
                color = MaterialTheme.colorScheme.surface,
                tonalElevation = 4.dp,
            ) {
                Text(
                    if (pullDistance > 80f) "松开刷新" else "下拉刷新",
                    color = MaterialTheme.colorScheme.primary,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                )
            }
        }
    }
}

@Composable
private fun HealthManagementContent(
    state: HealthUiState,
    onOpenXiaomi: () -> Unit,
    onSyncXiaomi: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        XiaomiConnectionCard(
            bound = state.xiaomiBound,
            lastSyncAt = state.xiaomiLastSyncAt,
            isSyncing = state.isXiaomiSyncing,
            onConnect = onOpenXiaomi,
            onSync = onSyncXiaomi,
        )

        state.error?.let {
            Text(
                text = it,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.fillMaxWidth(),
            )
        }

    }
}

@Composable
private fun XiaomiConnectionCard(
    bound: Boolean,
    lastSyncAt: Long?,
    isSyncing: Boolean,
    onConnect: () -> Unit,
    onSync: () -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                "小米健康",
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurface,
            )
            if (bound) {
                Text(
                    buildString {
                        append("已连接小米健康")
                        lastSyncAt?.let { append("，最近同步：${formatTime(it)}") }
                    },
                    color = MaterialTheme.colorScheme.secondary,
                )
                Button(onClick = onSync, enabled = !isSyncing) {
                    Text(if (isSyncing) "同步中…" else "重新同步")
                }
                OutlinedButton(onClick = onConnect) {
                    Text("重新登录")
                }
            } else {
                Text(
                    "连接小米账号后，可直连小米健康云拉取手环数据。",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Button(onClick = onConnect) {
                    Text("连接小米健康")
                }
            }
        }
    }
}

@Composable
private fun MetricCard(
    type: MetricType,
    metrics: List<DailyMetric>,
    samples: List<HealthSampleEntity>,
    sleepScore: SleepScoreEntity? = null,
    onClick: () -> Unit,
) {
    val (value, timestamp) = metricSummary(type, metrics, samples, sleepScore)
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onClick() },
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(
                type.label,
                style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                value,
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurface,
            )
            if (timestamp != null) {
                Text(
                    timestamp,
                    color = AuriTokens.Muted,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
}

@Composable
private fun SummaryCard(
    title: String,
    subtitle: String,
    onClick: () -> Unit,
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onClick() },
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(
                title,
                style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                subtitle,
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}

@Composable
private fun WorkoutListContent(
    samples: List<HealthSampleEntity>,
    modifier: Modifier = Modifier,
) {
    val sorted = samples.sortedByDescending { it.bucketStart }
    Column(
        modifier = modifier,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (sorted.isEmpty()) {
            Text(
                "暂无运动记录。",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            return@Column
        }
        sorted.forEach { sample ->
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Text(
                        "${workoutLabel(sample.value4)} · ${formatSampleDateTime(sample.bucketStart)}",
                        style = MaterialTheme.typography.titleSmall,
                        color = MaterialTheme.colorScheme.onSurface,
                    )
                    val minutes = sample.value1?.roundToInt() ?: 0
                    val calories = sample.value2?.roundToInt()
                    val avgHr = sample.value3?.roundToInt()
                    Text(
                        buildString {
                            append("${minutes / 60} 小时 ${minutes % 60} 分钟")
                            if (calories != null) append("，${calories} 千卡")
                            if (avgHr != null) append("，平均心率 ${avgHr} 次/分")
                        },
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun AbnormalHeartBeatListContent(
    samples: List<HealthSampleEntity>,
    modifier: Modifier = Modifier,
) {
    val sorted = samples.sortedByDescending { it.bucketStart }
    Column(
        modifier = modifier,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        if (sorted.isEmpty()) {
            Text(
                "暂无异常心跳记录。",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            return@Column
        }
        sorted.forEach { sample ->
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(14.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Text(
                        "异常心跳",
                        style = MaterialTheme.typography.titleSmall,
                        color = MaterialTheme.colorScheme.onSurface,
                    )
                    Text(
                        "${formatSampleDateTime(sample.bucketStart)} ～ ${formatSampleDateTime(sample.bucketEnd)}",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        "持续 ${sample.value1?.roundToInt() ?: 0} 秒 · 如有不适请及时就医",
                        color = AuriTokens.Muted,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
        }
    }
}

@Composable
private fun LoadingDotsText() {
    var dotCount by remember { mutableStateOf(0) }
    LaunchedEffect(Unit) {
        while (true) {
            dotCount = (dotCount + 1) % 4
            delay(300)
        }
    }
    Text(
        "正在同步" + ".".repeat(dotCount),
        color = AuriTokens.Muted,
        style = MaterialTheme.typography.bodySmall,
        modifier = Modifier.fillMaxWidth(),
        textAlign = TextAlign.Center,
    )
}

@Composable
private fun MetricDetailContent(
    type: MetricType,
    viewModel: HealthViewModel,
    rangeDays: Int,
    endDay: String?,
    onRangeChange: (Int) -> Unit,
    onOpenDatePicker: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var detailData by remember { mutableStateOf(MetricDetailData(emptyList(), emptyList())) }
    var loading by remember { mutableStateOf(true) }

    LaunchedEffect(type, rangeDays, endDay) {
        val cached = viewModel.cachedDetail(type, rangeDays, endDay)
        if (cached.metrics.isNotEmpty() || cached.samples.isNotEmpty()) {
            detailData = cached
            loading = false
        }
        viewModel.refreshDetail(type, rangeDays, endDay) { fresh ->
            detailData = fresh
            loading = false
        }
    }

    val isYear = rangeDays == 365
    val metrics = detailData.metrics
    val samples = detailData.samples
    val chartMetrics = if (isYear) aggregateByMonth(type, metrics) else metrics.sortedBy { it.day }

    Column(
        modifier = modifier,
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(
                selected = rangeDays == 1,
                onClick = { onRangeChange(1) },
                label = { Text("日") },
            )
            FilterChip(
                selected = rangeDays == 7,
                onClick = { onRangeChange(7) },
                label = { Text("周") },
            )
            FilterChip(
                selected = rangeDays == 30,
                onClick = { onRangeChange(30) },
                label = { Text("月") },
            )
            FilterChip(
                selected = rangeDays == 365,
                onClick = { onRangeChange(365) },
                label = { Text("年") },
            )
        }

        TextButton(onClick = onOpenDatePicker) {
            Text(
                text = rangeLabel(rangeDays, endDay),
                color = MaterialTheme.colorScheme.primary,
            )
        }

        if (loading) {
            LoadingDotsText()
        } else {
            if (rangeDays != 1) {
                TrendChart(type = type, metrics = chartMetrics, rangeDays = rangeDays, monthLabels = isYear)
            }

            if (rangeDays == 1 && samples.isNotEmpty()) {
                IntradayChart(type = type, samples = samples)
            }

            if (type == MetricType.SLEEP && rangeDays == 1) {
                SleepBreakdownCard(
                    metrics = metrics,
                    samples = samples,
                    sleepScore = if (detailData.dualSleepScoreVisible) {
                        detailData.sleepScores.maxByOrNull { it.sleepDay }
                    } else {
                        null
                    },
                )
            }

            Text(
                if (isYear) "每月详情" else "每日详情",
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurface,
            )

            chartMetrics.sortedByDescending { it.day }.forEach { metric ->
                MetricDetailRow(
                    type = type,
                    metric = metric,
                    monthLabel = isYear,
                    sleepScore = if (type == MetricType.SLEEP && !isYear && detailData.dualSleepScoreVisible) {
                        detailData.sleepScores.firstOrNull { it.sleepDay == metric.day }
                    } else {
                        null
                    },
                )
            }
        }
    }
}

@Composable
private fun SleepBreakdownCard(
    metrics: List<DailyMetric>,
    samples: List<HealthSampleEntity>,
    sleepScore: SleepScoreEntity?,
) {
    val day = metrics.maxByOrNull { it.day }?.day ?: return
    val sessions = samples
        .filter { it.metricType == HealthSampleType.SLEEP_SESSION.name && it.day == day }
        .sortedBy { it.bucketStart }
    if (sessions.isEmpty()) return

    val main = sessions.filter { it.value4 == "main" }
    val naps = sessions.filter { it.value4 == "nap" }
    val mainMinutes = main.sumOf { (it.value1 ?: 0.0).roundToInt() }
    val napMinutes = naps.sumOf { (it.value1 ?: 0.0).roundToInt() }
    val totalMinutes = sessions.sumOf { (it.value1 ?: 0.0).roundToInt() }
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                "睡眠分段",
                style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                buildString {
                    append("${formatDay(day)} 共睡 ${formatDuration(totalMinutes)}")
                    sleepScore
                        ?.takeIf { it.sleepDay == day }
                        ?.sleepHealthScore
                        ?.let { append(" · $it 分") }
                },
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurface,
            )
            Text(
                buildString {
                    append("主睡眠 ${formatDuration(mainMinutes)}")
                    if (napMinutes > 0) append(" · 午睡 ${formatDuration(napMinutes)}")
                },
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private fun formatDuration(minutes: Int): String =
    "${minutes / 60} 小时 ${minutes % 60} 分钟"

@Composable
private fun TrendChart(
    type: MetricType,
    metrics: List<DailyMetric>,
    rangeDays: Int,
    monthLabels: Boolean = false,
) {
    val values = metrics.mapNotNull { it.value1 }
    if (values.size < 2) {
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(20.dp),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
        ) {
            Text(
                "当前数据不足，暂无法生成趋势图。",
                modifier = Modifier.padding(16.dp),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        return
    }

    val minValue = values.minOrNull() ?: 0.0
    val maxValue = values.maxOrNull() ?: 0.0
    val span = (maxValue - minValue).takeIf { it > 0.0 } ?: 1.0
    val gridColor = MaterialTheme.colorScheme.outline
    val lineColor = MaterialTheme.colorScheme.primary
    val pointColor = MaterialTheme.colorScheme.secondary

    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                "${type.label}趋势",
                style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                "最高 ${formatNumber(maxValue)} · 最低 ${formatNumber(minValue)}",
                style = MaterialTheme.typography.bodySmall,
                color = AuriTokens.Muted,
            )
            Canvas(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(180.dp),
            ) {
                val left = 12.dp.toPx()
                val right = size.width - 12.dp.toPx()
                val top = 12.dp.toPx()
                val bottom = size.height - 12.dp.toPx()

                repeat(4) { index ->
                    val y = top + (bottom - top) * index / 3f
                    drawLine(
                        color = gridColor,
                        start = Offset(left, y),
                        end = Offset(right, y),
                        strokeWidth = 1.dp.toPx(),
                    )
                }

                val points = values.mapIndexed { index, value ->
                    val x = if (values.size == 1) {
                        left
                    } else {
                        left + (right - left) * index / (values.size - 1)
                    }
                    val y = bottom - ((value - minValue) / span).toFloat() * (bottom - top)
                    Offset(x, y)
                }

                val path = Path().apply {
                    moveTo(points.first().x, points.first().y)
                    points.drop(1).forEach { point ->
                        lineTo(point.x, point.y)
                    }
                }
                drawPath(
                    path = path,
                    color = lineColor,
                    style = Stroke(
                        width = 3.dp.toPx(),
                        cap = StrokeCap.Round,
                    ),
                )
                points.forEach { point ->
                    drawCircle(
                        color = pointColor,
                        radius = 4.dp.toPx(),
                        center = point,
                    )
                }
            }

            val tickCount = minOf(metrics.size, 6)
            val step = (metrics.size - 1).toFloat() / (tickCount - 1).coerceAtLeast(1)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                for (i in 0 until tickCount) {
                    val index = (i * step).roundToInt().coerceIn(0, metrics.lastIndex)
                    Text(
                        if (monthLabels) formatMonth(metrics[index].day) else formatAxisDay(metrics[index].day, rangeDays),
                        color = AuriTokens.Muted,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
            }
        }
    }
}

@Composable
private fun IntradayChart(
    type: MetricType,
    samples: List<HealthSampleEntity>,
) {
    val sorted = samples.sortedBy { it.bucketStart }
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                "${type.label}日内趋势",
                style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            when (type) {
                MetricType.SLEEP -> SleepStageChart(samples = sorted)
                else -> SampleLineChart(type = type, samples = sorted)
            }
        }
    }
}

@Composable
private fun SampleLineChart(
    type: MetricType,
    samples: List<HealthSampleEntity>,
) {
    val context = LocalContext.current
    val sorted = samples.sortedBy { it.bucketStart }
    val values = sorted.mapNotNull { it.value1 }
    if (values.size < 2) {
        Text(
            "日内采样点不足，暂时无法生成曲线。",
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        return
    }

    val minValue = values.minOrNull() ?: 0.0
    val maxValue = values.maxOrNull() ?: 0.0
    val span = (maxValue - minValue).takeIf { it > 0.0 } ?: 1.0
    val gridColor = MaterialTheme.colorScheme.outline
    val lineColor = when (type) {
        MetricType.HEART_RATE -> AuriTokens.HeartRate
        MetricType.STEPS -> AuriTokens.Steps
        MetricType.CALORIES -> AuriTokens.Calories
        MetricType.SPO2 -> AuriTokens.SpO2
        MetricType.STRESS -> AuriTokens.Stress
        else -> MaterialTheme.colorScheme.primary
    }
    val pointColor = lineColor
    val startMillis = sorted.minOfOrNull { Instant.parse(it.bucketStart).toEpochMilli() }
    val endMillis = sorted.maxOfOrNull { Instant.parse(it.bucketEnd).toEpochMilli() }
    val timeSpan = ((endMillis ?: startMillis ?: 0L) - (startMillis ?: 0L)).coerceAtLeast(1L)

    var selected by remember { mutableStateOf<HealthSampleEntity?>(null) }
    var selectedFrac by remember { mutableStateOf<Float?>(null) }
    var vibratingBpm by remember { mutableStateOf<Int?>(null) }
    var isPointerDown by remember { mutableStateOf(false) }
    var holdStartMillis by remember { mutableStateOf(0L) }
    val selectionColor = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.35f)

    LaunchedEffect(vibratingBpm) {
        val bpm = vibratingBpm ?: return@LaunchedEffect
        val vibrator = context.getSystemService(Vibrator::class.java)
        val interval = (60000f / bpm).toLong().coerceAtLeast(220)
        val pulse = 70L
        val pause = (interval - pulse).coerceAtLeast(0)
        vibrator?.vibrate(VibrationEffect.createWaveform(longArrayOf(0, pulse, pause), 0))
        try {
            awaitCancellation()
        } finally {
            vibrator?.cancel()
        }
    }

    LaunchedEffect(isPointerDown, holdStartMillis) {
        if (!isPointerDown || type != MetricType.HEART_RATE) return@LaunchedEffect
        delay(1500)
        if (isPointerDown) {
            selected?.value1?.roundToInt()?.let { vibratingBpm = it }
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            "最高 ${formatNumber(maxValue)} · 最低 ${formatNumber(minValue)}",
            color = AuriTokens.Muted,
            style = MaterialTheme.typography.bodySmall,
        )
        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .height(180.dp)
                .pointerInput(sorted, startMillis, timeSpan) {
                    val left = 12.dp.toPx()
                    val right = size.width.toFloat() - 12.dp.toPx()
                    fun updateSelection(tapX: Float) {
                        val base = startMillis ?: return
                        val frac = ((tapX - left) / (right - left)).coerceIn(0f, 1f)
                        val target = base + timeSpan * frac.toDouble()
                        selected = sorted.minByOrNull { sample ->
                            kotlin.math.abs(sampleMidMillis(sample) - target)
                        }
                        selectedFrac = frac
                    }
                    awaitEachGesture {
                        val down = awaitFirstDown(requireUnconsumed = false)
                        isPointerDown = true
                        holdStartMillis = System.currentTimeMillis()
                        vibratingBpm = null
                        updateSelection(down.position.x)
                        while (true) {
                            val event = awaitPointerEvent()
                            val change = event.changes.firstOrNull { it.id == down.id }
                            if (change == null || !change.pressed) break
                            if (change.position != change.previousPosition) {
                                updateSelection(change.position.x)
                                holdStartMillis = System.currentTimeMillis()
                                vibratingBpm = null
                            }
                        }
                        isPointerDown = false
                        vibratingBpm = null
                        selectedFrac = null
                        selected = null
                    }
                },
        ) {
            val left = 12.dp.toPx()
            val right = size.width - 12.dp.toPx()
            val top = 12.dp.toPx()
            val bottom = size.height - 12.dp.toPx()

            repeat(4) { index ->
                val y = top + (bottom - top) * index / 3f
                drawLine(
                    color = gridColor,
                    start = Offset(left, y),
                    end = Offset(right, y),
                    strokeWidth = 1.dp.toPx(),
                )
            }

            val base = startMillis ?: return@Canvas
            val points = sorted.mapNotNull { sample ->
                val value = sample.value1 ?: return@mapNotNull null
                val x = left + (right - left) * (((sampleMidMillis(sample) - base) / timeSpan.toDouble()).toFloat()).coerceIn(0f, 1f)
                val y = bottom - ((value - minValue) / span).toFloat() * (bottom - top)
                Offset(x, y)
            }

            if (points.size > 1) {
                val path = Path().apply {
                    moveTo(points.first().x, points.first().y)
                    points.drop(1).forEach { point -> lineTo(point.x, point.y) }
                }
                drawPath(
                    path = path,
                    color = lineColor,
                    style = Stroke(width = 3.dp.toPx(), cap = StrokeCap.Round),
                )
            }
            points.forEach { point ->
                drawCircle(color = pointColor, radius = 4.dp.toPx(), center = point)
            }

            selectedFrac?.let { frac ->
                val x = left + (right - left) * frac
                drawLine(
                    color = selectionColor,
                    start = Offset(x, top),
                    end = Offset(x, bottom),
                    strokeWidth = 1.dp.toPx(),
                )
            }
        }

        selected?.let { s ->
            val value = when (type) {
                MetricType.HEART_RATE -> "${s.value1?.roundToInt() ?: 0} 次/分"
                MetricType.STRESS -> "${s.value1?.roundToInt() ?: 0} 分"
                MetricType.SPO2 -> "${s.value1?.roundToInt() ?: 0} %"
                else -> formatNumber(s.value1 ?: 0.0)
            }
            Text(
                "${formatSampleDateTime(s.bucketStart)} · $value",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodySmall,
            )
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(
                formatBucket(sorted.first().bucketStart),
                color = AuriTokens.Muted,
                style = MaterialTheme.typography.bodySmall,
            )
            Text(
                formatBucket(sorted.last().bucketStart),
                color = AuriTokens.Muted,
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun SleepStageChart(
    samples: List<HealthSampleEntity>,
) {
    val stageColors = mapOf(
        1.0 to AuriTokens.SleepDeep,
        2.0 to AuriTokens.SleepLight,
        3.0 to AuriTokens.SleepRem,
        4.0 to AuriTokens.SleepAwake,
    )
    val sorted = samples
        .filter { it.metricType == HealthSampleType.SLEEP_STAGE.name }
        .sortedBy { it.bucketStart }
    if (sorted.isEmpty()) {
        Text(
            "暂无睡眠分期数据。",
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        return
    }
    val startMillis = sorted.minOfOrNull { Instant.parse(it.bucketStart).toEpochMilli() }
    val endMillis = sorted.maxOfOrNull { Instant.parse(it.bucketEnd).toEpochMilli() }
    val spanMillis = ((endMillis ?: startMillis ?: 0L) - (startMillis ?: 0L)).coerceAtLeast(1L)

    var selected by remember { mutableStateOf<HealthSampleEntity?>(null) }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .height(140.dp)
                .pointerInput(sorted, startMillis, spanMillis) {
                    fun select(tapX: Float) {
                        val base = startMillis ?: return
                        val target = base + spanMillis * (tapX / size.width.toFloat())
                        selected = sorted.firstOrNull { sample ->
                            Instant.parse(sample.bucketStart).toEpochMilli() <= target &&
                                Instant.parse(sample.bucketEnd).toEpochMilli() >= target
                        }
                    }
                    awaitEachGesture {
                        val down = awaitFirstDown(requireUnconsumed = false)
                        select(down.position.x)
                        while (true) {
                            val event = awaitPointerEvent()
                            val change = event.changes.firstOrNull { it.id == down.id } ?: break
                            if (!change.pressed) break
                            if (change.position != change.previousPosition) {
                                select(change.position.x)
                            }
                        }
                    }
                },
        ) {
            val start = startMillis ?: return@Canvas
            val end = endMillis ?: return@Canvas
            if (end <= start) return@Canvas
            val left = 0f
            val right = size.width
            val top = 6.dp.toPx()
            val bottom = size.height - 6.dp.toPx()
            val levelY = mapOf(
                4.0 to top,
                3.0 to top + (bottom - top) * 0.28f,
                2.0 to top + (bottom - top) * 0.60f,
                1.0 to top + (bottom - top) * 0.90f,
            )

            var prevX: Float? = null
            var prevY: Float? = null
            sorted.forEach { sample ->
                val s = Instant.parse(sample.bucketStart).toEpochMilli()
                val e = Instant.parse(sample.bucketEnd).toEpochMilli()
                val x1 = left + (right - left) * ((s - start).toFloat() / (end - start).toFloat())
                val x2 = left + (right - left) * ((e - start).toFloat() / (end - start).toFloat())
                val color = stageColors[sample.value1] ?: Color.Gray
                val y = levelY[sample.value1] ?: (top + (bottom - top) * 0.5f)
                if (prevX != null && prevY != null) {
                    drawLine(
                        color = color,
                        start = Offset(prevX!!, prevY!!),
                        end = Offset(x1, y),
                        strokeWidth = 2.dp.toPx(),
                    )
                }
                drawLine(
                    color = color,
                    start = Offset(x1, y),
                    end = Offset(x2, y),
                    strokeWidth = 4.dp.toPx(),
                )
                prevX = x2
                prevY = y
            }
        }

        selected?.let { s ->
            Text(
                "${formatBucket(s.bucketStart)} - ${formatBucket(s.bucketEnd)} · ${sleepStageLabel(s.value1)}",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                style = MaterialTheme.typography.bodySmall,
            )
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            LegendItem(AuriTokens.SleepDeep, "深睡")
            LegendItem(AuriTokens.SleepLight, "浅睡")
            LegendItem(AuriTokens.SleepRem, "快速眼动")
            LegendItem(AuriTokens.SleepAwake, "清醒")
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(
                formatBucket(sorted.first().bucketStart),
                color = AuriTokens.Muted,
                style = MaterialTheme.typography.bodySmall,
            )
            Text(
                formatBucket(sorted.last().bucketEnd),
                color = AuriTokens.Muted,
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun LegendItem(color: Color, label: String) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Box(
            modifier = Modifier
                .size(8.dp)
                .background(color, RoundedCornerShape(2.dp)),
        )
        Text(label, color = AuriTokens.Muted, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun MetricDetailRow(
    type: MetricType,
    metric: DailyMetric,
    monthLabel: Boolean = false,
    sleepScore: SleepScoreEntity? = null,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                if (monthLabel) formatMonth(metric.day) else formatDay(metric.day),
                color = AuriTokens.Muted,
            )
            Text(
                metricValueText(type, metric, sleepScore),
                color = MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}

private fun metricValueText(
    type: MetricType,
    metric: DailyMetric,
    sleepScore: SleepScoreEntity? = null,
): String {
    return when (type) {
        MetricType.STEPS -> "${metric.value1?.roundToInt() ?: 0} 步"
        MetricType.HEART_RATE -> {
            val average = metric.value1?.roundToInt()
            val min = metric.value2?.roundToInt()
            val max = metric.value3?.roundToInt()
            buildString {
                if (average != null) append("平均 $average")
                if (min != null && max != null) append("，$min - $max")
                append(" 次/分")
            }
        }

        MetricType.RESTING_HEART_RATE -> "${metric.value1?.roundToInt() ?: 0} 次/分"

        MetricType.SPO2 -> {
            val average = metric.value1?.roundToInt()
            val min = metric.value2?.roundToInt()
            val max = metric.value3?.roundToInt()
            buildString {
                if (average != null) append("平均 $average")
                if (min != null && max != null) append("，$min - $max")
                append(" %")
            }
        }

        MetricType.STRESS -> {
            val average = metric.value1?.roundToInt()
            val min = metric.value2?.roundToInt()
            val max = metric.value3?.roundToInt()
            buildString {
                if (average != null) append("平均 $average")
                if (min != null && max != null) append("，$min - $max")
                append(" 分")
            }
        }

        MetricType.SLEEP -> {
            val minutes = metric.value1?.roundToInt() ?: 0
            buildString {
                append("${minutes / 60} 小时 ${minutes % 60} 分钟")
                sleepScore
                    ?.takeIf { it.sleepDay == metric.day }
                    ?.sleepHealthScore
                    ?.let { append(" · $it 分") }
            }
        }

        MetricType.RECOVERY_SCORE -> "${metric.value1?.roundToInt() ?: 0} 分"

        MetricType.DISTANCE -> "${formatNumber((metric.value1 ?: 0.0) / 1000.0)} 公里"
        MetricType.CALORIES -> "${metric.value1?.roundToInt() ?: 0} 千卡"
        MetricType.WEIGHT -> "${formatNumber(metric.value1 ?: 0.0)} 公斤"
        MetricType.BODY_FAT -> "${formatNumber(metric.value1 ?: 0.0)} %"
        MetricType.BMI -> "${formatNumber(metric.value1 ?: 0.0)}"
        MetricType.MUSCLE_MASS -> "${formatNumber(metric.value1 ?: 0.0)} 公斤"
        MetricType.BODY_WATER -> "${formatNumber(metric.value1 ?: 0.0)} %"
        MetricType.BONE_MASS -> "${formatNumber(metric.value1 ?: 0.0)} 公斤"
        MetricType.VISCERAL_FAT -> "${metric.value1?.roundToInt() ?: 0} 级"
        MetricType.BMR -> "${metric.value1?.roundToInt() ?: 0} 千卡"
    }
}

private fun formatDay(day: String): String = day.takeLast(5).removePrefix("-")

private fun formatAxisDay(day: String, rangeDays: Int): String =
    if (rangeDays <= 31) formatDay(day) else day.take(7)

private fun formatMonth(month: String): String {
    val parts = month.take(7).split("-")
    return if (parts.size == 2) "${parts[1].toIntOrNull() ?: parts[1]}月" else month.take(7)
}

private fun aggregateByMonth(type: MetricType, metrics: List<DailyMetric>): List<DailyMetric> =
    metrics.groupBy { it.day.take(7) }
        .map { (month, list) ->
            val value1 = when (type) {
                MetricType.STEPS,
                MetricType.DISTANCE,
                MetricType.CALORIES,
                MetricType.SLEEP -> list.mapNotNull { it.value1 }.sum()

                MetricType.WEIGHT,
                MetricType.BODY_FAT,
                MetricType.BMI,
                MetricType.MUSCLE_MASS,
                MetricType.BODY_WATER,
                MetricType.BONE_MASS,
                MetricType.VISCERAL_FAT,
                MetricType.BMR -> list.maxByOrNull { it.day }?.value1

                else -> list.mapNotNull { it.value1 }.average()
            }
            DailyMetric(type, month, value1, null, null)
        }
        .sortedBy { it.day }

private fun metricSummary(
    type: MetricType,
    metrics: List<DailyMetric>,
    samples: List<HealthSampleEntity>,
    sleepScore: SleepScoreEntity? = null,
): Pair<String, String?> {
    val sampleType = when (type) {
        MetricType.HEART_RATE -> "HEART_RATE"
        MetricType.STRESS -> "STRESS"
        MetricType.SPO2 -> "SPO2"
        else -> null
    }
    if (sampleType != null) {
        val latestSample = samples.filter { it.metricType == sampleType }.maxByOrNull { it.bucketStart }
        if (latestSample != null) {
            val value = when (type) {
                MetricType.HEART_RATE -> "${latestSample.value1?.roundToInt() ?: 0} 次/分"
                MetricType.STRESS -> "${latestSample.value1?.roundToInt() ?: 0} 分"
                MetricType.SPO2 -> "${latestSample.value1?.roundToInt() ?: 0} %"
                else -> ""
            }
            return value to formatSampleDateTime(latestSample.bucketStart)
        }
    }

    val latest = metrics.maxByOrNull { it.day } ?: return "暂无数据" to null
    val value = metricValueText(type, latest, sleepScore)
    val timestamp = when (type) {
        MetricType.STEPS, MetricType.DISTANCE, MetricType.CALORIES ->
            activityMetricContext(latest)
        else -> formatDay(latest.day)
    }
    return value to timestamp
}

private fun activityMetricContext(metric: DailyMetric): String {
    val source = when (metric.source) {
        "xiaomi_aggregate" -> "小米综合"
        "xiaomi_device" -> "手环数据"
        else -> null
    }
    val freshness = if (metric.day == todayInHealthZone()) {
        val timestamp = metric.sourceUpdatedAt.takeIf { it > 0L }
            ?: metric.updatedAt.takeIf { it > 0L }
        timestamp?.let {
            "截至 ${SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(it))}"
        } ?: "今日"
    } else {
        formatDay(metric.day)
    }
    return listOfNotNull(source, freshness).joinToString(" · ")
}

private fun formatNumber(value: Double): String =
    if (value == value.toLong().toDouble()) {
        value.toLong().toString()
    } else {
        String.format(Locale.US, "%.1f", value)
    }

private fun formatBucket(bucketStart: String): String =
    runCatching {
        SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date.from(Instant.parse(bucketStart)))
    }.getOrDefault(bucketStart)

private fun formatSampleDateTime(bucketStart: String): String =
    runCatching {
        val zone = ZoneId.systemDefault()
        val dt = Instant.parse(bucketStart).atZone(zone)
        "${dt.monthValue.toString().padStart(2, '0')}-${dt.dayOfMonth.toString().padStart(2, '0')} " +
            "${dt.hour.toString().padStart(2, '0')}:${dt.minute.toString().padStart(2, '0')}"
    }.getOrDefault(bucketStart.take(16))

private fun sampleMidMillis(sample: HealthSampleEntity): Double =
    (Instant.parse(sample.bucketStart).toEpochMilli() +
        Instant.parse(sample.bucketEnd).toEpochMilli()) / 2.0

private fun sleepStageLabel(value: Double?): String = when (value) {
    1.0 -> "深睡"
    2.0 -> "浅睡"
    3.0 -> "快速眼动"
    4.0 -> "清醒"
    else -> "未知"
}

private fun workoutLabel(activityType: String?): String =
    when (activityType?.lowercase()) {
        "walking", "walk" -> "步行"
        "running", "run", "outdoor_running", "indoor_running", "treadmill" -> "跑步"
        "outdoor_riding" -> "户外骑行"
        "indoor_riding" -> "室内骑行"
        "cycling", "cycling_outdoor", "cycling_indoor", "bike", "bicycle" -> "骑行"
        "swimming", "swim" -> "游泳"
        "hiking", "hike", "climbing", "mountaineering" -> "登山"
        "badminton" -> "羽毛球"
        "basketball" -> "篮球"
        "football", "soccer" -> "足球"
        "table_tennis", "pingpong" -> "乒乓球"
        "tennis" -> "网球"
        "jump_rope", "rope_skipping", "skipping" -> "跳绳"
        "yoga" -> "瑜伽"
        "strength", "strength_training", "weight_training" -> "力量训练"
        "elliptical" -> "椭圆机"
        "rowing", "rowing_machine" -> "划船机"
        "dance", "dancing" -> "舞蹈"
        "skating", "roller_skating", "ice_skating" -> "滑冰"
        null, "", "workout" -> "运动"
        else -> "运动"
    }

private fun rangeLabel(rangeDays: Int, endDay: String?): String {
    val end = endDay?.let { LocalDate.parse(it) } ?: LocalDate.now(HealthZone)
    val start = end.minusDays((rangeDays - 1).toLong())
    return when (rangeDays) {
        1 -> "日期：$end"
        else -> "范围：$start ~ $end"
    }
}

private fun formatTime(timestamp: Long): String =
    SimpleDateFormat("MM-dd HH:mm", Locale.getDefault()).format(Date(timestamp))
