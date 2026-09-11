package com.auri.chat

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.net.Uri
import android.os.Build
import android.provider.Settings
import cn.jpush.android.api.DefaultPushNotificationBuilder
import cn.jpush.android.api.JPushInterface
import cn.jpush.android.api.NotificationMessage

private const val PREFS_NAME = "auri_settings"
private const val SOUND_KEY = "notification_sound"
private const val SOUND_SYSTEM = "system"

const val ACTION_OPEN_CHAT = "com.auri.chat.action.OPEN_CHAT"
const val ACTION_CHECK_UPDATE = "com.auri.chat.action.CHECK_UPDATE"
const val AURI_MESSAGES_CHANNEL_ID = "auri_messages"

private data class SoundRef(val key: String, val rawResId: Int?)

private val soundRefs = listOf(
    SoundRef(SOUND_SYSTEM, null),
    SoundRef("android", R.raw.auri_tone_android),
    SoundRef("gentle", R.raw.auri_tone_gentle),
    SoundRef("short", R.raw.auri_tone_short),
    SoundRef("classic", R.raw.auri_tone_classic),
)

fun selectedNotificationSoundUri(context: Context): Uri {
    val selected = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        .getString(SOUND_KEY, SOUND_SYSTEM) ?: SOUND_SYSTEM
    val ref = soundRefs.firstOrNull { it.key == selected } ?: soundRefs.first()
    return if (ref.rawResId != null) {
        Uri.parse("android.resource://${context.packageName}/${ref.rawResId}")
    } else {
        Settings.System.DEFAULT_NOTIFICATION_URI
    }
}

fun applyPushNotificationSound(context: Context, recreateChannel: Boolean = false) {
    ensureAuriMessagesChannel(context, recreateChannel)
    JPushInterface.setDefaultPushNotificationBuilder(
        AuriSoundPushNotificationBuilder(context, selectedNotificationSoundUri(context)),
    )
}

private fun ensureAuriMessagesChannel(context: Context, recreate: Boolean) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
    val manager = context.getSystemService(NotificationManager::class.java)
    if (recreate) manager.deleteNotificationChannel(AURI_MESSAGES_CHANNEL_ID)
    if (manager.getNotificationChannel(AURI_MESSAGES_CHANNEL_ID) != null) return
    val soundUri = selectedNotificationSoundUri(context)
    val audioAttributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_NOTIFICATION)
        .build()
    val channel = NotificationChannel(
        AURI_MESSAGES_CHANNEL_ID,
        "Auri 消息",
        NotificationManager.IMPORTANCE_DEFAULT,
    ).apply {
        description = "Auri 的聊天回复、主动消息与日程提醒"
        setSound(soundUri, audioAttributes)
        enableVibration(true)
        setShowBadge(true)
    }
    manager.createNotificationChannel(channel)
}

fun openChatIntent(context: Context): Intent =
    Intent(context, MainActivity::class.java).apply {
        action = ACTION_OPEN_CHAT
        flags = Intent.FLAG_ACTIVITY_NEW_TASK or
            Intent.FLAG_ACTIVITY_CLEAR_TOP or
            Intent.FLAG_ACTIVITY_SINGLE_TOP
    }

private fun chatPendingIntent(context: Context): PendingIntent =
    PendingIntent.getActivity(
        context,
        0,
        openChatIntent(context),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

private fun applyChatIntent(context: Context, notification: Notification): Notification =
    notification.apply {
        contentIntent = chatPendingIntent(context)
        flags = flags or Notification.FLAG_AUTO_CANCEL
    }

@Suppress("DEPRECATION")
private class AuriSoundPushNotificationBuilder(
    appContext: Context,
    private val soundUri: Uri,
) : DefaultPushNotificationBuilder() {
    init {
        context = appContext
    }

    override fun buildNotification(
        context: Context,
        message: NotificationMessage,
    ): Notification {
        val notification = super.buildNotification(context, message).apply { sound = soundUri }
        return applyChatIntent(context, notification)
    }

    override fun buildNotification(
        message: Map<String, String>,
    ): Notification {
        val notification = super.buildNotification(message).apply { sound = soundUri }
        return applyChatIntent(context, notification)
    }
}
