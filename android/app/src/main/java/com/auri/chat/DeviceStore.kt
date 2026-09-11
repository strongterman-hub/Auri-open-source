package com.auri.chat

import android.content.Context

class DeviceStore(context: Context) {
    private val preferences =
        context.getSharedPreferences("auri_device", Context.MODE_PRIVATE)

    fun getRegistrationId(): String? = preferences.getString(KEY_REGISTRATION_ID, null)

    fun saveRegistrationId(registrationId: String): Boolean {
        if (registrationId.isBlank()) return false
        val changed = getRegistrationId() != registrationId
        preferences.edit().putString(KEY_REGISTRATION_ID, registrationId).apply()
        return changed
    }

    companion object {
        private const val KEY_REGISTRATION_ID = "jpush_registration_id"
    }
}
