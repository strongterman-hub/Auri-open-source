package com.auri.chat

import android.app.Application
import android.content.ContentResolver
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.net.Uri
import android.os.Build
import android.provider.OpenableColumns
import android.util.Log
import android.util.Base64
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import cn.jpush.android.api.JPushInterface
import kotlinx.coroutines.delay
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.time.Instant
import java.time.ZoneId
import java.util.UUID

private const val PAGE_SIZE = 10

private fun deviceTimezoneId(): String = ZoneId.systemDefault().id

internal fun normalizeOptionalNotice(value: String?): String? =
    value
        ?.trim()
        ?.takeIf { it.isNotEmpty() && !it.equals("null", ignoreCase = true) }

data class ChatMessage(
    val id: String,
    val role: String,
    val content: String,
    val images: List<String> = emptyList(),
    val files: List<ChatFile> = emptyList(),
    val actions: List<ChatAction> = emptyList(),
    val isUser: Boolean,
    val isError: Boolean = false,
    val deliveryStatus: String = "sent",
    val pendingPayload: String = "",
    val timestamp: Long = System.currentTimeMillis(),
)

data class ChatFile(
    val id: String,
    val name: String,
    val mime: String,
    val size: Long,
)

data class ChatAction(
    val type: String,
    val label: String,
)

private fun encodeImages(images: List<String>): String {
    if (images.isEmpty()) return "[]"
    val array = JSONArray()
    images.forEach { array.put(it) }
    return array.toString()
}

private fun decodeImages(json: String): List<String> {
    if (json.isBlank()) return emptyList()
    return runCatching {
        val array = JSONArray(json)
        buildList {
            for (index in 0 until array.length()) {
                array.optString(index).takeIf { it.isNotBlank() }?.let { add(it) }
            }
        }
    }.getOrDefault(emptyList())
}

private fun encodeFiles(files: List<ChatFile>): String {
    if (files.isEmpty()) return "[]"
    val array = JSONArray()
    files.forEach { file ->
        array.put(
            JSONObject()
                .put("id", file.id)
                .put("name", file.name)
                .put("mime", file.mime)
                .put("size", file.size),
        )
    }
    return array.toString()
}

private fun decodeFiles(json: String): List<ChatFile> {
    if (json.isBlank()) return emptyList()
    return runCatching {
        val array = JSONArray(json)
        buildList {
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                add(
                    ChatFile(
                        id = item.optString("id"),
                        name = item.optString("name"),
                        mime = item.optString("mime"),
                        size = item.optLong("size"),
                    ),
                )
            }
        }
    }.getOrDefault(emptyList())
}

private fun encodeActions(actions: List<ChatAction>): String {
    if (actions.isEmpty()) return "[]"
    val array = JSONArray()
    actions.forEach { action ->
        array.put(
            JSONObject()
                .put("type", action.type)
                .put("label", action.label),
        )
    }
    return array.toString()
}

private fun decodeActions(json: String): List<ChatAction> {
    if (json.isBlank()) return emptyList()
    return runCatching {
        val array = JSONArray(json)
        buildList {
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index) ?: continue
                add(
                    ChatAction(
                        type = item.optString("type"),
                        label = item.optString("label"),
                    ),
                )
            }
        }
    }.getOrDefault(emptyList())
}

private fun ChatMessage.toEntity(): ChatMessageEntity = ChatMessageEntity(
    id = id,
    role = role,
    content = content,
    images = encodeImages(images),
    files = encodeFiles(files),
    actions = encodeActions(actions),
    isUser = isUser,
    isError = isError,
    deliveryStatus = deliveryStatus,
    pendingPayload = pendingPayload,
    timestamp = timestamp,
)

private fun ChatMessageEntity.toMessage(): ChatMessage = ChatMessage(
    id = id,
    role = role,
    content = content,
    images = decodeImages(images),
    files = decodeFiles(files),
    actions = decodeActions(actions),
    isUser = isUser,
    isError = isError,
    deliveryStatus = deliveryStatus,
    pendingPayload = pendingPayload,
    timestamp = timestamp,
)

private data class PendingOutgoing(
    val content: String,
    val images: List<String>,
    val files: List<FileUpload>,
)

private fun encodePendingOutgoing(
    content: String,
    images: List<String>,
    files: List<FileUpload>,
): String {
    val fileArray = JSONArray()
    files.forEach { file ->
        fileArray.put(
            JSONObject()
                .put("name", file.name)
                .put("mime", file.mime)
                .put("data", file.data)
                .put("size", file.size),
        )
    }
    return JSONObject()
        .put("content", content)
        .put("images", JSONArray(images))
        .put("files", fileArray)
        .toString()
}

private fun decodePendingOutgoing(payload: String): PendingOutgoing? = runCatching {
    val json = JSONObject(payload)
    val images = buildList {
        val array = json.optJSONArray("images") ?: JSONArray()
        for (index in 0 until array.length()) {
            array.optString(index).takeIf { it.isNotBlank() }?.let(::add)
        }
    }
    val files = buildList {
        val array = json.optJSONArray("files") ?: JSONArray()
        for (index in 0 until array.length()) {
            val item = array.optJSONObject(index) ?: continue
            add(
                FileUpload(
                    name = item.optString("name", "file"),
                    mime = item.optString("mime", "application/octet-stream"),
                    data = item.optString("data"),
                    size = item.optLong("size"),
                ),
            )
        }
    }
    PendingOutgoing(json.optString("content"), images, files)
}.getOrNull()

private const val MAX_IMAGE_DIMENSION = 1600
private const val IMAGE_JPEG_QUALITY = 85
private const val MAX_FILE_BYTES = 8 * 1024 * 1024

private fun encodeImage(context: Context, uri: Uri): String {
    val resolver = context.contentResolver
    val bitmap = decodeSampledBitmap(resolver, uri, MAX_IMAGE_DIMENSION)
        ?: throw IllegalStateException("无法读取图片")
    val output = ByteArrayOutputStream()
    bitmap.compress(Bitmap.CompressFormat.JPEG, IMAGE_JPEG_QUALITY, output)
    return "data:image/jpeg;base64," +
        Base64.encodeToString(output.toByteArray(), Base64.NO_WRAP)
}

private fun decodeSampledBitmap(
    resolver: ContentResolver,
    uri: Uri,
    maxDimension: Int,
): Bitmap? {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
        return try {
            ImageDecoder.decodeBitmap(
                ImageDecoder.createSource(resolver, uri),
            ) { decoder, info, _ ->
                decoder.setTargetSampleSize(
                    sampleSize(info.size.width, info.size.height, maxDimension),
                )
            }
        } catch (_: Exception) {
            null
        }
    }

    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
    if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null

    val options = BitmapFactory.Options().apply {
        inSampleSize = sampleSize(bounds.outWidth, bounds.outHeight, maxDimension)
    }
    return resolver.openInputStream(uri)?.use {
        BitmapFactory.decodeStream(it, null, options)
    }
}

private fun sampleSize(width: Int, height: Int, maxDimension: Int): Int {
    if (width <= 0 || height <= 0) return 1
    var sample = 1
    var w = width
    var h = height
    while (w > maxDimension || h > maxDimension) {
        sample *= 2
        w /= 2
        h /= 2
    }
    return sample
}

private fun readFileAttachment(context: Context, uri: Uri): FileUpload {
    val resolver = context.contentResolver
    val mime = resolver.getType(uri) ?: "application/octet-stream"
    var name = "file"
    var size = -1L

    resolver.query(uri, null, null, null, null)?.use { cursor ->
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

    val bytes = resolver.openInputStream(uri)?.use { it.readBytes() }
        ?: throw IllegalStateException("无法读取文件")
    if (bytes.size > MAX_FILE_BYTES) {
        throw IllegalStateException("文件过大，最大支持 8 MB")
    }

    val resolvedSize = if (size > 0) size else bytes.size.toLong()
    return FileUpload(
        name = name,
        mime = mime,
        data = Base64.encodeToString(bytes, Base64.NO_WRAP),
        size = resolvedSize,
    )
}

class ChatViewModel(application: Application) : AndroidViewModel(application) {
    private val api = AuriApi()
    private val sessionStore = SessionStore(application)
    private val authStore = AuthStore(application)
    private val deviceStore = DeviceStore(application)
    private val locationProvider = LocationProvider(application)
    private val messageDao = AuriDatabase.get(application).chatMessageDao()
    @Volatile
    private var chatVisible = false
    @Volatile
    private var appInForeground = true
    @Volatile
    private var isRefreshing = false
    private var hasMore = false
    private var oldestServerMessageId: String? = null
    private var latestServerMessageId: String? = null
    @Volatile
    private var nextChatPollMs = CHAT_IDLE_POLL_MS
    @Volatile
    private var isComposing = false

    private val _messages = mutableStateListOf<ChatMessage>()
    val messages: List<ChatMessage> get() = _messages

    var replyState by mutableStateOf("idle")
        private set

    val isAuriTyping: Boolean get() = replyState == "typing"

    var error by mutableStateOf<String?>(null)
        private set

    var notice by mutableStateOf<String?>(null)
        private set

    var isLoadingOlder by mutableStateOf(false)
        private set

    var proactiveEnabled by mutableStateOf(false)
        private set

    init {
        viewModelScope.launch {
            val cached = withContext(Dispatchers.IO) { messageDao.getAll() }
            if (cached.isNotEmpty()) {
                _messages.addAll(cached.map { it.toMessage() })
            }
            refreshFromServer()
            refreshProactiveEnabled()
            runCatching { heartbeatAndPollInbox() }
                .onFailure { Log.e("AuriApi", "proactive poll failed", it) }
            runCatching { pollChatUpdates() }
                .onFailure { Log.e("AuriApi", "chat update poll failed", it) }
        }
        viewModelScope.launch {
            while (isActive) {
                delay(PROACTIVE_POLL_MS)
                if (appInForeground) {
                    if (chatVisible) {
                        runCatching { heartbeatAndPollInbox() }
                            .onFailure { Log.e("AuriApi", "proactive poll failed", it) }
                    }
                    runCatching { ensureDeviceRegistered() }
                        .onFailure { Log.e("AuriApi", "device registration failed", it) }
                }
            }
        }
        viewModelScope.launch {
            while (isActive) {
                delay(nextChatPollMs)
                if (appInForeground && chatVisible) {
                    runCatching { pollChatUpdates() }
                        .onFailure {
                            nextChatPollMs = CHAT_QUEUED_POLL_MS
                            Log.e("AuriApi", "chat update poll failed", it)
                        }
                }
            }
        }
        viewModelScope.launch {
            delay(LOCATION_REPORT_INITIAL_DELAY_MS)
            while (isActive) {
                runCatching { reportLocation() }
                    .onFailure { Log.e("AuriApi", "location report failed", it) }
                delay(LOCATION_REPORT_MS)
            }
        }
    }

    fun setChatVisible(visible: Boolean) {
        chatVisible = visible
    }

    fun setAppInForeground(foreground: Boolean) {
        appInForeground = foreground
    }

    fun onComposingChanged(hasText: Boolean) {
        isComposing = hasText
        if (hasText) {
            JPushInterface.clearAllNotifications(getApplication())
        }
    }

    private suspend fun syncFromServer() {
        val token = authStore.getToken() ?: return
        val sessionId = resolveActiveSessionId() ?: return
        val json = withContext(Dispatchers.IO) {
            api.getMessages(sessionId, token, null, PAGE_SIZE)
        }
        hasMore = json.optBoolean("has_more")
        val serverMessages = parseServerMessages(json.optJSONArray("messages"))
        val unresolved = _messages.filter { local ->
            local.isUser && local.deliveryStatus != "sent" &&
                serverMessages.none { it.id == local.id }
        }
        val merged = (serverMessages + unresolved).sortedBy { it.timestamp }
        withContext(Dispatchers.IO) {
            messageDao.clearAll()
            messageDao.upsertAll(merged.map { it.toEntity() })
        }
        _messages.clear()
        _messages.addAll(merged)
        oldestServerMessageId = serverMessages.firstOrNull()?.id
        latestServerMessageId = serverMessages.lastOrNull()?.id
    }

    fun refreshFromServer() {
        if (isRefreshing) return
        isRefreshing = true
        viewModelScope.launch {
            try {
                syncFromServer()
            } catch (exception: Exception) {
                Log.e("AuriApi", "refreshFromServer failed", exception)
            } finally {
                isRefreshing = false
            }
        }
    }

    fun loadOlder() {
        if (isLoadingOlder || !hasMore) return
        val token = authStore.getToken() ?: return
        val beforeId = oldestServerMessageId ?: return
        viewModelScope.launch {
            isLoadingOlder = true
            try {
                val sessionId = resolveActiveSessionId() ?: return@launch
                val json = withContext(Dispatchers.IO) {
                    api.getMessages(sessionId, token, beforeId, PAGE_SIZE)
                }
                hasMore = json.optBoolean("has_more")
                val older = parseServerMessages(json.optJSONArray("messages"))
                if (older.isNotEmpty()) {
                    _messages.addAll(0, older)
                    withContext(Dispatchers.IO) {
                        messageDao.upsertAll(older.map { it.toEntity() })
                    }
                    oldestServerMessageId = older.first().id
                }
            } catch (exception: Exception) {
                Log.e("AuriApi", "loadOlder failed", exception)
            } finally {
                isLoadingOlder = false
            }
        }
    }

    private fun parseServerMessages(array: JSONArray?): List<ChatMessage> {
        if (array == null) return emptyList()
        return buildList {
            for (index in 0 until array.length()) {
                val item = array.getJSONObject(index)
                val role = item.optString("role", "assistant")
                val parsed = parseServerMessageContent(item)
                add(
                    ChatMessage(
                        id = item.optString("id").ifBlank { "server-${System.nanoTime()}-$index" },
                        role = role,
                        content = parsed.text,
                        images = parsed.images,
                        files = parsed.files,
                        actions = parsed.actions,
                        isUser = role == "user",
                        isError = false,
                        timestamp = parseTimestamp(item.optString("timestamp")),
                    ),
                )
            }
        }
    }

    private data class ParsedContent(
        val text: String,
        val images: List<String>,
        val files: List<ChatFile>,
        val actions: List<ChatAction>,
    )

    private fun parseServerMessageContent(item: JSONObject): ParsedContent {
        val content = item.opt("content")
        if (content is String) {
            return ParsedContent(content, emptyList(), emptyList(), emptyList())
        }
        if (content is JSONArray) {
            val text = StringBuilder()
            val images = mutableListOf<String>()
            val files = mutableListOf<ChatFile>()
            val actions = mutableListOf<ChatAction>()
            for (index in 0 until content.length()) {
                val part = content.optJSONObject(index) ?: continue
                when (part.optString("type")) {
                    "text" -> text.append(part.optString("text"))
                    "image_url" -> part.optJSONObject("image_url")
                        ?.optString("url")
                        ?.takeIf { it.isNotBlank() }
                        ?.let { images.add(it) }
                    "file" -> part.optJSONObject("file")?.let { file ->
                        files.add(
                            ChatFile(
                                id = file.optString("id"),
                                name = file.optString("name"),
                                mime = file.optString("mime"),
                                size = file.optLong("size"),
                            ),
                        )
                    }
                    "actions" -> part.optJSONArray("actions")?.let { array ->
                        for (actionIndex in 0 until array.length()) {
                            val action = array.optJSONObject(actionIndex) ?: continue
                            actions.add(
                                ChatAction(
                                    type = action.optString("type"),
                                    label = action.optString("label"),
                                ),
                            )
                        }
                    }
                }
            }
            return ParsedContent(text.toString(), images, files, actions)
        }
        return ParsedContent("", emptyList(), emptyList(), emptyList())
    }

    private fun parseTimestamp(iso: String): Long =
        runCatching { Instant.parse(iso).toEpochMilli() }.getOrDefault(System.currentTimeMillis())

    fun send(
        text: String,
        imageUris: List<Uri> = emptyList(),
        fileUris: List<Uri> = emptyList(),
    ) {
        val content = text.trim()
        if (content.isEmpty() && imageUris.isEmpty() && fileUris.isEmpty()) return

        error = null

        viewModelScope.launch {
            val prepared = try {
                withContext(Dispatchers.IO) {
                    val images = imageUris.map { encodeImage(getApplication(), it) }
                    val files = fileUris.map { readFileAttachment(getApplication(), it) }
                    images to files
                }
            } catch (exception: Exception) {
                error = "附件处理失败：${exception.message ?: "未知错误"}"
                return@launch
            }
            sendEncoded(content, prepared.first, prepared.second)
        }
    }

    private fun sendEncoded(
        content: String,
        images: List<String>,
        files: List<FileUpload>,
    ) {
        error = null

        val token = authStore.getToken()
        if (token == null) {
            error = "请先登录"
            return
        }

        val chatFiles = files.map {
            ChatFile(
                id = "local-${System.nanoTime()}",
                name = it.name,
                mime = it.mime,
                size = it.size,
            )
        }

        val messageId = UUID.randomUUID().toString()
        val pendingPayload = encodePendingOutgoing(content, images, files)

        addMessage(
            ChatMessage(
                id = messageId,
                role = "user",
                content = content,
                images = images,
                files = chatFiles,
                isUser = true,
                deliveryStatus = "sending",
                pendingPayload = pendingPayload,
                timestamp = System.currentTimeMillis(),
            ),
        )
        viewModelScope.launch {
            submitPendingMessage(messageId, token)
        }
    }

    private suspend fun submitPendingMessage(messageId: String, token: String) {
        val local = _messages.firstOrNull { it.id == messageId } ?: return
        val outgoing = decodePendingOutgoing(local.pendingPayload) ?: run {
            updateMessage(messageId) { it.copy(deliveryStatus = "failed") }
            return
        }
        updateMessage(messageId) { it.copy(deliveryStatus = "sending", isError = false) }
        try {
            val sessionId = resolveActiveSessionId()
                ?: throw IllegalStateException("无法创建会话")
            val result = withContext(Dispatchers.IO) {
                api.sendAsyncMessage(
                    sessionId,
                    messageId,
                    outgoing.content,
                    token,
                    outgoing.images,
                    outgoing.files,
                )
            }
            val acceptedSessionId = result.optString("session_id", sessionId)
            sessionStore.saveSessionId(authStore.getEmail().orEmpty(), acceptedSessionId)
            val resetNotice = if (result.isNull("reset_notice")) {
                null
            } else {
                result.optString("reset_notice")
            }
            normalizeOptionalNotice(resetNotice)?.let { notice = it }
            replyState = result.optString("reply_state", "queued")
            nextChatPollMs = CHAT_ACTIVE_POLL_MS
            val canonical = result.optJSONObject("message")?.let { messageJson ->
                parseServerMessages(JSONArray().put(messageJson)).firstOrNull()
            }
            updateMessage(messageId) {
                (canonical ?: it).copy(
                    deliveryStatus = "sent",
                    pendingPayload = "",
                    isError = false,
                )
            }
            runCatching { pollChatUpdates() }
                .onFailure { Log.e("AuriApi", "post-send chat poll failed", it) }
        } catch (exception: Exception) {
            Log.e("AuriApi", "message delivery failed", exception)
            error = exception.message ?: "发送失败，请稍后重试"
            updateMessage(messageId) {
                it.copy(deliveryStatus = "failed", isError = true)
            }
        }
    }

    fun retry(messageId: String) {
        val message = _messages.firstOrNull { it.id == messageId } ?: return
        if (!message.isUser || message.deliveryStatus != "failed") return
        val token = authStore.getToken()
        if (token == null) {
            error = "请先登录"
            return
        }
        updateMessage(messageId) { it.copy(deliveryStatus = "sending", isError = false) }
        viewModelScope.launch { submitPendingMessage(messageId, token) }
    }

    fun clearError() {
        error = null
    }

    fun clearNotice() {
        notice = null
    }

    fun refreshProactiveEnabled() {
        val token = authStore.getToken() ?: return
        viewModelScope.launch {
            val enabled = withContext(Dispatchers.IO) {
                runCatching {
                    api.getProactiveSettings(token).optBoolean("enabled", false)
                }.getOrDefault(proactiveEnabled)
            }
            proactiveEnabled = enabled
            getApplication<Application>()
                .getSharedPreferences("auri_settings", Context.MODE_PRIVATE)
                .edit()
                .putBoolean("proactive_enabled", enabled)
                .apply()
        }
    }

    fun enableProactive() {
        val token = authStore.getToken() ?: return
        getApplication<Application>()
            .getSharedPreferences("auri_settings", Context.MODE_PRIVATE)
            .edit()
            .putBoolean("proactive_enabled", true)
            .apply()
        proactiveEnabled = true
        viewModelScope.launch {
            withContext(Dispatchers.IO) {
                runCatching { api.updateProactiveSettings(true, token) }
            }.onFailure {
                proactiveEnabled = false
                getApplication<Application>()
                    .getSharedPreferences("auri_settings", Context.MODE_PRIVATE)
                    .edit()
                    .putBoolean("proactive_enabled", false)
                    .apply()
                notice = "主动消息开启失败，请到账号中心重试"
            }
        }
    }

    fun reportGuideActionCompleted(type: String) {
        val token = authStore.getToken() ?: return
        viewModelScope.launch {
            withContext(Dispatchers.IO) {
                runCatching { api.reportGuideActionCompleted(type, token) }
            }
        }
    }

    fun clearConversation() {
        viewModelScope.launch {
            val token = authStore.getToken()
            val userKey = authStore.getEmail().orEmpty()
            val sessionId = sessionStore.getSessionId(userKey)
            if (token != null && sessionId != null) {
                withContext(Dispatchers.IO) { runCatching { api.deleteSession(sessionId, token) } }
            }
            sessionStore.clearSession(userKey)
            withContext(Dispatchers.IO) { messageDao.clearAll() }
            _messages.clear()
            oldestServerMessageId = null
            latestServerMessageId = null
            replyState = "idle"
            runCatching { heartbeatAndPollInbox() }
                .onFailure { Log.e("AuriApi", "proactive poll after clear failed", it) }
        }
    }

    private suspend fun resolveActiveSessionId(): String? {
        val token = authStore.getToken() ?: return null
        val userKey = authStore.getEmail() ?: return null
        sessionStore.getSessionId(userKey)?.let { return it }
        return withContext(Dispatchers.IO) {
            runCatching { api.ensureSession("mobile-user", token) }
                .onSuccess { sessionStore.saveSessionId(userKey, it) }
                .onFailure { Log.e("AuriApi", "ensureSession failed", it) }
                .getOrNull()
        }
    }

    private suspend fun pollChatUpdates() {
        val token = authStore.getToken() ?: return
        val sessionId = resolveActiveSessionId() ?: return
        val json = withContext(Dispatchers.IO) {
            api.getChatUpdates(sessionId, token, latestServerMessageId)
        }
        replyState = json.optString("reply_state", "idle")
        nextChatPollMs = json.optLong("next_poll_ms", CHAT_IDLE_POLL_MS)
            .coerceIn(CHAT_ACTIVE_POLL_MS, CHAT_IDLE_POLL_MS)

        val incoming = parseServerMessages(json.optJSONArray("messages"))
        val existingIds = _messages.mapTo(mutableSetOf()) { it.id }
        if (incoming.isNotEmpty()) {
            mergeMessages(incoming)
            latestServerMessageId = incoming.last().id
            if (isComposing && incoming.any { !it.isUser && it.id !in existingIds }) {
                JPushInterface.clearAllNotifications(getApplication())
            }
        }

        val commands = json.optJSONArray("commands") ?: JSONArray()
        for (index in 0 until commands.length()) {
            val command = commands.optJSONObject(index) ?: continue
            if (command.optString("type") == "request_location") {
                command.optString("request_id").takeIf { it.isNotBlank() }?.let { requestId ->
                    viewModelScope.launch { reportFreshLocation(requestId) }
                }
            }
        }
    }

    private suspend fun heartbeatAndPollInbox() {
        val token = authStore.getToken() ?: return
        val inbox = withContext(Dispatchers.IO) {
            runCatching {
                api.heartbeat(token, deviceTimezoneId())
                api.getProactiveInbox(token)
            }
        }.getOrNull() ?: return

        val messagesArray = inbox.optJSONArray("messages") ?: return
        val newMessages = mutableListOf<ChatMessage>()
        val ids = mutableListOf<String>()
        for (index in 0 until messagesArray.length()) {
            val item = messagesArray.getJSONObject(index)
            val id = item.optString("id")
            if (id.isBlank()) continue
            ids.add(id)
            if (_messages.none { it.id == id }) {
                newMessages.add(
                    ChatMessage(
                        id = id,
                        role = "assistant",
                        content = item.optString("content", ""),
                        actions = decodeActions(
                            item.optJSONArray("actions")?.toString() ?: "[]",
                        ),
                        isUser = false,
                        isError = false,
                        timestamp = parseTimestamp(item.optString("decided_at")),
                    ),
                )
            }
        }

        if (newMessages.isNotEmpty()) {
            mergeMessages(newMessages)
            if (isComposing) {
                JPushInterface.clearAllNotifications(getApplication())
            }
        }
        if (ids.isNotEmpty()) {
            withContext(Dispatchers.IO) {
                runCatching { api.acknowledgeProactive(ids, token) }
            }
        }
    }

    private suspend fun ensureDeviceRegistered() {
        val token = authStore.getToken() ?: return
        val registrationId = deviceStore.getRegistrationId()
            ?: JPushInterface.getRegistrationID(getApplication())?.takeIf { it.isNotBlank() }
            ?: return
        if (authStore.getRegisteredDeviceToken() == registrationId) return

        val registered = withContext(Dispatchers.IO) {
            runCatching { api.registerDevice(registrationId, token) }.isSuccess
        }
        if (registered) {
            deviceStore.saveRegistrationId(registrationId)
            authStore.saveRegisteredDeviceToken(registrationId)
        }
    }

    private suspend fun reportLocation() {
        val token = authStore.getToken() ?: return
        val location = withContext(Dispatchers.IO) {
            locationProvider.currentLocation()
        } ?: return
        withContext(Dispatchers.IO) {
            runCatching {
                api.reportLocation(
                    location.latitude,
                    location.longitude,
                    token,
                    deviceTimezoneId(),
                )
            }
        }
    }

    private suspend fun reportFreshLocation(requestId: String? = null) {
        val token = authStore.getToken() ?: return
        val location = withContext(Dispatchers.IO) {
            locationProvider.freshLocation()
        } ?: return
        withContext(Dispatchers.IO) {
            runCatching {
                api.reportLocation(
                    location.latitude,
                    location.longitude,
                    token,
                    deviceTimezoneId(),
                    requestId,
                )
            }
        }
    }

    private fun addMessage(message: ChatMessage) {
        _messages.add(message)
        viewModelScope.launch {
            withContext(Dispatchers.IO) { messageDao.upsert(message.toEntity()) }
        }
    }

    private fun updateMessage(id: String, transform: (ChatMessage) -> ChatMessage) {
        val index = _messages.indexOfFirst { it.id == id }
        if (index >= 0) {
            val updated = transform(_messages[index])
            _messages[index] = updated
            viewModelScope.launch {
                withContext(Dispatchers.IO) { messageDao.upsert(updated.toEntity()) }
            }
        }
    }

    private suspend fun mergeMessages(messages: List<ChatMessage>) {
        messages.forEach { message ->
            val index = _messages.indexOfFirst { it.id == message.id }
            if (index >= 0) {
                _messages[index] = message
            } else {
                _messages.add(message)
            }
        }
        _messages.sortBy { it.timestamp }
        withContext(Dispatchers.IO) {
            messageDao.upsertAll(messages.map { it.toEntity() })
        }
    }

    companion object {
        private const val PROACTIVE_POLL_MS = 15_000L
        private const val CHAT_ACTIVE_POLL_MS = 1_000L
        private const val CHAT_QUEUED_POLL_MS = 2_000L
        private const val CHAT_IDLE_POLL_MS = 10_000L
        private const val LOCATION_REPORT_INITIAL_DELAY_MS = 5_000L
        private const val LOCATION_REPORT_MS = 5 * 60_000L
    }
}
