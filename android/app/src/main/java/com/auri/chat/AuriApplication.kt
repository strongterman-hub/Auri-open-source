package com.auri.chat

import android.app.Application
import cn.jpush.android.api.JPushInterface

class AuriApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        JPushInterface.setDebugMode(BuildConfig.DEBUG)
        if (BuildConfig.JPUSH_ENABLED && PrivacyConsent.isAgreed(this)) {
            JPushInterface.init(this)
            applyPushNotificationSound(this)
        }
    }
}
