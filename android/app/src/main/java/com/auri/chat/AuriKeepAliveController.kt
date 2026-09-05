package com.auri.chat

import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat

/** Starts the direct-distribution keep-alive service without linking it into store builds. */
object AuriKeepAliveController {
    fun start(context: Context) {
        if (!BuildConfig.KEEP_ALIVE_ENABLED || !BuildConfig.JPUSH_ENABLED) return
        val intent = Intent().setClassName(
            context.packageName,
            "com.auri.chat.AuriKeepAliveService",
        )
        ContextCompat.startForegroundService(context, intent)
    }
}
