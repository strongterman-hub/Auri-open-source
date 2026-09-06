package com.auri.chat

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowRight
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.platform.LocalDensity
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.filter
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.LocalTime
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import kotlin.math.roundToInt

private const val PICKER_DAY_MILLIS = 86_400_000L

internal fun localDateToPickerMillis(date: LocalDate): Long = date.toEpochDay() * PICKER_DAY_MILLIS

internal fun pickerMillisToLocalDate(millis: Long): LocalDate =
    LocalDate.ofEpochDay(Math.floorDiv(millis, PICKER_DAY_MILLIS))

internal fun monthCalendarCells(month: YearMonth): List<LocalDate?> {
    val prefix = month.atDay(1).dayOfWeek.value - 1
    val result = MutableList<LocalDate?>(prefix) { null }
    (1..month.lengthOfMonth()).forEach { result += month.atDay(it) }
    while (result.size % 7 != 0) result += null
    return result
}

internal fun eventCoversDate(event: ScheduleEvent, day: LocalDate): Boolean {
    val start = event.startsAt.toLocalDate()
    val last = if (event.allDay || event.endsAt.toLocalTime() == LocalTime.MIDNIGHT) {
        event.endsAt.toLocalDate().minusDays(1)
    } else event.endsAt.toLocalDate()
    return day in start..last
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ScheduleScreen(onBack: () -> Unit, viewModel: ScheduleViewModel = viewModel()) {
    val state by viewModel.state.collectAsState()
    val lifecycle = LocalLifecycleOwner.current
    val coroutineScope = rememberCoroutineScope()
    var editor by remember { mutableStateOf<EditorState?>(null) }
    var chooseEvent by remember { mutableStateOf<ScheduleEvent?>(null) }
    var chooseAction by remember { mutableStateOf("edit") }
    var confirmText by remember { mutableStateOf<String?>(null) }
    var retry by remember { mutableStateOf<(() -> Unit)?>(null) }
    var message by remember { mutableStateOf<String?>(null) }

    fun handle(result: SaveResult) {
        when (result) {
            SaveResult.Success -> { editor = null; message = null }
            is SaveResult.Failure -> message = result.message
            is SaveResult.ConfirmConflict -> { confirmText = "这个时间与已有日程重叠，仍然保存吗？"; retry = result.retry }
            is SaveResult.ConfirmReset -> { confirmText = "修改整个系列会清除已经单独调整过的日期，继续吗？"; retry = result.retry }
        }
    }

    fun openEditor(event: ScheduleEvent, scope: String) {
        coroutineScope.launch {
            if (scope == "series") {
                viewModel.series(event.id).onSuccess { series ->
                    editor = EditorState(event, scope, ScheduleDraft(
                        series.title, series.startsAt, series.endsAt, series.timezone, series.allDay,
                        series.location, series.notes, series.reminderMinutes, series.repeat, series.repeatUntil, series.status,
                    ))
                }.onFailure { message = it.message ?: "日程详情加载失败" }
            } else {
                editor = EditorState(event, scope, ScheduleDraft(
                    event.title, event.startsAt.toLocalDateTime(), event.endsAt.toLocalDateTime(), event.timezone,
                    event.allDay, event.location, event.notes, event.reminderMinutes, event.repeat, event.repeatUntil, event.status,
                ))
            }
        }
    }

    fun applyChoice(event: ScheduleEvent, scope: String) {
        when (chooseAction) {
            "edit" -> openEditor(event, scope)
            "complete" -> viewModel.setStatus(event, "completed", scope, ::handle)
            "restore" -> viewModel.setStatus(event, "scheduled", scope, ::handle)
            "cancel" -> viewModel.setStatus(event, "cancelled", scope, ::handle)
        }
        chooseEvent = null
    }

    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_START) viewModel.refresh() }
        lifecycle.lifecycle.addObserver(observer)
        onDispose { lifecycle.lifecycle.removeObserver(observer) }
    }

    if (editor != null) {
        ScheduleEditor(
            state = editor!!,
            saving = state.isSaving,
            error = message,
            onBack = { editor = null; message = null },
            onSave = { draft ->
                val target = editor!!.target
                if (target == null) viewModel.create(draft, done = ::handle)
                else viewModel.update(target, draft, editor!!.scope, done = ::handle)
            },
        )
    } else {
        BackHandler(onBack = onBack)
        AuriBackground {
            Scaffold(
                containerColor = Color.Transparent,
                contentColor = MaterialTheme.colorScheme.onBackground,
                topBar = {
                    TopAppBar(
                        navigationIcon = { TextButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回") } },
                        title = { Text("日程") },
                        actions = { TextButton(onClick = viewModel::today) { Text("今天") } },
                        colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.surface.copy(alpha = .7f)),
                    )
                },
                floatingActionButton = {
                    FloatingActionButton(onClick = {
                        val start = state.selectedDate.atTime(9, 0)
                        editor = EditorState(null, "series", ScheduleDraft("", start, start.plusHours(1), state.timezone, false))
                    }) { Icon(Icons.Outlined.Add, "添加日程") }
                },
            ) { inset ->
                Column(Modifier.fillMaxSize().padding(inset)) {
                    MonthCalendar(state.month, state.selectedDate, state.events, viewModel::select, viewModel::moveMonth)
                    HorizontalDivider(color = AuriTokens.Outline)
                    state.error?.let {
                        Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(it, Modifier.weight(1f), color = MaterialTheme.colorScheme.error)
                            TextButton(onClick = viewModel::refresh) { Text("重试") }
                        }
                    }
                    val selected = state.events.filter { eventCoversDate(it, state.selectedDate) }
                    when {
                        state.isLoading && state.events.isEmpty() -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
                        selected.isEmpty() -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Text("${state.selectedDate.monthValue} 月 ${state.selectedDate.dayOfMonth} 日没有安排", color = AuriTokens.TextSecondary)
                        }
                        else -> LazyColumn(
                            Modifier.fillMaxSize(), contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
                            verticalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            items(selected, key = { "${it.id}-${it.occurrenceDate}-${it.startsAt}" }) { event ->
                                ScheduleCard(
                                    event = event,
                                    onEdit = {
                                        chooseAction = "edit"
                                        if (event.repeat == "none") openEditor(event, "series") else chooseEvent = event
                                    },
                                    onComplete = {
                                        chooseAction = if (event.status == "completed") "restore" else "complete"
                                        if (event.repeat == "none") {
                                            viewModel.setStatus(event, if (event.status == "completed") "scheduled" else "completed", "series", ::handle)
                                        } else chooseEvent = event
                                    },
                                    onCancel = {
                                        chooseAction = "cancel"
                                        if (event.repeat == "none") viewModel.setStatus(event, "cancelled", "series", ::handle)
                                        else chooseEvent = event
                                    },
                                )
                            }
                            item { Spacer(Modifier.height(80.dp)) }
                        }
                    }
                }
            }
        }
    }

    chooseEvent?.takeIf { it.repeat != "none" }?.let { event ->
        AlertDialog(
            onDismissRequest = { chooseEvent = null },
            title = { Text(if (chooseAction == "edit") "修改重复日程" else "处理重复日程") },
            text = { Text("只处理 ${event.occurrenceDate.monthValue} 月 ${event.occurrenceDate.dayOfMonth} 日这一次，还是整个系列？") },
            confirmButton = { TextButton(onClick = { applyChoice(event, "occurrence") }) { Text("仅这一次") } },
            dismissButton = { Row { TextButton(onClick = { chooseEvent = null }) { Text("取消") }; TextButton(onClick = { applyChoice(event, "series") }) { Text("整个系列") } } },
        )
    }
    confirmText?.let { text ->
        AlertDialog(
            onDismissRequest = { confirmText = null; retry = null }, title = { Text("请确认") }, text = { Text(text) },
            confirmButton = { TextButton(onClick = { val action = retry; confirmText = null; retry = null; action?.invoke() }) { Text("继续保存") } },
            dismissButton = { TextButton(onClick = { confirmText = null; retry = null }) { Text("取消") } },
        )
    }
}

private data class EditorState(val target: ScheduleEvent?, val scope: String, val draft: ScheduleDraft)

@Composable
private fun MonthCalendar(month: YearMonth, selected: LocalDate, events: List<ScheduleEvent>, onSelect: (LocalDate) -> Unit, onMove: (Long) -> Unit) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = { onMove(-1) }) { Icon(Icons.AutoMirrored.Outlined.KeyboardArrowLeft, "上个月") }
            Text("${month.year} 年 ${month.monthValue} 月", Modifier.weight(1f), textAlign = TextAlign.Center, style = MaterialTheme.typography.titleMedium)
            TextButton(onClick = { onMove(1) }) { Icon(Icons.AutoMirrored.Outlined.KeyboardArrowRight, "下个月") }
        }
        Row(Modifier.fillMaxWidth()) {
            listOf("一", "二", "三", "四", "五", "六", "日").forEach { Text(it, Modifier.weight(1f), textAlign = TextAlign.Center, color = AuriTokens.Muted, style = MaterialTheme.typography.bodySmall) }
        }
        monthCalendarCells(month).chunked(7).forEach { week ->
            Row(Modifier.fillMaxWidth()) {
                week.forEach { day ->
                    Box(Modifier.weight(1f).height(48.dp), contentAlignment = Alignment.Center) {
                        if (day != null) {
                            val chosen = day == selected
                            Column(
                                Modifier.size(42.dp).background(if (chosen) AuriTokens.Primary else Color.Transparent, CircleShape).clickable { onSelect(day) },
                                horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center,
                            ) {
                                Text(day.dayOfMonth.toString(), fontWeight = if (day == LocalDate.now()) FontWeight.Bold else FontWeight.Normal)
                                if (events.any { eventCoversDate(it, day) }) Box(Modifier.size(4.dp).background(if (chosen) Color.White else AuriTokens.Secondary, CircleShape))
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ScheduleCard(event: ScheduleEvent, onEdit: () -> Unit, onComplete: () -> Unit, onCancel: () -> Unit) {
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
        shape = RoundedCornerShape(14.dp),
    ) {
        Column(Modifier.fillMaxWidth().clickable(onClick = onEdit).padding(14.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Top) {
            Box(Modifier.width(3.dp).height(48.dp).background(if (event.status == "completed") AuriTokens.Muted else AuriTokens.Primary, RoundedCornerShape(2.dp)))
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(event.title, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                val time = if (event.allDay) "全天" else "${event.startsAt.format(DateTimeFormatter.ofPattern("HH:mm"))} - ${event.endsAt.format(DateTimeFormatter.ofPattern("HH:mm"))}"
                Text(time + if (event.repeat != "none") " · ${repeatLabel(event.repeat)}" else "", color = AuriTokens.TextSecondary, style = MaterialTheme.typography.bodySmall)
                if (event.location.isNotBlank()) Text(event.location, color = AuriTokens.TextSecondary, style = MaterialTheme.typography.bodySmall)
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            TextButton(onClick = onComplete) { Text(if (event.status == "completed") "恢复" else "完成") }
            TextButton(onClick = onCancel) { Text("取消日程") }
        }
        }
    }
}

private fun repeatLabel(value: String) = when (value) { "daily" -> "每天"; "weekdays" -> "工作日"; "weekly" -> "每周"; else -> "不重复" }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ScheduleEditor(state: EditorState, saving: Boolean, error: String?, onBack: () -> Unit, onSave: (ScheduleDraft) -> Unit) {
    var title by rememberSaveable(state) { mutableStateOf(state.draft.title) }
    var startDate by rememberSaveable(state) { mutableStateOf(state.draft.startsAt.toLocalDate().toString()) }
    var startTime by rememberSaveable(state) { mutableStateOf(state.draft.startsAt.toLocalTime().format(DateTimeFormatter.ofPattern("HH:mm"))) }
    val displayEnd = if (state.draft.allDay) state.draft.endsAt.toLocalDate().minusDays(1) else state.draft.endsAt.toLocalDate()
    var endDate by rememberSaveable(state) { mutableStateOf(displayEnd.toString()) }
    var endTime by rememberSaveable(state) { mutableStateOf(state.draft.endsAt.toLocalTime().format(DateTimeFormatter.ofPattern("HH:mm"))) }
    var allDay by rememberSaveable(state) { mutableStateOf(state.draft.allDay) }
    var location by rememberSaveable(state) { mutableStateOf(state.draft.location) }
    var notes by rememberSaveable(state) { mutableStateOf(state.draft.notes) }
    var reminder by rememberSaveable(state) { mutableStateOf(state.draft.reminderMinutes ?: -1) }
    var repeat by rememberSaveable(state) { mutableStateOf(if (state.scope == "occurrence") "none" else state.draft.repeat) }
    var repeatUntil by rememberSaveable(state) { mutableStateOf(state.draft.repeatUntil?.toString().orEmpty()) }
    var formError by rememberSaveable(state) { mutableStateOf<String?>(null) }
    var datePickerTarget by rememberSaveable(state) { mutableStateOf<String?>(null) }
    var timePickerTarget by rememberSaveable(state) { mutableStateOf<String?>(null) }
    BackHandler(onBack = onBack)
    AuriBackground {
        Scaffold(
            containerColor = Color.Transparent,
            contentColor = MaterialTheme.colorScheme.onBackground,
            topBar = { TopAppBar(
                navigationIcon = { TextButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "返回") } },
                title = { Text(if (state.target == null) "新建日程" else if (state.scope == "occurrence") "编辑这一次" else "编辑整个系列") },
                actions = { TextButton(enabled = !saving, onClick = {
                    val result = runCatching {
                        require(title.isNotBlank()) { "请填写日程标题" }
                        val startDay = LocalDate.parse(startDate)
                        val endDay = LocalDate.parse(endDate)
                        val start = if (allDay) startDay.atStartOfDay() else LocalDateTime.of(startDay, LocalTime.parse(startTime))
                        val end = if (allDay) endDay.plusDays(1).atStartOfDay() else LocalDateTime.of(endDay, LocalTime.parse(endTime))
                        require(end > start) { "结束时间必须晚于开始时间" }
                        ScheduleDraft(title.trim(), start, end, state.draft.timezone, allDay, location.trim(), notes.trim(), reminder.takeIf { it >= 0 }, repeat,
                            repeatUntil.takeIf { it.isNotBlank() }?.let(LocalDate::parse), state.draft.status)
                    }
                    result.onSuccess { formError = null; onSave(it) }.onFailure { formError = "请检查日程时间：${it.message.orEmpty()}" }
                }) { Text(if (saving) "保存中…" else "保存") } },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.surface.copy(alpha = .7f)),
            ) },
        ) { inset ->
            Column(Modifier.fillMaxSize().padding(inset).verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                (formError ?: error)?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                OutlinedTextField(title, { title = it }, Modifier.fillMaxWidth(), label = { Text("标题") }, singleLine = true)
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) { Text("全天", Modifier.weight(1f)); Switch(allDay, { allDay = it }) }
                PickerField("开始日期", LocalDate.parse(startDate).format(DateTimeFormatter.ofPattern("yyyy 年 M 月 d 日"))) { datePickerTarget = "start" }
                if (!allDay) PickerField("开始时间", startTime) { timePickerTarget = "start" }
                PickerField(if (allDay) "结束日期（包含）" else "结束日期", LocalDate.parse(endDate).format(DateTimeFormatter.ofPattern("yyyy 年 M 月 d 日"))) { datePickerTarget = "end" }
                if (!allDay) PickerField("结束时间", endTime) { timePickerTarget = "end" }
                OutlinedTextField(location, { location = it }, Modifier.fillMaxWidth(), label = { Text("地点（选填）") }, singleLine = true)
                OutlinedTextField(notes, { notes = it }, Modifier.fillMaxWidth(), label = { Text("备注（选填）") }, minLines = 2)
                Text("提前提醒", fontWeight = FontWeight.SemiBold)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf(-1 to "不提醒", 0 to "准时").forEach { (value, label) ->
                        FilterChip(reminder == value, { reminder = value }, { Text(label) })
                    }
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf(10 to "10 分钟", 30 to "30 分钟").forEach { (value, label) ->
                        FilterChip(reminder == value, { reminder = value }, { Text(label) })
                    }
                }
                if (state.scope != "occurrence") {
                    Text("重复", fontWeight = FontWeight.SemiBold)
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        listOf("none", "daily").forEach { value -> FilterChip(repeat == value, { repeat = value }, { Text(repeatLabel(value)) }) }
                    }
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        listOf("weekdays", "weekly").forEach { value -> FilterChip(repeat == value, { repeat = value }, { Text(repeatLabel(value)) }) }
                    }
                    if (repeat != "none") PickerField(
                        "重复截止日期（选填）",
                        repeatUntil.takeIf { it.isNotBlank() }?.let { LocalDate.parse(it).format(DateTimeFormatter.ofPattern("yyyy 年 M 月 d 日")) } ?: "不设置截止日期",
                    ) { datePickerTarget = "repeat" }
                }
                Spacer(Modifier.height(32.dp))
            }
        }
    }

    datePickerTarget?.let { target ->
        val initial = when (target) {
            "start" -> LocalDate.parse(startDate)
            "end" -> LocalDate.parse(endDate)
            else -> repeatUntil.takeIf { it.isNotBlank() }?.let(LocalDate::parse) ?: LocalDate.parse(endDate)
        }
        ScheduleDatePickerDialog(
            title = when (target) { "start" -> "选择开始日期"; "end" -> "选择结束日期"; else -> "选择重复截止日期" },
            initialDate = initial,
            allowClear = target == "repeat",
            onDismiss = { datePickerTarget = null },
            onClear = { repeatUntil = ""; datePickerTarget = null },
            onConfirm = { selected ->
                when (target) {
                    "start" -> {
                        startDate = selected.toString()
                        if (LocalDate.parse(endDate) < selected) endDate = selected.toString()
                    }
                    "end" -> endDate = selected.toString()
                    else -> repeatUntil = selected.toString()
                }
                datePickerTarget = null
            },
        )
    }

    timePickerTarget?.let { target ->
        val initial = LocalTime.parse(if (target == "start") startTime else endTime)
        ScheduleTimePickerDialog(
            title = if (target == "start") "选择开始时间" else "选择结束时间",
            initialTime = initial,
            onDismiss = { timePickerTarget = null },
            onConfirm = { selected ->
                val formatted = selected.format(DateTimeFormatter.ofPattern("HH:mm"))
                if (target == "start") startTime = formatted else endTime = formatted
                timePickerTarget = null
            },
        )
    }
}

@Composable
private fun PickerField(label: String, value: String, onClick: () -> Unit) {
    OutlinedButton(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(6.dp),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp, vertical = 10.dp),
    ) {
        Column(Modifier.weight(1f), horizontalAlignment = Alignment.Start) {
            Text(label, style = MaterialTheme.typography.labelSmall, color = AuriTokens.TextSecondary)
            Spacer(Modifier.height(2.dp))
            Text(value, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurface)
        }
        Text("选择", color = AuriTokens.Primary)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ScheduleDatePickerDialog(
    title: String,
    initialDate: LocalDate,
    allowClear: Boolean,
    onDismiss: () -> Unit,
    onClear: () -> Unit,
    onConfirm: (LocalDate) -> Unit,
) {
    val pickerState = androidx.compose.material3.rememberDatePickerState(
        initialSelectedDateMillis = localDateToPickerMillis(initialDate),
    )
    DatePickerDialog(
        onDismissRequest = onDismiss,
        confirmButton = {
            TextButton(
                enabled = pickerState.selectedDateMillis != null,
                onClick = { pickerState.selectedDateMillis?.let { onConfirm(pickerMillisToLocalDate(it)) } },
            ) { Text("确定") }
        },
        dismissButton = {
            Row {
                if (allowClear) TextButton(onClick = onClear) { Text("清除") }
                TextButton(onClick = onDismiss) { Text("取消") }
            }
        },
    ) {
        DatePicker(
            state = pickerState,
            title = { Text(title, Modifier.padding(start = 24.dp, top = 16.dp)) },
            showModeToggle = false,
        )
    }
}

@Composable
private fun ScheduleTimePickerDialog(
    title: String,
    initialTime: LocalTime,
    onDismiss: () -> Unit,
    onConfirm: (LocalTime) -> Unit,
) {
    var hour by rememberSaveable { mutableStateOf(initialTime.hour) }
    var minute by rememberSaveable { mutableStateOf(initialTime.minute) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                NumberWheel(0..23, hour, "时") { hour = it }
                Text(":", Modifier.padding(horizontal = 8.dp), style = MaterialTheme.typography.headlineMedium)
                NumberWheel(0..59, minute, "分") { minute = it }
            }
        },
        confirmButton = { TextButton(onClick = { onConfirm(LocalTime.of(hour, minute)) }) { Text("确定") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

@Composable
private fun NumberWheel(values: IntRange, selected: Int, unit: String, onSelected: (Int) -> Unit) {
    val itemHeight = 48.dp
    val itemHeightPx = with(LocalDensity.current) { itemHeight.roundToPx() }
    val initialOffset = (selected - values.first).coerceIn(0, values.count() - 1) * itemHeightPx
    val scrollState = rememberScrollState(initialOffset)
    val scope = rememberCoroutineScope()

    LaunchedEffect(scrollState, itemHeightPx) {
        snapshotFlow { scrollState.isScrollInProgress }
            .distinctUntilChanged()
            .filter { !it }
            .collect {
                val index = (scrollState.value.toFloat() / itemHeightPx).roundToInt().coerceIn(0, values.count() - 1)
                scrollState.animateScrollTo(index * itemHeightPx)
                onSelected(values.first + index)
            }
    }

    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(unit, style = MaterialTheme.typography.labelMedium, color = AuriTokens.TextSecondary)
        Spacer(Modifier.height(6.dp))
        Box(Modifier.width(88.dp).height(240.dp), contentAlignment = Alignment.Center) {
            Box(
                Modifier.fillMaxWidth().height(itemHeight)
                    .background(MaterialTheme.colorScheme.primaryContainer, RoundedCornerShape(10.dp)),
            )
            Column(
                Modifier.fillMaxSize().verticalScroll(scrollState),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Spacer(Modifier.height(itemHeight * 2))
                values.forEach { value ->
                    Box(
                        Modifier.fillMaxWidth().height(itemHeight).clickable {
                            scope.launch {
                                scrollState.animateScrollTo((value - values.first) * itemHeightPx)
                                onSelected(value)
                            }
                        },
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(
                            value.toString().padStart(2, '0'),
                            style = MaterialTheme.typography.titleLarge,
                            fontWeight = if (value == selected) FontWeight.Bold else FontWeight.Normal,
                            color = if (value == selected) MaterialTheme.colorScheme.onPrimaryContainer else AuriTokens.TextSecondary,
                        )
                    }
                }
                Spacer(Modifier.height(itemHeight * 2))
            }
        }
    }
}
