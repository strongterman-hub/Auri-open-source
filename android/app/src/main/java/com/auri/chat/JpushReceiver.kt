package com.auri.chat

import android.content.Context
import android.content.Intent
import cn.jpush.android.api.CustomMessage
import cn.jpush.android.api.JPushInterface
import cn.jpush.android.api.NotificationMessage
import cn.jpush.android.service.JPushMessageReceiver
import org.json.JSONObject

class JpushReceiver : JPushMessageReceiver() {
    override fun onRegister(context: Context, registrationId: String) {
        super.onRegister(context, registrationId)
        if (DeviceStore(context).saveRegistrationId(registrationId)) {
            AuthStore(context).clearRegisteredDeviceRegistration()
        }
    }

    override fun onNotifyMessageOpened(context: Context, message: NotificationMessage) {
        super.onNotifyMessageOpened(context, message)
        JPushInterface.clearNotificationById(context, message.notificationId)
        // JPush 会用它内部的 PushActivity 消费通知点击，自定义 builder 里设置的
        // contentIntent 并不一定生效，因此这里显式回到聊天页，避免“点了通知只消失、不进聊天页”。
        context.startActivity(openChatIntent(context))
    }

    override fun onMessage(context: Context, message: CustomMessage) {
        super.onMessage(context, message)
        val type = runCatching {
            JSONObject(message.extra ?: "{}").optString("type")
        }.getOrNull()
        if (type == "force_update_check") {
            context.startActivity(
                Intent(context, MainActivity::class.java).apply {
                    action = ACTION_CHECK_UPDATE
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                },
            )
        }
    }
}
