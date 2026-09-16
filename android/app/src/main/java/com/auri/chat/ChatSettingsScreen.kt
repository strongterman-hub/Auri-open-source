package com.auri.chat

import android.content.Context
import android.media.MediaPlayer
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.KeyboardArrowRight
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Face
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import coil.compose.SubcomposeAsyncImage
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private data class SoundChoice(
    val key: String,
    val label: String,
    val subtitle: String,
    val rawResId: Int? = null,
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatSettingsScreen(
    onBack: () -> Unit,
    viewModel: ChatViewModel = viewModel(),
) {
    val context = LocalContext.current
    val prefs = remember {
        context.getSharedPreferences("auri_settings", Context.MODE_PRIVATE)
    }
    val choices = remember { buildSoundChoices() }
    var selectedSoundKey by remember {
        mutableStateOf(prefs.getString("notification_sound", "system") ?: "system")
    }
    var showSearch by remember { mutableStateOf(false) }
    var showClearDialog by remember { mutableStateOf(false) }
    var showSoundSheet by remember { mutableStateOf(false) }
    var showPresentationSheet by remember { mutableStateOf(false) }
    var showPersonalitySheet by remember { mutableStateOf(false) }
    var query by remember { mutableStateOf("") }
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        viewModel.loadPersonaOptions()
    }

    LaunchedEffect(viewModel.personaNotice) {
        val message = viewModel.personaNotice ?: return@LaunchedEffect
        snackbarHostState.showSnackbar(message)
        viewModel.clearPersonaNotice()
    }

    BackHandler {
        if (showSearch) {
            showSearch = false
            query = ""
        } else {
            onBack()
        }
    }

    AuriBackground {
        Scaffold(
            modifier = Modifier.fillMaxSize(),
            containerColor = Color.Transparent,
            snackbarHost = { SnackbarHost(snackbarHostState) },
            topBar = {
                TopAppBar(
                    navigationIcon = {
                        TextButton(onClick = {
                            if (showSearch) {
                                showSearch = false
                                query = ""
                            } else {
                                onBack()
                            }
                        }) {
                            Icon(
                                imageVector = Icons.AutoMirrored.Outlined.ArrowBack,
                                contentDescription = "返回",
                                tint = MaterialTheme.colorScheme.onSurface,
                            )
                        }
                    },
                    title = {
                        Text(
                            if (showSearch) "查找聊天记录" else "聊天设置",
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                    },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.62f),
                        titleContentColor = MaterialTheme.colorScheme.onSurface,
                    ),
                )
            },
        ) { innerPadding ->
        if (showSearch) {
            SearchChatHistory(
                query = query,
                onQueryChange = { query = it },
                messages = viewModel.messages,
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding),
            )
        } else {
            ChatSettingsContent(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(innerPadding),
                onSearch = {
                    showSearch = true
                    query = ""
                },
                onClear = { showClearDialog = true },
                onSound = { showSoundSheet = true },
                personaOptions = viewModel.personaOptions,
                personaLoading = viewModel.personaLoading,
                personaLoadFailed = viewModel.personaLoadFailed,
                onRetryPersona = { viewModel.loadPersonaOptions() },
                onPresentation = { showPresentationSheet = true },
                onPersonality = { showPersonalitySheet = true },
            )
        }
    }

    if (showClearDialog) {
        AlertDialog(
            onDismissRequest = { showClearDialog = false },
            title = { Text("清空聊天记录") },
            text = { Text("确定要清空所有聊天记录吗？此操作无法撤销。") },
            confirmButton = {
                TextButton(
                    onClick = {
                        showClearDialog = false
                        viewModel.clearConversation()
                        scope.launch {
                            snackbarHostState.showSnackbar("聊天记录已清空")
                        }
                    },
                ) {
                    Text("清空")
                }
            },
            dismissButton = {
                TextButton(onClick = { showClearDialog = false }) {
                    Text("取消")
                }
            },
        )
    }

    if (showSoundSheet) {
        ModalBottomSheet(onDismissRequest = { showSoundSheet = false }) {
            LazyColumn(
                modifier = Modifier.fillMaxWidth(),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(
                    horizontal = 16.dp,
                    vertical = 8.dp,
                ),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                item(key = "sound-header") {
                    Text(
                        text = "设置提示音",
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onSurface,
                        modifier = Modifier.padding(vertical = 8.dp),
                    )
                }
                items(choices, key = { it.key }) { choice ->
                    val selected = choice.key == selectedSoundKey
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(12.dp))
                            .clickable {
                                selectedSoundKey = choice.key
                                prefs.edit()
                                    .putString("notification_sound", choice.key)
                                    .apply()
                                applyPushNotificationSound(context, recreateChannel = true)
                                playSound(context, choice)
                            }
                            .padding(horizontal = 12.dp, vertical = 12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                text = choice.label,
                                style = MaterialTheme.typography.bodyLarge,
                                color = MaterialTheme.colorScheme.onSurface,
                            )
                            Text(
                                text = choice.subtitle,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        if (selected) {
                            Icon(
                                imageVector = Icons.Outlined.Check,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.primary,
                            )
                        }
                    }
                }
            }
        }
    }
    if (showPresentationSheet) {
        val options = viewModel.personaOptions
        if (options != null) {
            PersonaChoiceSheet(
                title = "选择性别",
                choices = options.characters.map { character ->
                    PersonaChoiceItem(
                        key = character.presentation,
                        title = character.label,
                        subtitle = character.summary,
                        selected = character.presentation == options.selection.presentation,
                    )
                },
                onDismiss = { showPresentationSheet = false },
                onSelect = { value ->
                    showPresentationSheet = false
                    viewModel.selectPresentation(value)
                },
            )
        }
    }

    if (showPersonalitySheet) {
        val options = viewModel.personaOptions
        if (options != null) {
            PersonaChoiceSheet(
                title = "选择性格",
                choices = options.presets.map { preset ->
                    PersonaChoiceItem(
                        key = preset.presetId,
                        title = preset.label,
                        subtitle = listOf(preset.description, preset.frequencyHint)
                            .filter { it.isNotBlank() }
                            .joinToString(" · "),
                        selected = preset.presetId == options.selection.presetId,
                    )
                },
                onDismiss = { showPersonalitySheet = false },
                onSelect = { value ->
                    showPersonalitySheet = false
                    viewModel.selectPersonality(value)
                },
            )
        }
    }
    }
}

@Composable
private fun ChatSettingsContent(
    modifier: Modifier,
    onSearch: () -> Unit,
    onClear: () -> Unit,
    onSound: () -> Unit,
    personaOptions: PersonaOptions?,
    personaLoading: Boolean,
    personaLoadFailed: Boolean,
    onRetryPersona: () -> Unit,
    onPresentation: () -> Unit,
    onPersonality: () -> Unit,
) {
    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Spacer(modifier = Modifier.height(20.dp))
        val currentCharacter = personaOptions?.let { options ->
            options.characters.firstOrNull {
                it.presentation == options.selection.presentation
            }
        }
        AuriSettingsAvatar(avatarUrl = currentCharacter?.avatarUrl.orEmpty())
        Spacer(modifier = Modifier.height(28.dp))
        PersonaSettingsGroup(
            options = personaOptions,
            loading = personaLoading,
            loadFailed = personaLoadFailed,
            onRetry = onRetryPersona,
            onPresentation = onPresentation,
            onPersonality = onPersonality,
        )
        Surface(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(14.dp),
            color = MaterialTheme.colorScheme.surfaceVariant,
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 8.dp),
            ) {
                SettingsRow(
                    icon = Icons.Outlined.Search,
                    title = "查找聊天记录",
                    subtitle = "按关键词搜索历史消息",
                    onClick = onSearch,
                )
                SettingsRow(
                    icon = Icons.Outlined.Delete,
                    title = "清空聊天记录",
                    subtitle = "删除当前会话中的全部消息",
                    onClick = onClear,
                )
                SettingsRow(
                    icon = Icons.Outlined.Notifications,
                    title = "设置提示音",
                    subtitle = "选择新消息提醒音",
                    onClick = onSound,
                )
            }
        }
    }
}

@Composable
private fun SettingsRow(
    icon: ImageVector,
    title: String,
    subtitle: String,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            imageVector = icon,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(22.dp),
        )
        Column(
            modifier = Modifier
                .weight(1f)
                .padding(horizontal = 12.dp),
        ) {
            Text(
                text = title,
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurface,
            )
            Text(
                text = subtitle,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Icon(
            imageVector = Icons.AutoMirrored.Outlined.KeyboardArrowRight,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun SearchChatHistory(
    query: String,
    onQueryChange: (String) -> Unit,
    messages: List<ChatMessage>,
    modifier: Modifier,
) {
    Column(modifier = modifier) {
        OutlinedTextField(
            value = query,
            onValueChange = onQueryChange,
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            placeholder = { Text("输入关键词搜索聊天记录") },
            singleLine = true,
            shape = RoundedCornerShape(14.dp),
        )
        val results = if (query.isBlank()) {
            emptyList()
        } else {
            messages.filter { message ->
                val searchable = buildString {
                    append(message.content)
                    message.files.forEach { file ->
                        append(' ')
                        append(file.name)
                    }
                }
                searchable.contains(query.trim(), ignoreCase = true)
            }
        }

        if (query.isNotBlank() && results.isEmpty()) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 80.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = "没有找到相关聊天记录",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(
                    horizontal = 16.dp,
                    vertical = 8.dp,
                ),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(results, key = { it.id }) { message ->
                    SearchResultItem(message)
                }
            }
        }
    }
}

@Composable
private fun SearchResultItem(message: ChatMessage) {
    val preview = when {
        message.content.isNotBlank() -> message.content
        message.images.isNotEmpty() -> "[图片]"
        message.files.isNotEmpty() -> "[文件] ${message.files.joinToString { it.name }}"
        else -> "[空消息]"
    }
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.surfaceVariant,
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.Top,
        ) {
            Text(
                text = if (message.isUser) "我" else "Auri",
                color = if (message.isUser) {
                    MaterialTheme.colorScheme.primary
                } else {
                    MaterialTheme.colorScheme.secondary
                },
                style = MaterialTheme.typography.labelLarge,
                modifier = Modifier.widthIn(min = 40.dp),
            )
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = preview,
                    color = MaterialTheme.colorScheme.onSurface,
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = formatChatTime(message.timestamp),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
}

private fun buildSoundChoices(): List<SoundChoice> = listOf(
    SoundChoice(
        key = "system",
        label = "系统默认",
        subtitle = "跟随系统通知提示音",
    ),
    SoundChoice(
        key = "android",
        label = "安卓短信",
        subtitle = "常见短信提示音",
        rawResId = R.raw.auri_tone_android,
    ),
    SoundChoice(
        key = "gentle",
        label = "柔和提示",
        subtitle = "轻快柔和的提示音",
        rawResId = R.raw.auri_tone_gentle,
    ),
    SoundChoice(
        key = "short",
        label = "短促提示",
        subtitle = "简洁明快的短提示音",
        rawResId = R.raw.auri_tone_short,
    ),
    SoundChoice(
        key = "classic",
        label = "经典提示",
        subtitle = "清晰的经典通知音",
        rawResId = R.raw.auri_tone_classic,
    ),
)

private fun playSound(context: Context, choice: SoundChoice) {
    val uri = if (choice.rawResId != null) {
        Uri.parse("android.resource://${context.packageName}/${choice.rawResId}")
    } else {
        Settings.System.DEFAULT_NOTIFICATION_URI
    }
    runCatching {
        val player = MediaPlayer.create(context, uri)
        player?.setOnCompletionListener { it.release() }
        player?.start()
    }
}

private fun formatChatTime(timestamp: Long): String =
    SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.getDefault()).format(Date(timestamp))


private data class PersonaChoiceItem(
    val key: String,
    val title: String,
    val subtitle: String,
    val selected: Boolean,
)

@Composable
private fun AuriSettingsAvatar(avatarUrl: String) {
    Box(
        modifier = Modifier.size(96.dp),
        contentAlignment = Alignment.Center,
    ) {
        // The gradient letter avatar stays underneath, so a missing or failed
        // character image silently falls back to the old settings identity.
        Box(
            modifier = Modifier
                .fillMaxSize()
                .clip(CircleShape)
                .background(
                    Brush.linearGradient(
                        listOf(
                            MaterialTheme.colorScheme.primary,
                            MaterialTheme.colorScheme.secondary,
                        ),
                    ),
                ),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = "Auri",
                color = Color.White,
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
                textAlign = TextAlign.Center,
            )
        }
        if (avatarUrl.isNotBlank()) {
            SubcomposeAsyncImage(
                model = avatarUrl,
                contentDescription = "Auri 头像",
                contentScale = ContentScale.Crop,
                modifier = Modifier
                    .fillMaxSize()
                    .clip(CircleShape)
                    .border(
                        width = 2.dp,
                        color = Color.White.copy(alpha = 0.12f),
                        shape = CircleShape,
                    ),
                loading = {},
                error = {},
            )
        }
    }
}

@Composable
private fun PersonaSettingsGroup(
    options: PersonaOptions?,
    loading: Boolean,
    loadFailed: Boolean,
    onRetry: () -> Unit,
    onPresentation: () -> Unit,
    onPersonality: () -> Unit,
) {
    if (options == null && !loading && !loadFailed) return
    if (options == null) {
        Surface(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(14.dp),
            color = MaterialTheme.colorScheme.surfaceVariant,
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 8.dp),
            ) {
                Text(
                    text = "Auri 人设",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                )
                SettingsRow(
                    icon = Icons.Outlined.Face,
                    title = if (loading) "正在加载…" else "Auri 人设暂不可用",
                    subtitle = if (loading) "正在读取当前设置" else "点按重试",
                    onClick = { if (!loading) onRetry() },
                )
            }
        }
        Spacer(modifier = Modifier.height(12.dp))
        return
    }
    if (!options.available || !options.userSelectionEnabled) return
    val currentCharacter = options.characters.firstOrNull {
        it.presentation == options.selection.presentation
    }
    val currentPreset = options.presets.firstOrNull {
        it.presetId == options.selection.presetId
    }
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        color = MaterialTheme.colorScheme.surfaceVariant,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 8.dp, vertical = 8.dp),
        ) {
            Text(
                text = "Auri 人设",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            )
            if (options.portraitAvailable) {
                SettingsRow(
                    icon = Icons.Outlined.Face,
                    title = "性别",
                    subtitle = listOfNotNull(
                        currentCharacter?.label,
                        currentCharacter?.summary,
                    ).joinToString(" · ").ifBlank { options.selection.presentation },
                    onClick = onPresentation,
                )
            }
            SettingsRow(
                icon = Icons.Outlined.Person,
                title = "性格",
                subtitle = listOfNotNull(
                    currentPreset?.label,
                    currentPreset?.description,
                ).joinToString(" · ").ifBlank { options.selection.presetId },
                onClick = onPersonality,
            )
        }
    }
    Spacer(modifier = Modifier.height(12.dp))
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PersonaChoiceSheet(
    title: String,
    choices: List<PersonaChoiceItem>,
    onDismiss: () -> Unit,
    onSelect: (String) -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        LazyColumn(
            modifier = Modifier.fillMaxWidth(),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(
                horizontal = 16.dp,
                vertical = 8.dp,
            ),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            item(key = "persona-header") {
                Text(
                    text = title,
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.padding(vertical = 8.dp),
                )
            }
            items(choices, key = { it.key }) { choice ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(12.dp))
                        .clickable { onSelect(choice.key) }
                        .padding(horizontal = 12.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = choice.title,
                            style = MaterialTheme.typography.bodyLarge,
                            color = MaterialTheme.colorScheme.onSurface,
                        )
                        if (choice.subtitle.isNotBlank()) {
                            Text(
                                text = choice.subtitle,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                    if (choice.selected) {
                        Icon(
                            imageVector = Icons.Outlined.Check,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.primary,
                        )
                    }
                }
            }
        }
    }
}