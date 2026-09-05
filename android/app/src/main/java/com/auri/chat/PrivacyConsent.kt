package com.auri.chat

import android.content.Context
import cn.jpush.android.api.JPushInterface

object PrivacyConsent {
    private const val PREFS = "auri_privacy"
    private const val KEY_AGREED = "agreed"

    fun isAgreed(context: Context): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getBoolean(KEY_AGREED, false)

    fun agree(context: Context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_AGREED, true)
            .apply()
        // JPush 合规要求：用户同意隐私政策后再初始化推送 SDK。
        if (BuildConfig.JPUSH_ENABLED) {
            JPushInterface.init(context)
            applyPushNotificationSound(context)
        }
    }
}
