package com.auri.chat

import android.content.Context
import cn.jpush.android.api.JPushInterface
import cn.jiguang.api.utils.JCollectionAuth

object PrivacyConsent {
    private const val PREFS = "auri_privacy"
    private const val KEY_AGREED = "agreed"
    private const val KEY_VERSION = "policy_version"
    private const val KEY_CHANGED_AT = "changed_at"
    const val POLICY_VERSION = "2026-09-09"

    fun isAgreed(context: Context): Boolean {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        return prefs.getBoolean(KEY_AGREED, false) &&
            prefs.getString(KEY_VERSION, null) == POLICY_VERSION
    }

    fun agree(context: Context) = setAgreed(context, true)

    fun setAgreed(context: Context, agreed: Boolean) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_AGREED, agreed)
            .putString(KEY_VERSION, POLICY_VERSION)
            .putLong(KEY_CHANGED_AT, System.currentTimeMillis())
            .apply()
        applySdkConsent(context, agreed)
    }

    fun applySdkConsent(context: Context, agreed: Boolean = isAgreed(context)) {
        if (!BuildConfig.JPUSH_ENABLED) return
        JCollectionAuth.setAuth(context, agreed)
        if (agreed) {
            JPushInterface.setKeepLongConnInBackground(context, true)
            JPushInterface.init(context)
            JPushInterface.resumePush(context)
            applyPushNotificationSound(context)
        } else {
            JPushInterface.stopPush(context)
            AuriKeepAliveController.stop(context)
        }
    }
}
