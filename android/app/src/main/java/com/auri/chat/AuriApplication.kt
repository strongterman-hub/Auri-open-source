package com.auri.chat

import android.app.Application
import cn.jpush.android.api.JPushInterface

class AuriApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        JPushInterface.setDebugMode(BuildConfig.DEBUG)
        PrivacyConsent.applySdkConsent(this)
    }
}
