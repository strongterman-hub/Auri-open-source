package com.auri.chat

import android.content.Context

class AuthStore(context: Context) {
    private val preferences = context.getSharedPreferences("auri_auth", Context.MODE_PRIVATE)

    fun getToken(): String? = preferences.getString(KEY_TOKEN, null)

    fun getEmail(): String? = preferences.getString(KEY_EMAIL, null)

    fun save(token: String, email: String) {
        preferences.edit()
            .putString(KEY_TOKEN, token)
            .putString(KEY_EMAIL, email)
            .apply()
    }

    fun clear() {
        preferences.edit().clear().apply()
    }

    fun getXiaomiBound(): Boolean = preferences.getBoolean(KEY_XIAOMI_BOUND, false)

    fun getXiaomiLastSyncAt(): Long? =
        preferences.getLong(KEY_XIAOMI_LAST_SYNC_AT, 0L).takeIf { it > 0 }

    fun getDualSleepScoreVisible(): Boolean =
        preferences.getBoolean(KEY_DUAL_SLEEP_SCORE_VISIBLE, false)

    fun saveDualSleepScoreVisible(visible: Boolean) {
        preferences.edit().putBoolean(KEY_DUAL_SLEEP_SCORE_VISIBLE, visible).apply()
    }

    fun getRegisteredDeviceToken(): String? =
        preferences.getString(KEY_REGISTERED_DEVICE_TOKEN, null)

    fun saveRegisteredDeviceToken(registrationId: String) {
        preferences.edit().putString(KEY_REGISTERED_DEVICE_TOKEN, registrationId).apply()
    }

    fun saveXiaomiStatus(bound: Boolean, lastSyncAt: Long?) {
        val editor = preferences.edit().putBoolean(KEY_XIAOMI_BOUND, bound)
        if (lastSyncAt != null && lastSyncAt > 0) {
            editor.putLong(KEY_XIAOMI_LAST_SYNC_AT, lastSyncAt)
        } else {
            editor.remove(KEY_XIAOMI_LAST_SYNC_AT)
        }
        editor.apply()
    }

    companion object {
        private const val KEY_TOKEN = "token"
        private const val KEY_EMAIL = "email"
        private const val KEY_XIAOMI_BOUND = "xiaomi_bound"
        private const val KEY_XIAOMI_LAST_SYNC_AT = "xiaomi_last_sync_at"
        private const val KEY_DUAL_SLEEP_SCORE_VISIBLE = "dual_sleep_score_visible"
        private const val KEY_REGISTERED_DEVICE_TOKEN = "registered_device_token"
    }
}
