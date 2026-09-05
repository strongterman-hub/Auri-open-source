package com.auri.chat

import android.content.Context

class SessionStore(context: Context) {
    private val preferences = context.getSharedPreferences("auri", Context.MODE_PRIVATE)

    fun getSessionId(userKey: String): String? = preferences.getString(key(userKey), null)

    fun saveSessionId(userKey: String, sessionId: String) {
        preferences.edit().putString(key(userKey), sessionId).apply()
    }

    fun clearSession(userKey: String) {
        preferences.edit().remove(key(userKey)).apply()
    }

    private fun key(userKey: String) = "session_id_$userKey"
}
