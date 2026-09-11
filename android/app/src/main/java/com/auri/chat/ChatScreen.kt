package com.auri.chat

import android.Manifest
import android.content.Context
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Build
import android.provider.OpenableColumns
import android.util.Base64
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.List
import androidx.compose.material.icons.automirrored.outlined.Send
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.DateRange
import androidx.compose.material.icons.outlined.FavoriteBorder
import androidx.compose.material.icons.outlined.Menu
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

private sealed interface ChatListItem {
    data class Timestamp(val time: Long) : ChatListItem
    data class Message(val message: ChatMessage) : ChatListItem
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    onOpenSchedule: () -> Unit = {},
    onOpenHealth: () -> Unit,
    onOpenAccount: () -> Unit,
    onOpenSettings: () -> Unit,
    onOpenCredits: () -> Unit,
    viewModel: ChatViewModel = viewModel(),
) {
    val lifecycleOwner = LocalLifecycleOwner.current
    val context = LocalContext.current
    val backgroundSettingsProfile = remember { currentBackgroundSettingsProfile() }
    var notificationsGranted by remember { mutableStateOf(areNotificationsGranted(context)) }
    var locationGranted by remember { mutableStateOf(isLocationGranted(context)) }

    val notificationPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) {
        notificationsGranted = areNotificationsGranted(context)
        if (notificationsGranted) {
            viewModel.reportGuideActionCompleted("request_notification")
            AuriKeepAliveController.start(context)
        } else {
            AuriKeepAliveController.stop(context)
        }
    }
    val locationPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        locationGranted = granted
        if (granted) {
            viewModel.reportGuideActionCompleted("request_location")
        }
    }

    fun handleChatAction(action: ChatAction) {
        when (action.type) {
            "enable_proactive" -> {
                if (viewModel.proactiveEnabled) {
                    onOpenAccount()
                } else {
                    viewModel.enableProactive()
                    viewModel.reportGuideActionCompleted("enable_proactive")
                }
            }
            "open_health" -> {
                onOpenHealth()
                viewModel.reportGuideActionCompleted("open_health")
            }
            "open_account" -> onOpenAccount()
            "request_notification" -> {
                if (Build.VERSION.SDK_INT >= 33 && !notificationsGranted) {
                    notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                } else {
                    context.startActivity(openNotificationSettings(context))
                    viewModel.reportGuideActionCompleted("request_notification")
                }
            }
            "request_location" -> {
                if (!locationGranted) {
                    locationPermissionLauncher.launch(Manifest.permission.ACCESS_FINE_LOCATION)
                } else {
                    context.startActivity(openAppDetails(context))
                    viewModel.reportGuideActionCompleted("request_location")
                }
            }
            "open_autostart" -> {
                backgroundSettingsProfile?.let { openBackgroundSettings(context, it) }
                viewModel.reportGuideActionCompleted("open_autostart")
            }
        }
    }

    DisposableEffect(lifecycleOwner) {
        viewModel.setChatVisible(true)
        viewModel.refreshFromServer()
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_START -> {
                    viewModel.setAppInForeground(true)
                    viewModel.refreshFromServer()
                    viewModel.refreshProactiveEnabled()
                    notificationsGranted = areNotificationsGranted(context)
                    locationGranted = isLocationGranted(context)
                    if (notificationsGranted) {
                        AuriKeepAliveController.start(context)
                    } else {
                        AuriKeepAliveController.stop(context)
                    }
                }
                Lifecycle.Event.ON_STOP -> viewModel.setAppInForeground(false)
                else -> Unit
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose {
            viewModel.setChatVisible(false)
            lifecycleOwner.lifecycle.removeObserver(observer)
        }
    }

    val messages = viewModel.messages
    val shouldSkipBackgroundSettingsAction = backgroundSettingsProfile == null &&
        messages.any { message -> message.actions.any { it.type == "open_autostart" } }
    LaunchedEffect(shouldSkipBackgroundSettingsAction) {
        if (shouldSkipBackgroundSettingsAction) {
            viewModel.reportGuideActionCompleted("open_autostart")
        }
    }
    val drawerState = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val displayItems = buildChatDisplayItems(messages)
    val reversedItems = displayItems.asReversed()
    val listState = rememberLazyListState()
    LaunchedEffect(messages.lastOrNull()?.id, messages.lastOrNull()?.content?.length) {
        if (reversedItems.isNotEmpty()) {
            listState.scrollToItem(0)
        }
    }

    val shouldLoadOlder by remember {
        derivedStateOf {
            val info = listState.layoutInfo
            val lastVisible = info.visibleItemsInfo.maxOfOrNull { it.index } ?: -1
            info.totalItemsCount > info.visibleItemsInfo.size &&
                lastVisible >= info.totalItemsCount - 1
        }
    }
    LaunchedEffect(shouldLoadOlder) {
        if (shouldLoadOlder) {
            viewModel.loadOlder()
        }
    }

    AuriBackground {
        ModalNavigationDrawer(
            drawerState = drawerState,
            drawerContent = {
                AppDrawer(
                    onOpenSchedule = {
                        scope.launch { drawerState.close() }
                        onOpenSchedule()
                    },
                    onOpenHealth = {
                        scope.launch { drawerState.close() }
                        onOpenHealth()
                    },
                    onOpenAccount = {
                        scope.launch { drawerState.close() }
                        onOpenAccount()
                    },
                )
            },
        ) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .imePadding(),
            ) {
                TopAppBar(
                    navigationIcon = {
                        TextButton(
                            onClick = { scope.launch { drawerState.open() } },
                        ) {
                            Icon(
                                imageVector = Icons.Outlined.Menu,
                                contentDescription = "打开菜单",
                                tint = MaterialTheme.colorScheme.onSurface,
                            )
                        }
                    },
                    title = {
                        Box(
                            modifier = Modifier.fillMaxWidth(),
                            contentAlignment = Alignment.Center,
                        ) {
                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                Text(
                                    text = "Auri",
                                    textAlign = TextAlign.Center,
                                    color = MaterialTheme.colorScheme.onSurface,
                                )
                                Text(
                                    text = if (viewModel.isAuriTyping) {
                                        "正在输入…"
                                    } else {
                                        "内容由AI生成"
                                    },
                                    color = AuriTokens.Muted,
                                    style = MaterialTheme.typography.labelSmall,
                                )
                            }
                        }
                    },
                    actions = {
                        TextButton(onClick = onOpenSettings) {
                            Icon(
                                imageVector = Icons.Outlined.MoreVert,
                                contentDescription = "聊天设置",
                                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = MaterialTheme.colorScheme.surface.copy(alpha = 0.62f),
                        titleContentColor = MaterialTheme.colorScheme.onSurface,
                        actionIconContentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                    ),
                )

                viewModel.error?.let {
                    Surface(
                        color = MaterialTheme.colorScheme.errorContainer,
                        contentColor = MaterialTheme.colorScheme.onErrorContainer,
                        shape = RoundedCornerShape(10.dp),
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 12.dp, vertical = 6.dp),
                    ) {
                        Row(
                            modifier = Modifier.padding(start = 14.dp, end = 4.dp, top = 6.dp, bottom = 6.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                text = it,
                                modifier = Modifier.weight(1f),
                                style = MaterialTheme.typography.bodyMedium,
                            )
                            if (it.contains("Credits") || it.contains("余额不足")) {
                                TextButton(onClick = onOpenCredits) { Text("充值") }
                            } else {
                                TextButton(onClick = viewModel::clearError) { Text("关闭") }
                            }
                        }
                    }
                }

                viewModel.notice?.let {
                    Surface(
                        color = MaterialTheme.colorScheme.secondaryContainer,
                        contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
                        shape = RoundedCornerShape(10.dp),
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 12.dp, vertical = 6.dp),
                    ) {
                        Row(
                            modifier = Modifier.padding(start = 14.dp, end = 4.dp, top = 6.dp, bottom = 6.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(
                                text = it,
                                modifier = Modifier.weight(1f),
                                style = MaterialTheme.typography.bodyMedium,
                            )
                            TextButton(onClick = viewModel::clearNotice) {
                                Text("关闭")
                            }
                        }
                    }
                }

                LazyColumn(
                    state = listState,
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f),
                    reverseLayout = true,
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(
                        horizontal = 16.dp,
                        vertical = 16.dp,
                    ),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    items(
                        items = reversedItems,
                        key = { item ->
                            when (item) {
                                is ChatListItem.Timestamp -> "time-${item.time}"
                                is ChatListItem.Message -> item.message.id
                            }
                        },
                    ) { item ->
                        when (item) {
                            is ChatListItem.Timestamp -> TimestampDivider(item.time)
                            is ChatListItem.Message -> MessageBubble(
                                message = item.message,
                                proactiveEnabled = viewModel.proactiveEnabled,
                                notificationsGranted = notificationsGranted,
                                locationGranted = locationGranted,
                                backgroundSettingsProfile = backgroundSettingsProfile,
                                onRetry = viewModel::retry,
                                onAction = ::handleChatAction,
                            )
                        }
                    }
                    if (viewModel.isLoadingOlder) {
                        item(key = "loading-older") {
                            Box(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(vertical = 8.dp),
                                contentAlignment = Alignment.Center,
                            ) {
                                Text(
                                    text = "加载中…",
                                    color = AuriTokens.Muted,
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                        }
                    }
                }

                MessageInputBar(
                    onSend = viewModel::send,
                    onComposingChanged = viewModel::onComposingChanged,
                )
            }
        }
    }
}

@Composable
private fun AppDrawer(
    onOpenSchedule: () -> Unit,
    onOpenHealth: () -> Unit,
    onOpenAccount: () -> Unit,
) {
    ModalDrawerSheet(
        modifier = Modifier.width(300.dp),
        drawerContainerColor = MaterialTheme.colorScheme.surface,
        drawerContentColor = MaterialTheme.colorScheme.onSurface,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                "Auri",
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.onSurface,
            )
            Spacer(modifier = Modifier.height(12.dp))
            TextButton(
                onClick = onOpenSchedule,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(
                    imageVector = Icons.Outlined.DateRange,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurface,
                )
                Spacer(modifier = Modifier.width(12.dp))
                Text("日程", color = MaterialTheme.colorScheme.onSurface)
            }
            TextButton(
                onClick = onOpenHealth,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(
                    imageVector = Icons.Outlined.FavoriteBorder,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurface,
                )
                Spacer(modifier = Modifier.width(12.dp))
                Text("健康", color = MaterialTheme.colorScheme.onSurface)
            }
            TextButton(
                onClick = onOpenAccount,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Icon(
                    imageVector = Icons.Outlined.Person,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurface,
                )
                Spacer(modifier = Modifier.width(12.dp))
                Text("账号", color = MaterialTheme.colorScheme.onSurface)
            }
        }
    }
}

@Composable
private fun TimestampDivider(time: Long) {
    Box(
        modifier = Modifier.fillMaxWidth(),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = formatWeChatTimestamp(time),
            color = AuriTokens.Muted,
            style = MaterialTheme.typography.bodySmall,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(vertical = 4.dp),
        )
    }
}

@Composable
private fun MessageBubble(
    message: ChatMessage,
    proactiveEnabled: Boolean,
    notificationsGranted: Boolean,
    locationGranted: Boolean,
    backgroundSettingsProfile: BackgroundSettingsProfile?,
    onRetry: (String) -> Unit,
    onAction: (ChatAction) -> Unit,
) {
    val alignment = if (message.isUser) Alignment.CenterEnd else Alignment.CenterStart
    val bubbleColor = if (message.isUser) {
        MaterialTheme.colorScheme.primary
    } else {
        MaterialTheme.colorScheme.surfaceVariant
    }
    val bubbleShape = if (message.isUser) {
        RoundedCornerShape(18.dp, 18.dp, 4.dp, 18.dp)
    } else {
        RoundedCornerShape(18.dp, 18.dp, 18.dp, 4.dp)
    }
    val contentColor = if (message.isUser) {
        MaterialTheme.colorScheme.onPrimary
    } else {
        MaterialTheme.colorScheme.onSurface
    }

    Box(
        modifier = Modifier.fillMaxWidth(),
        contentAlignment = alignment,
    ) {
        Surface(
            modifier = Modifier
                .widthIn(max = 340.dp),
            color = bubbleColor,
            shape = bubbleShape,
        ) {
            Column(
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 11.dp),
            ) {
                message.images.forEach { imageUrl ->
                    val bitmap = rememberDataUrlBitmap(imageUrl)
                    if (bitmap != null) {
                        Image(
                            bitmap = bitmap,
                            contentDescription = null,
                            contentScale = ContentScale.FillWidth,
                            modifier = Modifier
                                .fillMaxWidth()
                                .aspectRatio(bitmap.width.toFloat() / bitmap.height.toFloat())
                                .clip(RoundedCornerShape(8.dp))
                                .padding(bottom = 8.dp),
                        )
                    }
                }
                message.files.forEach { file ->
                    FileAttachmentChip(file = file, contentColor = contentColor)
                }
                if (message.content.isNotEmpty()) {
                    MarkdownText(
                        text = message.content,
                        modifier = Modifier.fillMaxWidth(),
                        color = contentColor,
                    )
                }
                val visibleActions = message.actions.filter {
                    it.type != "open_autostart" || backgroundSettingsProfile != null
                }
                if (visibleActions.isNotEmpty()) {
                    Spacer(modifier = Modifier.height(8.dp))
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        visibleActions.forEach { action ->
                            val visual = actionVisual(
                                action,
                                proactiveEnabled,
                                notificationsGranted,
                                locationGranted,
                                backgroundSettingsProfile,
                            )
                            ChatActionButton(
                                label = visual.label,
                                isOn = visual.isOn,
                                isToggle = visual.isToggle,
                                onClick = { onAction(action) },
                            )
                        }
                    }
                }
                if (message.isUser && message.deliveryStatus == "sending") {
                    Text(
                        "发送中…",
                        color = contentColor.copy(alpha = 0.75f),
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(top = 6.dp),
                    )
                }
                if (message.isUser && message.deliveryStatus == "failed") {
                    TextButton(onClick = { onRetry(message.id) }) {
                        Text("发送失败，点击重试", color = MaterialTheme.colorScheme.error)
                    }
                }
            }
        }
    }
}

private data class ActionVisual(
    val label: String,
    val isOn: Boolean,
    val isToggle: Boolean,
)

private fun actionVisual(
    action: ChatAction,
    proactiveEnabled: Boolean,
    notificationsGranted: Boolean,
    locationGranted: Boolean,
    backgroundSettingsProfile: BackgroundSettingsProfile?,
): ActionVisual = when (action.type) {
    "enable_proactive" -> ActionVisual(
        label = if (proactiveEnabled) "已开启主动消息" else action.label,
        isOn = proactiveEnabled,
        isToggle = true,
    )
    "request_notification" -> ActionVisual(
        label = if (notificationsGranted) "已授权通知" else action.label,
        isOn = notificationsGranted,
        isToggle = true,
    )
    "request_location" -> ActionVisual(
        label = if (locationGranted) "已授权位置" else action.label,
        isOn = locationGranted,
        isToggle = true,
    )
    "open_autostart" -> ActionVisual(
        label = backgroundSettingsProfile?.title ?: action.label,
        isOn = false,
        isToggle = false,
    )
    else -> ActionVisual(action.label, false, false)
}

@Composable
private fun ChatActionButton(
    label: String,
    isOn: Boolean,
    isToggle: Boolean,
    onClick: () -> Unit,
) {
    val interactionSource = remember { MutableInteractionSource() }
    val isPressed by interactionSource.collectIsPressedAsState()
    val scale by animateFloatAsState(
        targetValue = if (isPressed) 0.97f else 1f,
        label = "chatActionScale",
    )
    val colors = if (isToggle && isOn) {
        ButtonDefaults.buttonColors(
            containerColor = MaterialTheme.colorScheme.secondaryContainer,
            contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
        )
    } else {
        ButtonDefaults.buttonColors()
    }
    Button(
        onClick = onClick,
        modifier = Modifier
            .fillMaxWidth()
            .scale(scale),
        shape = RoundedCornerShape(12.dp),
        colors = colors,
        interactionSource = interactionSource,
        contentPadding = PaddingValues(vertical = 8.dp),
    ) {
        if (isToggle && isOn) {
            Icon(
                imageVector = Icons.Outlined.Check,
                contentDescription = null,
                modifier = Modifier.size(16.dp),
            )
            Spacer(modifier = Modifier.width(6.dp))
        }
        Text(label)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MessageInputBar(
    onSend: (String, List<Uri>, List<Uri>) -> Unit,
    onComposingChanged: (Boolean) -> Unit,
) {
    var text by rememberSaveable { mutableStateOf("") }
    var selectedImages by remember { mutableStateOf(listOf<Uri>()) }
    var selectedFiles by remember { mutableStateOf(listOf<PickedFile>()) }
    var showAttachmentSheet by remember { mutableStateOf(false) }
    val context = LocalContext.current

    val pickMedia = rememberLauncherForActivityResult(
        ActivityResultContracts.PickVisualMedia(),
    ) { uri ->
        if (uri != null && selectedImages.size < 4) {
            selectedImages = selectedImages + uri
        }
    }

    val pickFile = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri ->
        if (uri != null && selectedFiles.size < 4) {
            val (name, size) = queryFileNameSize(context, uri)
            selectedFiles = selectedFiles + PickedFile(uri, name, size)
        }
    }

    if (showAttachmentSheet) {
        ModalBottomSheet(onDismissRequest = { showAttachmentSheet = false }) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .navigationBarsPadding()
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                TextButton(
                    onClick = {
                        showAttachmentSheet = false
                        pickMedia.launch(
                            PickVisualMediaRequest(
                                ActivityResultContracts.PickVisualMedia.ImageOnly,
                            ),
                        )
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("图片")
                }
                TextButton(
                    onClick = {
                        showAttachmentSheet = false
                        pickFile.launch(arrayOf("*/*"))
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("文件")
                }
            }
        }
    }

    Surface(
        color = MaterialTheme.colorScheme.surface.copy(alpha = 0.78f),
        shadowElevation = 10.dp,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(10.dp),
        ) {
            if (selectedImages.isNotEmpty() || selectedFiles.isNotEmpty()) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(bottom = 8.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    selectedImages.forEachIndexed { index, uri ->
                        PendingImageThumbnail(
                            uri = uri,
                            onRemove = {
                                selectedImages = selectedImages.filterIndexed { i, _ -> i != index }
                            },
                        )
                    }
                    selectedFiles.forEachIndexed { index, file ->
                        PendingFileChip(
                            file = file,
                            onRemove = {
                                selectedFiles = selectedFiles.filterIndexed { i, _ -> i != index }
                            },
                        )
                    }
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                TextButton(
                    onClick = { showAttachmentSheet = true },
                    modifier = Modifier.size(48.dp),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp),
                ) {
                    Icon(
                        imageVector = Icons.Outlined.Add,
                        contentDescription = "添加附件",
                        tint = MaterialTheme.colorScheme.onSurface,
                    )
                }

                OutlinedTextField(
                    value = text,
                    onValueChange = {
                        text = it
                        onComposingChanged(it.isNotBlank())
                    },
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("输入消息") },
                    maxLines = 4,
                    shape = RoundedCornerShape(22.dp),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(
                        onSend = {
                            if (
                                text.isNotBlank() ||
                                selectedImages.isNotEmpty() ||
                                selectedFiles.isNotEmpty()
                            ) {
                                onSend(text, selectedImages, selectedFiles.map { it.uri })
                                text = ""
                                selectedImages = emptyList()
                                selectedFiles = emptyList()
                                onComposingChanged(false)
                            }
                        },
                    ),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = MaterialTheme.colorScheme.primary,
                        unfocusedBorderColor = MaterialTheme.colorScheme.outline,
                    ),
                )
                Button(
                    onClick = {
                        onSend(text, selectedImages, selectedFiles.map { it.uri })
                        text = ""
                        selectedImages = emptyList()
                        selectedFiles = emptyList()
                        onComposingChanged(false)
                    },
                    enabled = text.isNotBlank() ||
                        selectedImages.isNotEmpty() ||
                        selectedFiles.isNotEmpty(),
                    shape = CircleShape,
                    contentPadding = PaddingValues(0.dp),
                    modifier = Modifier.size(48.dp),
                ) {
                    Icon(
                        imageVector = Icons.AutoMirrored.Outlined.Send,
                        contentDescription = "发送",
                        modifier = Modifier.size(20.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun PendingImageThumbnail(uri: Uri, onRemove: () -> Unit) {
    val bitmap = rememberUriBitmap(uri)
    Box(modifier = Modifier.size(64.dp)) {
        if (bitmap != null) {
            Image(
                bitmap = bitmap,
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier
                    .fillMaxSize()
                    .clip(RoundedCornerShape(8.dp)),
            )
        } else {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(
                        MaterialTheme.colorScheme.surfaceVariant,
                        RoundedCornerShape(8.dp),
                    ),
            )
        }
        Box(
            modifier = Modifier
                .align(Alignment.TopEnd)
                .size(32.dp)
                .background(
                    Color.Black.copy(alpha = 0.55f),
                    RoundedCornerShape(16.dp),
                )
                .clickable(onClick = onRemove),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = Icons.Outlined.Close,
                contentDescription = "移除",
                tint = Color.White,
                modifier = Modifier.size(16.dp),
            )
        }
    }
}

@Composable
private fun rememberUriBitmap(uri: Uri): ImageBitmap? {
    val context = LocalContext.current
    return produceState<ImageBitmap?>(initialValue = null, uri) {
        value = withContext(Dispatchers.IO) {
            runCatching {
                val options = BitmapFactory.Options().apply { inSampleSize = 4 }
                context.contentResolver.openInputStream(uri)?.use {
                    BitmapFactory.decodeStream(it, null, options)?.asImageBitmap()
                }
            }.getOrNull()
        }
    }.value
}

@Composable
private fun rememberDataUrlBitmap(dataUrl: String): ImageBitmap? {
    return produceState<ImageBitmap?>(initialValue = null, dataUrl) {
        value = withContext(Dispatchers.IO) {
            runCatching {
                val base64 = dataUrl.substringAfter("base64,", "")
                val bytes = Base64.decode(base64, Base64.NO_WRAP)
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
            }.getOrNull()
        }
    }.value
}

private data class PickedFile(
    val uri: Uri,
    val name: String,
    val size: Long,
)

private fun queryFileNameSize(context: Context, uri: Uri): Pair<String, Long> {
    var name = "文件"
    var size = -1L
    context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
        if (cursor.moveToFirst()) {
            val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (nameIndex >= 0) {
                cursor.getString(nameIndex)?.takeIf { it.isNotBlank() }?.let { name = it }
            }
            val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
            if (sizeIndex >= 0 && !cursor.isNull(sizeIndex)) {
                size = cursor.getLong(sizeIndex)
            }
        }
    }
    return name to size
}

private fun formatFileSize(bytes: Long): String {
    if (bytes <= 0) return ""
    if (bytes < 1024) return "$bytes B"
    if (bytes < 1024 * 1024) return "${bytes / 1024} KB"
    return String.format(Locale.getDefault(), "%.1f MB", bytes / 1024.0 / 1024.0)
}

@Composable
private fun PendingFileChip(file: PickedFile, onRemove: () -> Unit) {
    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .padding(horizontal = 10.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = file.name,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.widthIn(max = 140.dp),
            style = MaterialTheme.typography.bodySmall,
        )
        Box(
            modifier = Modifier
                .size(32.dp)
                .clickable(onClick = onRemove),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = Icons.Outlined.Close,
                contentDescription = "移除",
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(16.dp),
            )
        }
    }
}

@Composable
private fun FileAttachmentChip(file: ChatFile, contentColor: Color) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 6.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(Color.Black.copy(alpha = 0.10f))
            .padding(10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Icon(
            imageVector = Icons.AutoMirrored.Outlined.List,
            contentDescription = null,
            tint = contentColor,
            modifier = Modifier.size(20.dp),
        )
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = file.name,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                color = contentColor,
                style = MaterialTheme.typography.bodyMedium,
            )
            if (file.size > 0) {
                Text(
                    text = formatFileSize(file.size),
                    color = contentColor.copy(alpha = 0.7f),
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
    }
}

private fun buildChatDisplayItems(messages: List<ChatMessage>): List<ChatListItem> {
    val result = mutableListOf<ChatListItem>()
    var lastShownTime: Long? = null

    messages.forEach { message ->
        val time = message.timestamp
        if (
            lastShownTime == null ||
            time - lastShownTime!! >= 5 * 60 * 1000L ||
            !isSameDay(time, lastShownTime!!)
        ) {
            result += ChatListItem.Timestamp(time)
            lastShownTime = time
        }
        result += ChatListItem.Message(message)
    }

    return result
}

private fun isSameDay(first: Long, second: Long): Boolean {
    val firstCalendar = Calendar.getInstance().apply { timeInMillis = first }
    val secondCalendar = Calendar.getInstance().apply { timeInMillis = second }
    return firstCalendar.get(Calendar.YEAR) == secondCalendar.get(Calendar.YEAR) &&
        firstCalendar.get(Calendar.DAY_OF_YEAR) == secondCalendar.get(Calendar.DAY_OF_YEAR)
}

private fun formatWeChatTimestamp(timestamp: Long): String {
    val now = Calendar.getInstance()
    val target = Calendar.getInstance().apply { timeInMillis = timestamp }
    val timeText = SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(timestamp))

    return when {
        isSameDay(timestamp, now.timeInMillis) -> timeText
        isYesterday(target, now) -> "昨天 $timeText"
        target.after(
            (now.clone() as Calendar).apply { add(Calendar.DAY_OF_YEAR, -7) },
        ) -> {
            "${weekdayLabel(target.get(Calendar.DAY_OF_WEEK))} $timeText"
        }
        else -> SimpleDateFormat("yyyy年M月d日 HH:mm", Locale.CHINA).format(Date(timestamp))
    }
}

private fun isYesterday(target: Calendar, now: Calendar): Boolean {
    val yesterday = now.clone() as Calendar
    yesterday.add(Calendar.DAY_OF_YEAR, -1)
    return target.get(Calendar.YEAR) == yesterday.get(Calendar.YEAR) &&
        target.get(Calendar.DAY_OF_YEAR) == yesterday.get(Calendar.DAY_OF_YEAR)
}

private fun weekdayLabel(dayOfWeek: Int): String {
    return when (dayOfWeek) {
        Calendar.MONDAY -> "星期一"
        Calendar.TUESDAY -> "星期二"
        Calendar.WEDNESDAY -> "星期三"
        Calendar.THURSDAY -> "星期四"
        Calendar.FRIDAY -> "星期五"
        Calendar.SATURDAY -> "星期六"
        Calendar.SUNDAY -> "星期日"
        else -> ""
    }
}
